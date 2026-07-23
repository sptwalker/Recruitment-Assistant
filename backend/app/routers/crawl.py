"""采集路由（M2.5）：扩展的鉴权 WebSocket 接入 + SPA 驱动采集的 HTTP 端点。

- WS `/crawl/ws?token=<JWT>&platform=boss`：扩展带 JWT 连入（跨站扩展未必能带 cookie，
  故支持 query token；也兼容 cookie）。校验通过 → 注册进 CrawlHub，按 user_id 路由。
  上报事件在该用户的 tenant 上下文内交给 hub.on_event（沿用 HTTP 层同一套租户隔离）。
- HTTP `GET /crawl/status`：查我自己的扩展在线情况。
- HTTP `POST /crawl/command`：给我自己的扩展下发命令（如开始/停止采集）。跨用户不可达。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, WebSocket
from pydantic import BaseModel
from sqlalchemy.orm import Session
from starlette.websockets import WebSocketDisconnect

from backend.app.crawl_hub import Conn, hub
from backend.app.deps import get_current_user, get_session_factory
from backend.app.security import COOKIE_NAME, create_access_token, decode_access_token
from recruitment_assistant.storage import tenancy
from recruitment_assistant.storage.auth_models import User

router = APIRouter(prefix="/crawl", tags=["crawl"])


def _authenticate(db: Session, token: str | None) -> User | None:
    """解 JWT → 加载在职用户。无效返回 None。"""
    if not token:
        return None
    user_id = decode_access_token(token)
    if user_id is None:
        return None
    user = db.get(User, user_id)
    if user is None or not user.is_active:
        return None
    # 拆出，避免关闭 session 后仍需访问属性时报 detached
    db.expunge(user)
    return user


@router.websocket("/ws")
async def crawl_ws(
    websocket: WebSocket,
    platform: str = "boss",
    token: str | None = None,
    session_factory=Depends(get_session_factory),
) -> None:
    # cookie 兜底（SPA 同源场景）；query token 优先（扩展跨站场景）
    tok = token or websocket.cookies.get(COOKIE_NAME)
    # 只在鉴权这一下用 DB：临时开、立即关，绝不把连接占满整个 WS 生命周期
    # （否则每个在线扩展占一条池连接，PG 上还 idle-in-transaction → 池耗尽）。
    with session_factory() as db:
        user = _authenticate(db, tok)
    if user is None:
        await websocket.close(code=1008)  # policy violation：未授权
        return

    await websocket.accept()
    conn = Conn(ws=websocket, platform=platform)
    hub.register(user.id, conn)
    try:
        while True:
            event = await websocket.receive_json()
            if hub.on_event is not None:
                # 上报事件在该用户/租户上下文内处理：入库自动盖 tenant_id/owner_id
                # ponytail: on_event 目前同步直调。M3 接真入库时若单条耗时明显，改
                # run_in_threadpool 并在线程内自建 tenant_scope（ContextVar 不跨线程传播）。
                with tenancy.tenant_scope(user.org_id, user.id):
                    hub.on_event(user.id, user.org_id, event)
    except WebSocketDisconnect:
        pass
    finally:
        hub.unregister(user.id, conn)


class CommandBody(BaseModel):
    command: dict
    platform: str | None = None  # None = 发给我所有平台的扩展


@router.get("/status")
def crawl_status(user: User = Depends(get_current_user)) -> dict:
    return {
        "connected": hub.is_connected(user.id),
        "platforms": hub.platforms_of(user.id),
    }


@router.post("/command")
async def crawl_command(
    body: CommandBody, user: User = Depends(get_current_user)
) -> dict:
    delivered = await hub.send_to_user(user.id, body.command, platform=body.platform)
    return {"delivered": delivered}


@router.get("/token")
def crawl_token(user: User = Depends(get_current_user)) -> dict:
    """签发一枚新 JWT 供用户复制到扩展 popup。登录 JWT 存 httpOnly cookie，SPA 的 JS
    读不到；扩展跨站又带不了 cookie，故这里现签一枚（同 secret/有效期）让用户手工搬运。
    """
    return {"token": create_access_token(user.id)}
