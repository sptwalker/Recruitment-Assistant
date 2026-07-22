"""本系统 JWT 签发/校验 + cookie 常量（M2.1）。

飞书只做身份认证；登录成功后签发本系统短期 JWT，放 httpOnly cookie。
密钥来自 settings.jwt_secret（走环境变量/.env，不入库不进 git）。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import jwt

from recruitment_assistant.config.settings import get_settings

ALGORITHM = "HS256"
COOKIE_NAME = "access_token"


def create_access_token(user_id: int) -> str:
    settings = get_settings()
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user_id),
        "iat": now,
        "exp": now + timedelta(hours=settings.jwt_expire_hours),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=ALGORITHM)


def decode_access_token(token: str) -> int | None:
    """返回 user_id；无效/过期返回 None。"""
    try:
        payload = jwt.decode(token, get_settings().jwt_secret, algorithms=[ALGORITHM])
        return int(payload["sub"])
    except (jwt.InvalidTokenError, KeyError, ValueError):
        return None
