import os
import re
from datetime import datetime, timedelta, timezone

import bcrypt
import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

# 密钥**没有默认值**：漏配时进程起不来，而不是拿一个公开在源码里的字符串兜底。
#
# 从「部署注意事项」升级为硬失败（工单 #11 / ADR 0010）：管理后台上线后，
# 猜中这个常量就等于能伪造任意管理员 token——兜底值的代价从「不好看」变成
# 「任何人都是管理员」。
#
# 纯空白视同未配置，与 database._env_int 里 `not raw.strip()` 的既有约定一致。
_raw_jwt_secret = os.getenv("JWT_SECRET")
if not _raw_jwt_secret or not _raw_jwt_secret.strip():
    raise RuntimeError(
        "缺少环境变量 JWT_SECRET，进程拒绝启动。\n"
        "  配法一：在 backend/.env 里写一行 JWT_SECRET=<32 位以上随机串>\n"
        "  配法二：导出环境变量 JWT_SECRET=$(openssl rand -hex 32)\n"
        "  已有 .env 的部署请确认该文件未漏提交、且启动进程读得到它。"
    )
JWT_SECRET = _raw_jwt_secret.strip()
JWT_ALGORITHM = "HS256"
JWT_EXPIRE_HOURS = 72

security = HTTPBearer(auto_error=False)


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(password: str, hashed: str) -> bool:
    return bcrypt.checkpw(password.encode(), hashed.encode())


def create_token(user_id: int, email: str) -> str:
    payload = {
        "sub": str(user_id),
        "email": email,
        "exp": datetime.now(timezone.utc) + timedelta(hours=JWT_EXPIRE_HOURS),
        "iat": datetime.now(timezone.utc),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def decode_token(token: str) -> dict:
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        payload["sub"] = int(payload["sub"])
        return payload
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token 已过期，请重新登录")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="无效的 Token")


def validate_email(email: str) -> bool:
    return bool(re.match(r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$", email))


def validate_password(password: str) -> str | None:
    if len(password) < 6:
        return "密码长度不能少于 6 位"
    if len(password) > 50:
        return "密码长度不能超过 50 位"
    return None


async def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(security)) -> dict:
    """必须登录的依赖注入"""
    if not credentials:
        raise HTTPException(status_code=401, detail="请先登录")
    payload = decode_token(credentials.credentials)
    from database import get_user_by_id
    user = get_user_by_id(payload["sub"])
    if not user:
        raise HTTPException(status_code=401, detail="用户不存在")
    return user


async def get_optional_user(credentials: HTTPAuthorizationCredentials = Depends(security)) -> dict | None:
    """可选登录：已登录返回用户信息，未登录返回 None"""
    if not credentials:
        return None
    try:
        payload = decode_token(credentials.credentials)
        from database import get_user_by_id
        return get_user_by_id(payload["sub"])
    except HTTPException:
        return None


async def require_admin(user: dict = Depends(get_current_user)) -> dict:
    """管理员专用依赖。

    身份**每次请求都回查 users 表**（get_current_user 已经是 SELECT *），
    所以 is_admin 不进 JWT：提权与撤权在**下一次请求**就生效，不用等 token 过期。
    代价是每次多一次查询——后台是低频操作，这个交换划算（ADR 0010）。

    鉴权只在这里发生，路由体内不许再写 `if not user`。
    """
    if not user.get("is_admin"):
        raise HTTPException(status_code=403, detail="需要管理员权限")
    return user
