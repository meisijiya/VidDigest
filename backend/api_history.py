"""解析历史记录 API 路由"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from auth import get_current_user
from database import (
    MAX_PARSE_HISTORY_PER_USER,
    delete_parse_history,
    get_chat_session,
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


@router.get("/chat")
async def chat_session(url: str, user: dict = Depends(get_current_user)):
    """当前用户与某个视频的追问会话，只有他自己读得到。

    为什么需要这个读出口：追问记录按 (user_id, video_url) 存在 chat_messages，
    而**不依赖这个用户有没有解析过这个视频**。此前的唯一读路径
    /api/history/{id} 由 parse_history 行驱动，B 追问 A 解析的视频时
    他并没有那一行——「B 读不到 A」因此是假通过，实际上是谁的都读不到。

    响应沿用 get_chat_session 的 [{question, answer}]：前端 chatHistoryList
    吃的就是这个形状，详情接口给的也是它。不新造形状，是为了老数据回填
    那一行和这个端点在同一处汇合。

    注册位置同 by-url：**必须在 /{history_id} 之前**，否则 "chat" 会被
    当成 int 路径参数解析，直接 422。
    """
    return {"chat_history": get_chat_session(user["id"], url)}


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
