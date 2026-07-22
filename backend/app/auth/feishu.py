"""飞书（Lark）OAuth 授权码登录（M2.1）。

流程：/auth/feishu/login 302 到飞书授权页（带 state cookie 防 CSRF）→ 用户授权 →
飞书回调 /auth/feishu/callback?code&state → 后端换 app_access_token → 换 user_access_token
→ 拉用户信息（open_id/union_id/tenant_key/name/avatar）→ upsert Org/User → 签本系统 JWT
写 httpOnly cookie → 302 回前端。

一个飞书企业(tenant_key) = 一个 Organization；企业内第一个登录的用户为 admin，其余 recruiter。
飞书 App ID/Secret 走 settings（环境变量/.env），不入库不进 git。
"""
from __future__ import annotations

import secrets
from datetime import datetime, timezone
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, Cookie, Depends, HTTPException, Query, Response, status
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.deps import get_current_user, get_db
from backend.app.security import COOKIE_NAME, create_access_token
from recruitment_assistant.config.settings import get_settings
from recruitment_assistant.storage.auth_models import Organization, Role, User

router = APIRouter(prefix="/auth", tags=["auth"])

_FEISHU_BASE = "https://open.feishu.cn/open-apis"
_AUTHORIZE_URL = f"{_FEISHU_BASE}/authen/v1/authorize"
_STATE_COOKIE = "oauth_state"


def _feishu_get(url: str, token: str) -> dict:
    r = httpx.get(url, headers={"Authorization": f"Bearer {token}"}, timeout=10)
    r.raise_for_status()
    return r.json()


def exchange_code(code: str) -> dict:
    """用授权码换飞书用户信息。返回 user_info 的 data 段（open_id/union_id/tenant_key/...）。

    抽成独立函数便于单测 mock。三步：app_access_token → user_access_token → user_info。
    """
    settings = get_settings()
    with httpx.Client(timeout=10) as client:
        app_resp = client.post(
            f"{_FEISHU_BASE}/auth/v3/app_access_token/internal",
            json={"app_id": settings.feishu_app_id, "app_secret": settings.feishu_app_secret},
        )
        app_resp.raise_for_status()
        app_token = app_resp.json()["app_access_token"]

        tok_resp = client.post(
            f"{_FEISHU_BASE}/authen/v1/oidc/access_token",
            headers={"Authorization": f"Bearer {app_token}"},
            json={"grant_type": "authorization_code", "code": code},
        )
        tok_resp.raise_for_status()
        user_token = tok_resp.json()["data"]["access_token"]

    info = _feishu_get(f"{_FEISHU_BASE}/authen/v1/user_info", user_token)
    return info["data"]


def _upsert_user(db: Session, profile: dict) -> User:
    """按 tenant_key upsert Org，按 open_id upsert User。企业内首个用户 = admin。"""
    tenant_key = profile.get("tenant_key") or "default"
    org = db.scalar(select(Organization).where(Organization.feishu_tenant_key == tenant_key))
    if org is None:
        org = Organization(feishu_tenant_key=tenant_key, name=profile.get("tenant_key"))
        db.add(org)
        db.flush()  # 拿 org.id

    user = db.scalar(select(User).where(User.feishu_open_id == profile["open_id"]))
    if user is None:
        # 企业内是否已有用户 → 决定角色（首个 admin）
        has_member = db.scalar(select(User.id).where(User.org_id == org.id).limit(1)) is not None
        user = User(
            org_id=org.id,
            feishu_open_id=profile["open_id"],
            role=Role.recruiter.value if has_member else Role.admin.value,
        )
        db.add(user)

    # 每次登录刷新可变身份字段
    user.feishu_union_id = profile.get("union_id")
    user.feishu_user_id = profile.get("user_id")
    user.name = profile.get("name")
    user.avatar_url = profile.get("avatar_url")
    user.email = profile.get("email") or profile.get("enterprise_email")
    user.last_login_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(user)
    return user


@router.get("/feishu/login")
def feishu_login() -> RedirectResponse:
    settings = get_settings()
    if not settings.feishu_app_id:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "飞书应用未配置")
    state = secrets.token_urlsafe(24)
    params = {
        "app_id": settings.feishu_app_id,
        "redirect_uri": settings.feishu_redirect_uri,
        "state": state,
    }
    resp = RedirectResponse(f"{_AUTHORIZE_URL}?{urlencode(params)}")
    # state 存 httpOnly cookie，回调时比对，防 CSRF
    resp.set_cookie(_STATE_COOKIE, state, httponly=True, samesite="lax", max_age=600)
    return resp


@router.get("/feishu/callback")
def feishu_callback(
    code: str = Query(...),
    state: str = Query(...),
    oauth_state: str | None = Cookie(default=None, alias=_STATE_COOKIE),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    if not oauth_state or not secrets.compare_digest(state, oauth_state):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "state 校验失败")
    profile = exchange_code(code)
    user = _upsert_user(db, profile)

    token = create_access_token(user.id)
    resp = RedirectResponse(get_settings().frontend_origin)
    resp.set_cookie(COOKIE_NAME, token, httponly=True, samesite="lax",
                    max_age=get_settings().jwt_expire_hours * 3600)
    resp.delete_cookie(_STATE_COOKIE)
    return resp


@router.get("/me")
def me(user: User = Depends(get_current_user)) -> dict:
    return {
        "id": user.id,
        "org_id": user.org_id,
        "name": user.name,
        "avatar_url": user.avatar_url,
        "email": user.email,
        "role": user.role,
    }


@router.post("/logout")
def logout(resp: Response) -> dict:
    resp.delete_cookie(COOKIE_NAME)
    return {"status": "ok"}
