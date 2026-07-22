"""M2.1 认证测试：JWT 往返、未登录 401、飞书回调 upsert（mock exchange_code）。"""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from backend.app import security
from backend.app.deps import get_db
from backend.app.main import create_app
from recruitment_assistant.storage.resume_models import ResumeBase
from recruitment_assistant.storage import models as _m  # noqa: F401
from recruitment_assistant.storage import auth_models as _am  # noqa: F401
from recruitment_assistant.storage.auth_models import Organization, Role, User


@pytest.fixture()
def client(tmp_path, monkeypatch):
    """TestClient，DB 指向临时 SQLite（含全部表）；不进入 lifespan 故 startup 不跑。"""
    engine = create_engine(f"sqlite:///{tmp_path/'auth.db'}", echo=False)

    @event.listens_for(engine, "connect")
    def _fk(conn, _rec):
        conn.cursor().execute("PRAGMA foreign_keys=ON")

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
    yield TestClient(app), TestSession
    engine.dispose()


def test_jwt_roundtrip():
    token = security.create_access_token(42)
    assert security.decode_access_token(token) == 42
    assert security.decode_access_token("garbage") is None


def test_me_requires_auth(client):
    tc, _ = client
    assert tc.get("/auth/me").status_code == 401


def test_callback_creates_first_user_as_admin(client, monkeypatch):
    tc, TestSession = client
    monkeypatch.setattr(
        "backend.app.auth.feishu.exchange_code",
        lambda code: {
            "tenant_key": "tk_abc", "open_id": "ou_1",
            "union_id": "un_1", "name": "张三", "avatar_url": "http://a/x.png",
        },
    )
    tc.cookies.set("oauth_state", "s1")
    r = tc.get("/auth/feishu/callback?code=c1&state=s1", follow_redirects=False)
    assert r.status_code == 307  # RedirectResponse → 前端
    assert security.COOKIE_NAME in r.cookies

    with TestSession() as db:
        u = db.query(User).filter_by(feishu_open_id="ou_1").one()
        assert u.role == Role.admin.value
        assert u.name == "张三"
        assert db.query(Organization).filter_by(feishu_tenant_key="tk_abc").count() == 1

    # 带回签发的 cookie 访问 /auth/me
    me = tc.get("/auth/me")
    assert me.status_code == 200 and me.json()["role"] == "admin"


def test_callback_bad_state_rejected(client, monkeypatch):
    tc, _ = client
    tc.cookies.set("oauth_state", "real")
    r = tc.get("/auth/feishu/callback?code=c&state=forged", follow_redirects=False)
    assert r.status_code == 400


def test_second_user_in_org_is_recruiter(client, monkeypatch):
    tc, TestSession = client
    with TestSession() as db:
        org = Organization(feishu_tenant_key="tk_abc", name="ACME")
        db.add(org)
        db.flush()
        db.add(User(org_id=org.id, feishu_open_id="ou_existing", role=Role.admin.value))
        db.commit()

    monkeypatch.setattr(
        "backend.app.auth.feishu.exchange_code",
        lambda code: {"tenant_key": "tk_abc", "open_id": "ou_2", "name": "李四"},
    )
    tc.cookies.set("oauth_state", "s2")
    tc.get("/auth/feishu/callback?code=c2&state=s2", follow_redirects=False)
    with TestSession() as db:
        u = db.query(User).filter_by(feishu_open_id="ou_2").one()
        assert u.role == Role.recruiter.value
