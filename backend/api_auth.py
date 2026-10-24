from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from auth import (
    create_token, get_current_user, hash_password,
    validate_email, validate_password, verify_password,
)
from database import create_user, get_user_by_email
from ratelimit import rate_limit

router = APIRouter(prefix="/api/auth", tags=["auth"])


class RegisterRequest(BaseModel):
    email: str
    password: str


class LoginRequest(BaseModel):
    email: str
    password: str


def _build_user_response(user: dict) -> dict:
    """构建用户信息响应，检查 VIP 是否过期"""
    is_vip = False
    vip_expire_at = None
    if user.get("is_vip") and user.get("vip_expire_at"):
        try:
            expire = datetime.fromisoformat(user["vip_expire_at"])
            # 兼容 naive datetime（旧数据 / 不同模块写入格式不同）
            if expire.tzinfo is None:
                expire = expire.replace(tzinfo=timezone.utc)
            is_vip = expire > datetime.now(timezone.utc)
            vip_expire_at = user["vip_expire_at"]
        except ValueError:
            pass
    return {
        "id": user["id"],
        "email": user["email"],
        "is_vip": is_vip,
        "vip_expire_at": vip_expire_at,
        # 前端没有任何别的途径知道「当前这个人是不是管理员」——管理入口的
        # 可见性判据最终来自这里。缺了这个字段，入口就永远不显示，而症状
        # 看起来像渲染 bug，实际是接口少给了一把钥匙。
        #
        # 只暴露**本人**的角色，不是提权路径：真正的边界仍是 require_admin
        # （工单 #11），而前端隐藏只做体验（ADR 0010）。
        #
        # 用 bool() 而不是原样透传：库里是 0/1，而前端要用它做 v-if 判据，
        # `0` 在 JS 里是 falsy 所以能用，但 `None`（老行没有该列时）也是
        # falsy、`1`/`0` 却是 number 不是 boolean，两种类型混在一个字段里
        # 会让「=== true」这种判据在某一侧悄悄失效。
        "is_admin": bool(user.get("is_admin")),
    }


@router.post("/register")
async def register(req: RegisterRequest, _rl: None = Depends(rate_limit("/api/register"))):
    """注册

    **限流**：注册是写库端点，每分钟同一来源最多 5 次。登录同理（10 次）——
    这两个端点要算 bcrypt，是撞库面。

    **不挂并发闸门**：注册是一次写库的短往返，占不住什么资源；并发闸门
    只挂在真正长时间占资源的解析与下载上（ADR 0021）。
    """
    if not validate_email(req.email):
        raise HTTPException(status_code=400, detail="邮箱格式不正确")
    err = validate_password(req.password)
    if err:
        raise HTTPException(status_code=400, detail=err)
    if get_user_by_email(req.email):
        raise HTTPException(status_code=400, detail="该邮箱已注册")

    hashed = hash_password(req.password)
    user = create_user(req.email, hashed)
    token = create_token(user["id"], req.email)

    return {
        "success": True,
        "data": {
            "token": token,
            "user": {"id": user["id"], "email": req.email, "is_vip": False, "vip_expire_at": None},
        },
    }


@router.post("/login")
async def login(req: LoginRequest, _rl: None = Depends(rate_limit("/api/login"))):
    """登录

    **限流**：登录要算 bcrypt（慢哈希），且是最典型的撞库面，每分钟同一
    来源最多 10 次。

    **不挂并发闸门**：一次登录是毫秒级往返，速率闸门对撞库面已经够用；
    并发闸门只挂在真正长时间占资源的解析与下载上（ADR 0021）。
    """
    user = get_user_by_email(req.email)
    if not user:
        raise HTTPException(status_code=400, detail="邮箱或密码错误")

    if not verify_password(req.password, user["password_hash"]):
        raise HTTPException(status_code=400, detail="邮箱或密码错误")

    token = create_token(user["id"], user["email"])
    return {
        "success": True,
        "data": {
            "token": token,
            "user": _build_user_response(user),
        },
    }


@router.get("/me")
async def get_me(user: dict = Depends(get_current_user)):
    return {
        "success": True,
        "data": _build_user_response(user),
    }
