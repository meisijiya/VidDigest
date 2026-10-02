"""AI 视频总结相关 API 路由"""

import asyncio
import json
import logging
from collections.abc import AsyncIterable

from fastapi import APIRouter, Depends, HTTPException
from fastapi.sse import ServerSentEvent, EventSourceResponse
from pydantic import BaseModel

from auth import get_optional_user
from database import (
    check_quota_kind,
    consume_quota,
    quota_limit,
    refund_quota,
)
from tags import validate_tags

logger = logging.getLogger("api_summarize")

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
                "limit": quota_limit("parse"), "parse": None, "chat": None}
    return {"logged_in": True, **_quota_payload(user["id"])}


def _quota_payload(user_id: int, primary: str = "parse") -> dict:
    """两个额度各自的 (allowed, remaining, limit)，供前端分别展示。

    顶层 remaining/limit/unlimited 是留给旧前端的兼容别名，必须跟随
    `primary`——即「这次事件刚动的是哪类额度」。若追问事件里还报 parse
    的数字，前端的降级分支会把刚扣掉的对话额度显示成解析的余量。

    limit 走 `quota_limit` 而不是模块常量：remaining 由数据层按调用时的
    模块值算，limit 若在 import 期冻结成另一份，同一份 payload 就会自相矛盾。
    """
    payload = {}
    for kind in ("parse", "chat"):
        allowed, remaining = check_quota_kind(user_id, kind)
        payload[kind] = {
            "allowed": allowed,
            "remaining": (0 if not allowed else remaining),
            "limit": quota_limit(kind),
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
    limit = quota_limit(kind)
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


def _subtitle_failure(subtitle_data: dict) -> tuple[str, str, str]:
    """把「为什么拿不到字幕」翻成一句用户看得懂的话。

    返回 (前半句, fail_reason, asr_fail_reason)。后半句留给调用方拼——同一个
    原因对「无法生成总结」和「无法回答问题」要接不同的尾巴。

    延迟导入 summarizer 与本文件其它地方一致（openai / yt_dlp 都很重），
    不为了几个常量在模块顶部把它拖进来。
    """
    from summarizer import (
        FAIL_ASR_FAILED,
        FAIL_ASR_NOT_CONFIGURED,
        FAIL_FETCH_FAILED,
        FAIL_NO_TRACK,
    )

    # .get() 不是防御性过度：这两个键是新加的，任何一条仍返回旧形状的
    # 路径（测试里的 StubExtractor、第三方补丁）都不该把这里变成 KeyError。
    reason = subtitle_data.get("fail_reason", "")
    asr_reason = subtitle_data.get("asr_fail_reason", "")

    if reason == FAIL_FETCH_FAILED:
        head = "平台提供了字幕但没能取下来"
    elif reason == FAIL_NO_TRACK:
        head = "该视频没有字幕"
    else:
        head = "该视频没有可用的字幕"

    if asr_reason == FAIL_ASR_NOT_CONFIGURED:
        head += "，且服务端未配置语音识别"
    elif asr_reason == FAIL_ASR_FAILED:
        head += "，服务端用语音识别补字幕也没成功"

    return head, reason, asr_reason


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
    事件顺序：subtitle → quota → summary(流式token) → mindmap → tags → done

    一次解析产出三件事（总结 / 思维导图 / 标签），共用同一份字幕上下文，
    也只调一次模型、只扣一次额度。
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

    # 额度是否已扣。扣了之后没走完流程就要还回去。
    quota_spent = False
    try:
        extractor = _get_extractor()
        subtitle_data = await _run_in_thread(extractor.extract, req.url)

        yield ServerSentEvent(
            raw_data=json.dumps(subtitle_data, ensure_ascii=False),
            event="subtitle",
        )

        if not subtitle_data["has_subtitle"]:
            head, reason, asr_reason = _subtitle_failure(subtitle_data)
            yield ServerSentEvent(
                raw_data=json.dumps({
                    "message": f"{head}，无法生成总结",
                    # 机器可读的原因：前端将来要分开提示时不必再猜
                    "reason": reason,
                    "asr_reason": asr_reason,
                }, ensure_ascii=False),
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

        # 一次模型调用产出三件事：总结逐 token 下发（打字机效果），
        # 哨兵之后的 JSON 在流末尾一次性解析。模型只被调一次，额度也只扣这一次。
        summarizer = _get_summarizer()
        for kind, payload in summarizer.summarize_full_stream(full_text, req.language):
            if kind == "summary":
                yield ServerSentEvent(
                    raw_data=json.dumps(payload, ensure_ascii=False),
                    event="summary",
                )
            elif kind == "mindmap":
                yield ServerSentEvent(
                    raw_data=json.dumps({"markdown": payload}, ensure_ascii=False),
                    event="mindmap",
                )
            elif kind == "tags":
                # 词表是产品规则，模型说了不算：校验在这里做，
                # 线上只可能拿到词表内、去重、非空的数组。
                accepted, rejected = validate_tags(payload)
                if rejected:
                    logger.info("丢弃词表外的标签：%s", rejected)
                yield ServerSentEvent(
                    raw_data=json.dumps(accepted, ensure_ascii=False),
                    event="tags",
                )
            else:
                logger.warning("未知的产出类型 %r，本次解析忽略它", kind)

        # 三项都已产出，扣费就此结清：此后即便客户端断流也不回滚。
        quota_spent = False
        yield ServerSentEvent(raw_data="[DONE]", event="done")

    except HTTPException:
        # 交给下面的 finally 回滚，然后原样抛出——不能降级成 SSE 错误事件。
        raise
    except Exception as e:
        # 模型调用失败不该白扣额度。扣减与调用之间没有事务，
        # 这里是把已扣的那一次还回去——回滚本身不会把计数压到负数。
        yield ServerSentEvent(
            raw_data=json.dumps({"message": f"总结失败: {str(e)}"}, ensure_ascii=False),
            event="error",
        )
    finally:
        # 唯一回滚点：except 分支和「客户端中途断开」共用它，不会重复退款。
        # 断流时抛的是 GeneratorExit / CancelledError，两者都继承 BaseException
        # 而非 Exception，上面的 except 抓不到——实测额度就停在扣减后的值。
        # 无论如何都原样抛出，KeyboardInterrupt / SystemExit 不会被吞掉。
        if quota_spent:
            refund_quota(user["id"], "parse")


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
                head, reason, asr_reason = _subtitle_failure(subtitle_data)
                yield ServerSentEvent(
                    raw_data=json.dumps({
                        "message": f"{head}，无法回答问题",
                        "reason": reason,
                        "asr_reason": asr_reason,
                    }, ensure_ascii=False),
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

        # 回答已完整产出，扣费就此结清：此后即便客户端断流也不回滚。
        quota_spent = False
        yield ServerSentEvent(raw_data="[DONE]", event="done")

    except HTTPException:
        # 交给下面的 finally 回滚，然后原样抛出——不能降级成 SSE 错误事件。
        raise
    except Exception as e:
        yield ServerSentEvent(
            raw_data=json.dumps({"message": f"回答失败: {str(e)}"}, ensure_ascii=False),
            event="error",
        )
    finally:
        # 同 summarize_video：唯一回滚点，断流抛的 GeneratorExit /
        # CancelledError 不在 Exception 族里，但扣了的额度必须还回去。
        if quota_spent:
            refund_quota(user["id"], "chat")
