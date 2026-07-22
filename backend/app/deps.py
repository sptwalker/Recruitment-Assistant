"""FastAPI 依赖：DB session、当前用户、角色校验（M2.1）。

get_current_user 从 httpOnly cookie 里的 JWT 解出 user_id，加载 User。
未登录/失效/停用 → 401。require_role(...) 生成角色门禁依赖。
"""
from __future__ import annotations

from collections.abc import Generator

from fastapi import Cookie, Depends, HTTPException, status
from sqlalchemy.orm import Session

from backend.app.security import COOKIE_NAME, decode_access_token
from recruitment_assistant.storage.auth_models import Role, User
from recruitment_assistant.storage.db import SessionLocal


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_current_user(
    access_token: str | None = Cookie(default=None, alias=COOKIE_NAME),
    db: Session = Depends(get_db),
) -> User:
    if not access_token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "未登录")
    user_id = decode_access_token(access_token)
    if user_id is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "登录已失效")
    user = db.get(User, user_id)
    if user is None or not user.is_active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "用户不存在或已停用")
    return user


def require_role(*roles: Role):
    """生成角色门禁依赖：当前用户角色不在 roles 内 → 403。"""
    allowed = {r.value for r in roles}

    def _dep(user: User = Depends(get_current_user)) -> User:
        if user.role not in allowed:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "权限不足")
        return user

    return _dep
