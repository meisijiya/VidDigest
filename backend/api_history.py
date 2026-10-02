"""解析历史记录 API 路由"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from auth import get_current_user
from database import (
    MAX_PARSE_HISTORY_PER_USER,
    append_chat_history,
    delete_parse_history,
    get_community_video_by_url,
    get_parse_histories,
    get_parse_history_detail,
    upsert_parse_history,
)

router = APIRouter(prefix="/api/history", tags=["解析历史"])


class SaveHistoryRequest(BaseModel):
    url: str
    video_title: str = ""
    video_data: dict | None = None
    summary_md: str = ""
    mindmap_md: str = ""
    subtitle_data: dict | None = None


class SaveChatRequest(BaseModel):
    url: str
    question: str
    answer: str


@router.get("")
async def list_history(user: dict = Depends(get_current_user)):
    """获取当前用户最近 30 条解析历史"""
    return {"items": get_parse_histories(user["id"], MAX_PARSE_HISTORY_PER_USER)}


@router.post("/save")
async def save_history(req: SaveHistoryRequest, user: dict = Depends(get_current_user)):
    """保存/更新解析历史（同一视频去重，每用户滚动保留 30 条）"""
    history_id = upsert_parse_history(
        user_id=user["id"],
        video_url=req.url,
        video_title=req.video_title,
        video_data=req.video_data,
        summary_md=req.summary_md,
        mindmap_md=req.mindmap_md,
        subtitle_data=req.subtitle_data,
    )
    return {"success": True, "id": history_id}


@router.post("/chat")
async def save_chat(req: SaveChatRequest, user: dict = Depends(get_current_user)):
    """向指定视频的解析历史追加一条 AI 问答"""
    append_chat_history(user["id"], req.url, req.question, req.answer)
    return {"success": True}


@router.get("/by-url")
async def history_by_url(url: str, user: dict = Depends(get_current_user)):
    """按视频 URL 问「社区里有没有这一份」，返回社区视频表的那一行。

    注意：必须注册在 /{history_id} 之前，否则 "by-url" 会被当作 int 路径参数解析失败。

    为什么查 videos 而不是 parse_history（工单 #7 顺带修，原 MEDIUM-2）：
    这里是前端**决定要不要重新解析**的地方，也是详情展示路径的入口。
    查个人历史表的话，有过个人记录的用户会在这里命中，然后前端直接渲染
    自己那份 summary_md——于是同一个链接在社区里明明有唯一一份总结，
    这个人看到的却是另一份，且此后每次进来都绕过社区数据源。
    与「无论谁先解析，看到的都是同一份」直接抵触。

    现在它只回答「在不在」，**不返回任何内容**：
    返回的是 community card 白名单投影，与未登录访客看到的完全同形。
    总结 / 字幕由 /api/summarize 从社区视频表回放，那是唯一的内容出口。

    parse_history 本身保持原样（ADR 0001）：它仍是「我解析过什么」的
    个人访问记录，滚动保留 30 条。
    """
    return {"item": get_community_video_by_url(url)}


@router.get("/{history_id}")
async def history_detail(history_id: int, user: dict = Depends(get_current_user)):
    """获取单条历史记录完整内容"""
    item = get_parse_history_detail(user["id"], history_id)
    if not item:
        raise HTTPException(status_code=404, detail="记录不存在")
    return item


@router.delete("/{history_id}")
async def remove_history(history_id: int, user: dict = Depends(get_current_user)):
    """删除单条历史记录"""
    if not delete_parse_history(user["id"], history_id):
        raise HTTPException(status_code=404, detail="记录不存在")
    return {"success": True}
