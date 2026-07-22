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
from recruitment_assistant.storage import tenancy


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_session_factory():
    """返回 session 工厂本身（非 session）。给 WebSocket 这类长连场景用：
    在需要时临时开、用完立即关，不像 get_db 那样把连接占满整个连接生命周期。
    测试可 override 成临时库的 sessionmaker。"""
    return SessionLocal


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


async def tenant_ctx(user: User = Depends(get_current_user)):
    """设置本请求的租户/用户上下文，供 storage.tenancy 的 ORM 层过滤/盖章使用；请求结束复位。

    业务路由都应 Depends(tenant_ctx)（而非直接 get_current_user），以保证租户过滤生效。
    recruiter 只看自己盖章的根业务表（owner_id==自己）；admin/manager 看整租户。

    必须是 async：ContextVar 在请求所在的事件循环上下文里 set，sync 端点经 threadpool
    执行时会复制该上下文 → 过滤对 sync handler 生效（sync 依赖里 set 则不会传播过去）。
    """
    owner_only = user.id if user.role == Role.recruiter.value else None
    tokens = tenancy.set_context(user.org_id, user.id, owner_only)
    try:
        yield user
    finally:
        tenancy.reset_context(tokens)
