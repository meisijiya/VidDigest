"""AI 视频总结相关 API 路由"""

import asyncio
import json
from collections.abc import AsyncIterable

from fastapi import APIRouter, Depends, HTTPException
from fastapi.sse import ServerSentEvent, EventSourceResponse
from pydantic import BaseModel

from auth import get_optional_user
from database import (
    DAILY_CHAT_LIMIT,
    DAILY_PARSE_LIMIT,
    check_quota_kind,
    consume_quota,
    refund_quota,
)

router = APIRouter(prefix="/api", tags=["AI 总结"])

#: 额度种类 → 中文名，只用于「今日XX次数已用完」这类提示文案。
_QUOTA_LABELS = {"parse": "解析", "chat": "追问"}


@router.get("/quota")
async def get_quota(user: dict | None = Depends(get_optional_user)):
    """当前额度（只读，不消耗）。

    解析额度与对话额度是两个独立计数器，各自在次日重置。
    """
    if not user:
        return {"logged_in": False, "unlimited": False, "remaining": None,
                "limit": DAILY_PARSE_LIMIT, "parse": None, "chat": None}
    return {"logged_in": True, **_quota_payload(user["id"])}


def _quota_payload(user_id: int, primary: str = "parse") -> dict:
    """两个额度各自的 (allowed, remaining, limit)，供前端分别展示。

    顶层 remaining/limit/unlimited 是留给旧前端的兼容别名，必须跟随
    `primary`——即「这次事件刚动的是哪类额度」。若追问事件里还报 parse
    的数字，前端的降级分支会把刚扣掉的对话额度显示成解析的余量。
    """
    payload = {}
    for kind, limit in (("parse", DAILY_PARSE_LIMIT), ("chat", DAILY_CHAT_LIMIT)):
        allowed, remaining = check_quota_kind(user_id, kind)
        payload[kind] = {
            "allowed": allowed,
            "remaining": (0 if not allowed else remaining),
            "limit": limit,
        }
    head = payload[primary]
    return {
        **payload,
        "unlimited": head["remaining"] == -1,
        "remaining": head["remaining"],
        "limit": head["limit"],
    }


class SummarizeRequest(BaseModel):
    url: str
    language: str = "zh"


class ChatRequest(BaseModel):
    url: str
    question: str
    subtitle_text: str = ""


def _check_quota_permission(user: dict | None, kind: str):
    """检查某类额度权限（只判定，不扣）。"""
    limit = DAILY_PARSE_LIMIT if kind == "parse" else DAILY_CHAT_LIMIT
    label = _QUOTA_LABELS[kind]

    if not user:
        return False, 0, "请先登录后使用 AI 功能"

    allowed, remaining = check_quota_kind(user["id"], kind)
    if not allowed:
        return False, 0, f"今日{label}次数已用完（每日 {limit} 次），明日 0 点重置"

    return True, remaining, None


def _get_summarizer():
    """延迟初始化 VideoSummarizer"""
    from summarizer import VideoSummarizer
    if not hasattr(_get_summarizer, "_instance"):
        try:
            _get_summarizer._instance = VideoSummarizer()
        except ValueError as e:
            raise HTTPException(status_code=500, detail=str(e))
    return _get_summarizer._instance


def _get_extractor():
    """延迟初始化 SubtitleExtractor"""
    from summarizer import SubtitleExtractor
    if not hasattr(_get_extractor, "_instance"):
        _get_extractor._instance = SubtitleExtractor()
    return _get_extractor._instance


async def _run_in_thread(func, *args):
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, func, *args)


# ── 流式总结端点 ────────────────────────────────────────

@router.post("/summarize", response_class=EventSourceResponse)
async def summarize_video(
    req: SummarizeRequest,
    user: dict | None = Depends(get_optional_user),
) -> AsyncIterable[ServerSentEvent]:
    """
    AI 视频总结（SSE 流式）
    事件顺序：subtitle → quota → summary(流式token) → mindmap → done
    """
    allowed, remaining, message = _check_quota_permission(user, "parse")
    if not allowed:
        yield ServerSentEvent(
            raw_data=json.dumps({
                "message": message,
                "need_login": user is None,
                "need_vip": user is not None,
            }, ensure_ascii=False),
            event="error",
        )
        return

    # 额度是否已扣。扣了之后模型调用失败要还回去。
    quota_spent = False
    try:
        extractor = _get_extractor()
        subtitle_data = await _run_in_thread(extractor.extract, req.url)

        yield ServerSentEvent(
            raw_data=json.dumps(subtitle_data, ensure_ascii=False),
            event="subtitle",
        )

        if not subtitle_data["has_subtitle"]:
            yield ServerSentEvent(
                raw_data=json.dumps({"message": "该视频没有可用的字幕，无法生成总结"}, ensure_ascii=False),
                event="error",
            )
            return

        full_text = subtitle_data["full_text"]

        # 真正要调用 AI 了，此刻才扣额度（字幕提取失败不扣）
        consume_quota(user["id"], "parse")
        quota_spent = True

        # 额度尽早下发，前端在流式开始前就能显示剩余次数。
        # 顶层字段由 _quota_payload 统一产出——在这里手写会被末尾展开的
        # payload 覆盖掉，写了也不生效。
        yield ServerSentEvent(
            raw_data=json.dumps(_quota_payload(user["id"], "parse"), ensure_ascii=False),
            event="quota",
        )

        # 流式生成总结摘要
        summarizer = _get_summarizer()
        for token in summarizer.summarize_stream(full_text, req.language):
            yield ServerSentEvent(
                raw_data=json.dumps(token, ensure_ascii=False),
                event="summary",
            )

        # 生成思维导图（非流式）
        mindmap_md = await _run_in_thread(summarizer.generate_mindmap, full_text, req.language)
        yield ServerSentEvent(
            raw_data=json.dumps({"markdown": mindmap_md}, ensure_ascii=False),
            event="mindmap",
        )

        yield ServerSentEvent(raw_data="[DONE]", event="done")

    except HTTPException:
        if quota_spent:
            refund_quota(user["id"], "parse")
        raise
    except Exception as e:
        # 模型调用失败不该白扣额度。扣减与调用之间没有事务，
        # 这里是把已扣的那一次还回去——回滚本身不会把计数压到负数。
        if quota_spent:
            refund_quota(user["id"], "parse")
        yield ServerSentEvent(
            raw_data=json.dumps({"message": f"总结失败: {str(e)}"}, ensure_ascii=False),
            event="error",
        )


# ── AI 问答端点 ──────────────────────────────────────────

@router.post("/chat", response_class=EventSourceResponse)
async def chat_with_video(
    req: ChatRequest,
    user: dict | None = Depends(get_optional_user),
) -> AsyncIterable[ServerSentEvent]:
    """AI 视频问答（SSE 流式）。消耗对话额度，与解析额度互不影响。"""
    allowed, _remaining, message = _check_quota_permission(user, "chat")
    if not allowed:
        yield ServerSentEvent(
            raw_data=json.dumps({
                "message": message,
                "need_login": user is None,
                "need_vip": user is not None,
            }, ensure_ascii=False),
            event="error",
        )
        return

    quota_spent = False
    try:
        if not req.subtitle_text.strip():
            extractor = _get_extractor()
            subtitle_data = await _run_in_thread(extractor.extract, req.url)
            if not subtitle_data["has_subtitle"]:
                yield ServerSentEvent(
                    raw_data=json.dumps({"message": "该视频没有可用的字幕，无法回答问题"}, ensure_ascii=False),
                    event="error",
                )
                return
            subtitle_text = subtitle_data["full_text"]
        else:
            subtitle_text = req.subtitle_text

        # 即将调用 AI，此刻才扣额度（无字幕 / 未登录 / 超额都不扣）
        consume_quota(user["id"], "chat")
        quota_spent = True

        # 问答也显式回报额度，和总结走同一套展示。顶层数字跟 chat 走——
        # 旧前端只认顶层字段时，看到的必须是刚被扣掉的那个计数器，
        # 否则追问一次却显示解析余量没动。
        yield ServerSentEvent(
            raw_data=json.dumps(_quota_payload(user["id"], "chat"), ensure_ascii=False),
            event="quota",
        )

        summarizer = _get_summarizer()
        for token in summarizer.chat_stream(subtitle_text, req.question):
            yield ServerSentEvent(
                raw_data=json.dumps(token, ensure_ascii=False),
                event="answer",
            )

        yield ServerSentEvent(raw_data="[DONE]", event="done")

    except HTTPException:
        if quota_spent:
            refund_quota(user["id"], "chat")
        raise
    except Exception as e:
        if quota_spent:
            refund_quota(user["id"], "chat")
        yield ServerSentEvent(
            raw_data=json.dumps({"message": f"回答失败: {str(e)}"}, ensure_ascii=False),
            event="error",
        )
