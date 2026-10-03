"""社区浏览与搜索 API 路由（工单 #7）

可见性是这个模块唯一的设计重点。票面把可见性写死成一张表：

    资源            未登录        已登录
    社区列表        可见          可见
    标签筛选/翻页   可用          可用
    视频详情        需登录(401)   可见
    关键词搜索      需登录(401)   可用
    追问            需登录(401)   可用

两处约定贯穿全文件：

1. **鉴权只在依赖层发生。** 路由函数体内没有任何 `if not user` 式的判断——
   手工判断迟早会漏掉某条早退路径（参数校验失败、异常、提前 return），
   而漏掉的那条就是一次真实的未授权内容泄漏。需要登录的端点一律
   `Depends(get_current_user)`，未登录由依赖抛 401，不返回空、不返回部分。

2. **对外响应按字段白名单投影。** 未登录访客的列表项里不会出现字幕、总结、
   思维导图——不是置空、不是裁剪，是**这些键根本不会被构造出来**。
   白名单在 database.COMMUNITY_CARD_FIELDS，写法与理由写在那里。

列表对未登录与已登录**返回同一种形状**：不是「已登录多给几个字段」，
而是列表这个入口无论谁来看都只有封面、标题、标签。内容只在详情里出现。
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from auth import get_current_user
from database import (
    COMMUNITY_PAGE_SIZE_DEFAULT,
    COMMUNITY_PAGE_SIZE_MAX,
    VIDEO_STATUS_READY,
    get_community_video,
    get_video_by_url,
    list_community_videos,
    publish_video_card,
    search_community_videos,
)

router = APIRouter(prefix="/api/community", tags=["社区"])


class PublishCardRequest(BaseModel):
    """解析成功后回填社区卡片的展示信息。

    只有标题与封面两样：它们是平台元数据，与谁解析无关。刻意不接收
    summary / mindmap / subtitle / tags——那些是社区内容，只能由
    complete_video 写（工单 #6 的「ready 行谁都不能改写」），从别处
    再开一条写路径就等于把那条承诺从后门捅穿。
    """
    url: str
    video_title: str = ""
    cover_url: str = ""


# ── 公开：社区列表 ────────────────────────────────────────────
#
# 分页上下限刻意**不**写成 Query(ge=..., le=...)：那样这套规则会同时存在于
# 路由声明与 database._clamp_page 两处，改一处忘一处就会出现「接口收紧了
# 数据层没收紧」或反之的缝。统一由数据层那一个函数归一，路由只负责透传。

@router.get("/videos")
async def community_videos(
    page: int = Query(1, description="页码，从 1 开始；小于 1 归一到 1"),
    page_size: int = Query(COMMUNITY_PAGE_SIZE_DEFAULT,
                           description=f"每页条数，上限 {COMMUNITY_PAGE_SIZE_MAX}"),
    tag: str = Query("", description="按标签精确筛选；留空为不过滤"),
):
    """社区列表。**任何人可访问，含未登录访客。**

    刻意不挂任何鉴权依赖：列表对登录与否返回**完全相同**的形状。
    挂上 get_optional_user 再按登录状态分支，等于给「多给几个字段」留一个
    位置；不挂依赖则这个分支在结构上就不存在。

    翻页与标签筛选同属列表本身的用法，因此同样对访客开放——它们是读公开
    数据的不同方式，不是特权。
    """
    return list_community_videos(page=page, page_size=page_size, tag=tag)


# ── 需登录：这条视频社区里有没有 ───────────────────────────
#
# ⚠️ 必须声明在 /videos/{video_id} **之前**。FastAPI 按声明顺序匹配，
# "{video_id}" 声明在前的话，"by-url" 会被它吃掉并按 int 解析 → 422，
# 症状是「这个端点怎么调都是参数不合法」，与鉴权、与数据都无关。
@router.get("/videos/by-url")
async def community_video_by_url(
    url: str = Query(..., description="视频链接，需与解析时规范化后完全一致"),
    user: dict = Depends(get_current_user),
):
    """社区视频表里有没有这一份。只答存在性与写权限，不回内容。

    存在的理由是「按谁在读」而不是「按数据在哪张表」：前端要靠它决定
    自动拉取还是等用户点「开始 AI 解析」，而用户点开一条**别人**解析的
    视频时，他自己的解析历史里当然没有这一条——拿个人历史去判，
    陌生人永远看不到复用提示，「重新解析」按钮也就永远不出现。
    """
    row = get_video_by_url(url)
    ready = row is not None and row.get("status") == VIDEO_STATUS_READY
    return {
        "exists": ready,
        # 写权限与内容在两处：这里给「能不能改」，内容由 /api/summarize 的
        # 复用回放给。两处同源（都读 videos.parsed_by），不各算一次。
        "can_regenerate": ready and row.get("parsed_by") == user["id"],
    }


# ── 需登录：详情 ──────────────────────────────────────────────

@router.get("/videos/{video_id}")
async def community_video_detail(
    video_id: int,
    user: dict = Depends(get_current_user),
):
    """社区视频详情：总结、思维导图、标签、字幕全文。

    未登录由 get_current_user 抛 401。**不是返回空、也不是返回半份**：
    「看一半」会让访客以为社区里没有内容，而真实原因是他们没登录。
    依赖层解决掉这件事，路由体内因此没有一行鉴权代码。
    """
    item = get_community_video(video_id)
    if item is None:
        # 占位（pending）与不存在都归到这里：pending 是别人正在解析的
        # 空壳，不是社区内容，不该被当作「有结果但没权限」来暗示。
        raise HTTPException(status_code=404, detail="视频不存在或尚未解析完成")
    return item


# ── 需登录：搜索 ──────────────────────────────────────────────

@router.get("/search")
async def community_search(
    q: str = Query("", description="视频名称关键词，或完整视频链接"),
    page: int = Query(1, description="页码，从 1 开始；小于 1 归一到 1"),
    page_size: int = Query(COMMUNITY_PAGE_SIZE_DEFAULT,
                           description=f"每页条数，上限 {COMMUNITY_PAGE_SIZE_MAX}"),
    tag: str = Query("", description="按标签精确筛选；可与 q 取交集"),
    user: dict = Depends(get_current_user),
):
    """社区搜索：视频名称关键词 / 视频链接精确定位 / 标签。

    未登录 401。检索能力限定给登录用户，理由与配额外的「白看」不同：
    列表是浏览，搜索是主动查找社区内容。
    """
    return search_community_videos(q=q, page=page, page_size=page_size, tag=tag)


# ── 需登录：回填卡片展示信息 ──────────────────────────────────

@router.post("/cards")
async def publish_community_card(
    req: PublishCardRequest,
    user: dict = Depends(get_current_user),
):
    """回填某条社区视频的标题与封面（前端解析成功后调用）。

    为什么需要它：解析时服务端拿不到平台标题与缩略图地址——字幕流里没有
    这些信息，而社区卡片要显示它们。标题与封面由已经拿到 `/api/parse`
    结果的前端回填一次，之后所有访客看到同一张卡片。

    回填不影响内容归属：任何人都能**填空**，但补不出总结、字幕或标签，
    也**改不动**别人已经填好的标题与封面——先到先得（见
    `database.publish_video_card` 的说明）。所以「补」不是「改」。
    """
    updated = publish_video_card(req.url, req.video_title, req.cover_url)
    return {"success": True, "updated": updated}
