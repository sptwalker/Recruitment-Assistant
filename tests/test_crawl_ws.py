"""M2.5 采集 WebSocket 接入：JWT 鉴权、按用户注册、事件租户上下文、命令路由隔离（HTTP 层）。

复用 test_api_rbac 的临时 SQLite + get_db override 思路。每测重置全局 hub，避免串扰。
"""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from starlette.websockets import WebSocketDisconnect

from backend.app import security
from backend.app.crawl_hub import hub
from backend.app.deps import get_db, get_session_factory
from backend.app.main import create_app
from recruitment_assistant.storage import auth_models as _am  # noqa: F401
from recruitment_assistant.storage import models as _m  # noqa: F401
from recruitment_assistant.storage import tenancy
from recruitment_assistant.storage.auth_models import Organization, Role, User
from recruitment_assistant.storage.resume_models import ResumeBase


@pytest.fixture()
def env(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path/'crawl.db'}", echo=False)
    ResumeBase.metadata.create_all(bind=engine)
    TestSession = sessionmaker(bind=engine)

    def _override_db():
        db = TestSession()
        try:
            yield db
        finally:
            db.close()

    app = create_app()
    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[get_session_factory] = lambda: TestSession
    tc = TestClient(app)

    users = {}
    with TestSession() as db:
        for org_key in ("org1", "org2"):
            org = Organization(feishu_tenant_key=f"tk_{org_key}", name=org_key)
            db.add(org)
            db.flush()
            u = User(org_id=org.id, feishu_open_id=f"ou_{org_key}", role=Role.recruiter.value,
                     is_active=True, name=org_key)
            db.add(u)
            db.flush()
            users[org_key] = u.id
        db.commit()

    # 每测起点：清空全局中枢
    hub._conns.clear()
    hub.on_event = None

    def token(key: str) -> str:
        return security.create_access_token(users[key])

    def cookie(key: str) -> None:
        tc.cookies.set(security.COOKIE_NAME, security.create_access_token(users[key]))

    yield tc, token, cookie, users
    hub._conns.clear()
    hub.on_event = None
    engine.dispose()


def test_ws_rejects_missing_and_bad_token(env):
    tc, _, _, _ = env
    with pytest.raises(WebSocketDisconnect):
        with tc.websocket_connect("/crawl/ws"):
            pass
    with pytest.raises(WebSocketDisconnect):
        with tc.websocket_connect("/crawl/ws?token=garbage"):
            pass


def test_ws_registers_and_event_runs_in_tenant_context(env):
    tc, token, _, users = env
    captured = []
    hub.on_event = lambda uid, org_id, ev: captured.append(
        (uid, org_id, ev, tenancy.current_tenant_id(), tenancy.current_user_id())
    )

    with tc.websocket_connect(f"/crawl/ws?token={token('org1')}&platform=boss") as ws:
        assert hub.is_connected(users["org1"], "boss")
        ws.send_json({"type": "resume_scraped", "data": {"name": "甲"}})
        # 触发一次往返，确保服务端已处理事件
        ws.send_json({"type": "ping"})

    assert captured, "on_event 未被调用"
    uid, org_id, ev, ctx_tid, ctx_uid = captured[0]
    assert uid == users["org1"]
    assert ev["type"] == "resume_scraped"
    # 事件在该用户/租户上下文内处理：入库会自动盖 tenant_id/owner_id
    assert ctx_tid == org_id
    assert ctx_uid == users["org1"]
    # 断连后自动摘除
    assert not hub.is_connected(users["org1"])


def test_command_routes_to_own_extension_only(env):
    tc, token, cookie, users = env

    with tc.websocket_connect(f"/crawl/ws?token={token('org1')}&platform=boss") as ws1:
        # org1 自己下发命令 → 送达自己的扩展
        cookie("org1")
        r = tc.post("/crawl/command", json={"command": {"type": "start"}})
        assert r.json() == {"delivered": 1}
        assert ws1.receive_json() == {"type": "start"}

        # org2 下发命令 → 够不到 org1 的扩展（跨用户隔离），delivered=0
        cookie("org2")
        r = tc.post("/crawl/command", json={"command": {"type": "start"}})
        assert r.json() == {"delivered": 0}


def test_status_reports_own_connections(env):
    tc, token, cookie, users = env
    cookie("org1")
    assert tc.get("/crawl/status").json() == {"connected": False, "platforms": []}

    with tc.websocket_connect(f"/crawl/ws?token={token('org1')}&platform=boss"):
        cookie("org1")
        assert tc.get("/crawl/status").json() == {"connected": True, "platforms": ["boss"]}
        # org2 视角看不到 org1 的连接
        cookie("org2")
        assert tc.get("/crawl/status").json() == {"connected": False, "platforms": []}


def test_ws_unauthenticated_status_401(env):
    tc, _, _, _ = env
    tc.cookies.clear()
    assert tc.get("/crawl/status").status_code == 401


def test_token_mints_valid_jwt_for_current_user(env):
    tc, _, cookie, users = env
    cookie("org1")
    r = tc.get("/crawl/token")
    assert r.status_code == 200
    # 签发的 token 能解回本人 id（供扩展跨站带 query token 连 WS）
    assert security.decode_access_token(r.json()["token"]) == users["org1"]
    # 未登录拿不到
    tc.cookies.clear()
    assert tc.get("/crawl/token").status_code == 401

