"""解析历史记录 API 路由"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from auth import get_current_user
from database import (
    HISTORY_PAGE_SIZE_DEFAULT,
    MAX_PARSE_HISTORY_PER_USER,
    clear_parse_history,
    delete_parse_history,
    get_chat_session,
    get_parse_history_detail,
    get_parse_history_favorite,
    list_parse_histories,
    list_parse_history_facets,
    set_parse_history_favorite,
    upsert_parse_history,
)

router = APIRouter(prefix="/api/history", tags=["解析历史"])


class FavoriteRequest(BaseModel):
    is_favorite: bool


class SaveHistoryRequest(BaseModel):
    url: str
    video_title: str = ""
    video_data: dict | None = None
    summary_md: str = ""
    mindmap_md: str = ""
    subtitle_data: dict | None = None


@router.get("")
async def list_history(q: str = "", tag: str = "", favorite: bool = False,
                      ai: str = "", page: int = 1,
                      page_size: int = HISTORY_PAGE_SIZE_DEFAULT,
                      user: dict = Depends(get_current_user)):
    """当前用户的解析历史：分页 + 关键词 / 标签 / 仅收藏 / AI 状态。

    这四组条件可任意组合。前端是四个独立控件，组合语义必须落在
    服务端 —— 每加一个控件就在前端过滤一次，很快会出现「界面显示
    的是 200 条、实际总数是 1000 条」这种对不上的情况。
    """
    return list_parse_histories(
        user["id"], q=q, tag=tag, favorite=favorite, ai=ai,
        page=page, page_size=page_size,
    )


@router.post("/save")
async def save_history(req: SaveHistoryRequest, user: dict = Depends(get_current_user)):
    """保存/更新解析历史（同一视频去重，每用户滚动保留 1000 条）"""
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


@router.get("/facets")
async def history_facets(user: dict = Depends(get_current_user)):
    """历史页标签筛选的选项。

    **注册位置必须在 ``/{history_id}`` 之前** —— 否则 "facets" 会被当成
    int 路径参数解析，直接 422。这与 /chat 是同一条纪律。
    """
    return {"items": list_parse_history_facets(user["id"])}


@router.patch("/{history_id}/favorite")
async def toggle_favorite(history_id: int, req: FavoriteRequest,
                          user: dict = Depends(get_current_user)):
    """收藏 / 取消收藏。前端是**幂等**设置而不是 toggle 翻转。"""
    if not set_parse_history_favorite(user["id"], history_id, req.is_favorite):
        raise HTTPException(status_code=404, detail="记录不存在")
    return {"success": True, "id": history_id, "is_favorite": req.is_favorite}


@router.delete("")
async def clear_history(force: bool = False,
                        user: dict = Depends(get_current_user)):
    """清空历史。默认跳过收藏；要连收藏一起删必须显式 force=true。

    为什么不是循环调用单条删除：上限 1000 条时那是 1000 个请求，
    中途失败还会留下一半删一半没删的列表。
    """
    deleted = clear_parse_history(user["id"], keep_favorites=not force)
    return {"success": True, "deleted": deleted,
            "kept_favorites": deleted == 0 or not force}


@router.get("/{history_id}")
async def history_detail(history_id: int, user: dict = Depends(get_current_user)):
    """获取单条历史记录完整内容"""
    item = get_parse_history_detail(user["id"], history_id)
    if not item:
        raise HTTPException(status_code=404, detail="记录不存在")
    return item


@router.delete("/{history_id}")
async def remove_history(history_id: int, force: bool = False,
                          user: dict = Depends(get_current_user)):
    """删除单条历史记录。收藏项需要 force=true 才删得掉。

    **这是服务端的一道保险，不是前端的提示。** 双重保险若做成
    「前端弹两次 confirm」，那么任何一个忘了带 confirm 的新入口
    （脚本、curl、以后加的批量操作）都能直接删掉收藏 —— 用户标了
    星的东西，靠界面自觉保护，等于没保护。

    409 而不是静默删除：调用方据此弹出第二道确认，语义也清楚。
    """
    favorite = get_parse_history_favorite(user["id"], history_id)
    if favorite is None:
        raise HTTPException(status_code=404, detail="记录不存在")
    if favorite and not force:
        raise HTTPException(status_code=409, detail={
            "code": "favorited",
            "message": "这条解析已收藏，删除需要再确认一次。",
        })
    delete_parse_history(user["id"], history_id)
    return {"success": True, "forced": bool(favorite and force)}
