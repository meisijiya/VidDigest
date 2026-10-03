"""管理后台 API（工单 #12 / #13 / ADR 0010）。

四个管理能力 + 一个公开清单端点：

| 端点 | 鉴权 | 写操作 |
|---|---|---|
| `GET /api/models` | **公开** | 只读 |
| `GET /api/admin/models` | require_admin | 只读 |
| `PATCH /api/admin/models/{id}` | require_admin | **写**（改已有行，不新增） |
| `GET /api/admin/users` | require_admin | 只读 |
| `POST /api/admin/users/{id}/quota` | require_admin | **写** |
| `GET /api/admin/community` | require_admin | 只读 |

## 为什么 `/api/models` 是公开的

工单 #13 的原话：普通用户的 BYOK 面板要靠这份清单渲染厂商下拉，
挂 `require_admin` 会让「消除前后端漂移」变成「普通用户的下拉没了」。
公开端点只吐 `enabled=1` 的行与公共字段，与管理端点同源不同投影。

## 鉴权只写在依赖上

路由体内**没有一处** `if not user`：身份判定全部由 `require_admin`
在依赖层完成（ADR 0010 的约定）。少写一次判断就少一个「这条忘了挂依赖」
的位置，而那种遗漏在测试里表现为「全员 403」——照样绿。

## 响应体里没有凭据，也不该有

平台 Key 在 .env、用户 BYOK 不落盘（ADR 0004 / 0011）。本文件不做
「记得删掉 key 字段」这种逐个剔除，字段形状由数据层的白名单投影决定
（`model_catalog.PUBLIC_FIELDS` / `ADMIN_FIELDS`，以及各 `_admin_*_item`）。
将来给表加一列 `api_key`，白名单不会因为那列存在就把它吐出去。
"""

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict

import model_catalog
from auth import require_admin
from database import (
    ADMIN_PAGE_SIZE_DEFAULT,
    QUOTA_UNLIMITED,
    admin_user_detail,
    is_vip_active,
    list_admin_community,
    list_admin_users,
    set_user_quota_override,
)

logger = logging.getLogger("admin_api")

router = APIRouter(prefix="/api", tags=["管理后台"])


# ── 额度值域（唯一值得单测的部分）──────────────────────────
#
# 值域规则（工单 #12）：
#   null  → 清除覆盖，回落全局
#   0     → 一条都不能用
#   -1    → 无限（与数据层 remaining == -1 同一个约定）
#   其它负数 / 非整数 / 超大值 → 400
#
# 纯函数，不触库也不依赖 FastAPI，所以能直接单测。

#: 单人上限的可取上界。一年 10 万次解析远超任何真实用法，
#: 而没有上界的话一个手滑的 10**18 会变成「这列 int 存不下」的运行期错误。
QUOTA_OVERRIDE_MAX = 100_000


class QuotaValueError(ValueError):
    """额度值域非法。路由层把它翻译成 400。"""


def normalize_quota_value(raw: Any, field: str) -> int | None:
    """把请求里的一个额度值收敛成 int | None，非法就抛 QuotaValueError。

    刻意**只收 int**，float 一律拒绝：``1.0`` 与 ``1`` 在 JSON 里是同一个
    语义，但「收下 1.0」就得额外决定它存 1 还是 1.0，而那条规则没有消费者。
    前端也不会产生 ``5.0``——``JSON.stringify(5)`` 给的是 ``5``。

    bool 显式拒绝：Python 里 ``True == 1``，不拦的话 ``{"parse_limit": true}``
    会静静地变成「每天 1 次」。
    """
    if raw is None:
        return None
    if isinstance(raw, bool):
        raise QuotaValueError(f"{field} 必须是整数或 null，不能是布尔值")
    if not isinstance(raw, int):
        raise QuotaValueError(f"{field} 必须是整数或 null，实得 {raw!r}")
    if raw < QUOTA_UNLIMITED:
        raise QuotaValueError(
            f"{field} 只能取 null（回落全局）、0（不可用）或 -1（无限），"
            f"负数 {raw} 不在约定内"
        )
    if raw > QUOTA_OVERRIDE_MAX:
        raise QuotaValueError(
            f"{field} 上限是 {QUOTA_OVERRIDE_MAX}，实得 {raw}"
        )
    return raw


# ── 请求体 ──────────────────────────────────────────────────

class QuotaUpdateRequest(BaseModel):
    """改单人额度的请求体。

    两个字段都声明成 ``Any``：声明成 ``int | None`` 的话，非法值会由
    pydantic 变成 **422**，而契约要求的是 **400**——「参数不合法」与
    「请求体结构不对」是两种不同的客户端错误，不该混成一个码。
    值域判定交给 normalize_quota_value，那才是可单测的纯函数。

    **字段缺省 ≠ 传 null**：
      - 缺省（这个 key 压根没出现）→ 不动这一项，只改传了的那项
      - 显式 null → 清除覆盖，回落全局
    两者混为一谈的话，一次只想改对话额度的请求会把解析额度静默清掉。
    """

    parse_limit: Any = None
    chat_limit: Any = None


# ── 模型清单（工单 #13）─────────────────────────────────────

@router.get("/models")
async def public_model_providers():
    """可用厂商清单。**任何人可访问，含未登录访客。**

    刻意不挂鉴权依赖：厂商、端点、模型这些是**产品配置**，
    本来就该公开（byok.js 的注释第 4 条：配置可以公开，key 不行）。
    挂上 get_optional_user 再按登录状态分支，等于给「将来多给点字段」
    留一个位置；不挂则那个分支在结构上就不存在。
    """
    return {"items": model_catalog.list_model_providers(only_enabled=True)}


@router.get("/admin/models")
async def admin_model_providers(_: dict = Depends(require_admin)):
    """厂商清单（管理视图）。全部行，含已下架的，并带上架状态与排序。"""
    return {
        "items": model_catalog.list_model_providers(
            only_enabled=False, admin_view=True
        )
    }


class ModelUpdateRequest(BaseModel):
    """改一个厂商行。字段全是 ``Any``，值域判定交给数据层的纯函数。

    声明成具体类型的话非法值会由 pydantic 变成 422，而契约要的是 400 ——
    「参数不合法」与「请求体结构不对」是两种客户端错误，不该混成一个码
    （与 QuotaUpdateRequest 同一理由）。

    **没出现的键 = 不改这一项**，所以改默认模型不必把整行重发一遍。

    ``extra="forbid"`` 不是洁癖：pydantic 默认会**悄悄丢掉**未声明的键，
    于是 ``{"api_key": "...", "label": "新名"}`` 会只剩 label 生效并返回
    200 —— 调用方以为凭据也改了。数据层的 ``EDITABLE_FIELDS`` 拦得住这个，
    但它在 HTTP 边界根本走不到（键先被 pydantic 丢了），白写了。

    用 422 而不是 400 正是本类自己的口径：「参数不合法」(400) 与
    「请求体结构不对」(422) 是两种客户端错误，不该混成一个码。
    """

    model_config = ConfigDict(extra="forbid")

    label: Any = None
    hint: Any = None
    base_url: Any = None
    models: Any = None
    default_model: Any = None
    enabled: Any = None
    sort_order: Any = None


@router.patch("/admin/models/{provider_id}")
async def admin_update_model(
    provider_id: str,
    payload: ModelUpdateRequest,
    _: dict = Depends(require_admin),
):
    """改厂商行。**可改**：显示名、提示、端点、模型列表、平台默认、上下架、排序。

    明确不提供**新增**端点：新增平台厂商还得在 .env 里配凭据并重启
    （ADR 0011 记录的代价）。后台建出来的那一行会立刻不可用 ——
    那比「不能建」更糟。改已有行是纯配置操作，效果当场可见。

    改完**回读**而不是回显：返回的是「库里现在是什么」。
    """
    # exclude_unset=True → 只处理请求里真的出现的键
    patch = payload.model_dump(exclude_unset=True)
    try:
        updated = model_catalog.update_model_provider(provider_id, patch)
    except model_catalog.ModelValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    if updated is None:
        # 厂商 id 不存在：不能静默成功——那会让调用方以为改到了
        raise HTTPException(status_code=404, detail=f"厂商 {provider_id} 不存在")

    # 下架的即时可见性：平台默认模型读的就是 enabled=1 的行
    return {"item": updated, "platform_default": model_catalog.platform_default_model(provider_id)}


# ── 用户记录（工单 #12）─────────────────────────────────────

@router.get("/admin/users")
async def admin_users(
    limit: int = Query(ADMIN_PAGE_SIZE_DEFAULT, description="每页条数，有上界"),
    offset: int = Query(0, description="跳过的条数"),
    q: str = Query("", description="按邮箱模糊匹配"),
    _: dict = Depends(require_admin),
):
    """用户记录列表（只读）。分页上界由数据层统一收敛。"""
    return list_admin_users(limit=limit, offset=offset, q=q)


class QuotaUpdateResult(BaseModel):
    user: dict
    note: str | None = None
    message: str


@router.post("/admin/users/{user_id}/quota")
async def admin_set_quota(
    user_id: int,
    payload: QuotaUpdateRequest,
    _: dict = Depends(require_admin),
):
    """改某个用户的解析/对话额度。**本文件唯一的写操作。**

    明确不做（项目范围边界）：封禁、解封、删账号、下架社区视频。
    这里只动两列额度，且随时可以用 null 改回去。
    """
    # 404 先判：用户不存在时**不能**静默成功。
    before = admin_user_detail(user_id)
    if before is None:
        raise HTTPException(status_code=404, detail="用户不存在")

    # exclude_unset=True → 只处理请求里真的出现了的 key（见 QuotaUpdateRequest）。
    #
    # 字段名（parse_limit）在这里映射成额度种类（parse）：数据层那套
    # _QUOTA_KINDS 按种类索引，两种命名混过一次，症状是数据层抛
    # 「未知的额度类型 'parse_limit'」——只有真跑起来才看得见的错。
    supplied = payload.model_dump(exclude_unset=True)
    try:
        overrides = {}
        for kind in ("parse", "chat"):
            field = f"{kind}_limit"
            if field in supplied:
                overrides[kind] = normalize_quota_value(supplied[field], field)
    except QuotaValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    if overrides:
        set_user_quota_override(user_id, overrides)

    # 回读而不是回显：返回的是「库里现在是什么」，
    # 而不是「我们请求写进去的是什么」。
    user = admin_user_detail(user_id)
    if user is None:
        # 刚写完就没了：只有并发删除能做到。不假装成功。
        raise HTTPException(status_code=404, detail="用户不存在")

    if is_vip_active(user):
        # 覆盖值**照写不误**（VIP 到期后它就该生效），但必须明说此刻不生效：
        # check_quota_kind / consume_quota / refund_quota 三处都在解析上限
        # 之前就为 VIP 短路返回 -1。静默成功会让管理员以为改成了。
        return QuotaUpdateResult(
            user=user,
            note="vip_not_effective",
            message="给有效 VIP 改额度不会生效",
        )
    return QuotaUpdateResult(user=user, note=None, message="额度已更新")


# ── 社区记录（工单 #12）─────────────────────────────────────

@router.get("/admin/community")
async def admin_community(
    limit: int = Query(ADMIN_PAGE_SIZE_DEFAULT, description="每页条数，有上界"),
    offset: int = Query(0, description="跳过的条数"),
    _: dict = Depends(require_admin),
):
    """社区记录列表（只读）。不过滤 status——占位行也是记录。"""
    return list_admin_community(limit=limit, offset=offset)
