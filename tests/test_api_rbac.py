"""M2.3 API 权限矩阵：租户隔离 + recruiter 归属可见范围 + logs admin 门禁（HTTP 层）。

复用 test_auth 的临时 SQLite + get_db override 思路。认证靠直接签发 JWT 写 cookie，
绕过飞书回调（回调已在 test_auth 覆盖）。
"""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from backend.app import security
from backend.app.deps import get_db
from backend.app.main import create_app
from recruitment_assistant.storage import auth_models as _am  # noqa: F401
from recruitment_assistant.storage import models as _m  # noqa: F401
from recruitment_assistant.storage.auth_models import Organization, Role, User
from recruitment_assistant.storage.resume_models import ResumeBase


@pytest.fixture()
def env(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path/'api.db'}", echo=False)

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
    tc = TestClient(app)

    # 两个租户，各 admin + 两名 recruiter
    users = {}
    with TestSession() as db:
        for org_key in ("org1", "org2"):
            org = Organization(feishu_tenant_key=f"tk_{org_key}", name=org_key)
            db.add(org)
            db.flush()
            for tag, role in (("admin", Role.admin), ("recA", Role.recruiter), ("recB", Role.recruiter)):
                u = User(org_id=org.id, feishu_open_id=f"ou_{org_key}_{tag}",
                         role=role.value, is_active=True, name=f"{org_key}-{tag}")
                db.add(u)
                db.flush()
                users[f"{org_key}_{tag}"] = u.id
        db.commit()

    def login(key: str):
        tc.cookies.set(security.COOKIE_NAME, security.create_access_token(users[key]))

    yield tc, login, TestSession
    engine.dispose()


def test_recruiter_owner_and_tenant_isolation(env):
    tc, login, _ = env

    login("org1_recA")
    cid = tc.post("/candidates", json={"name": "候选甲"}).json()["candidate_id"]

    # 同租户另一 recruiter：归属过滤 → 看不到、取不到
    login("org1_recB")
    assert tc.get("/candidates").json()["items"] == []
    assert tc.get(f"/candidates/{cid}").status_code == 404

    # 同租户 admin：整租户可见
    login("org1_admin")
    assert any(i["candidate_id"] == cid for i in tc.get("/candidates").json()["items"])
    assert tc.get(f"/candidates/{cid}").status_code == 200

    # 异租户 recruiter：租户过滤 → 看不到、取不到、删不掉
    login("org2_recA")
    assert tc.get("/candidates").json()["items"] == []
    assert tc.get(f"/candidates/{cid}").status_code == 404
    assert tc.delete(f"/candidates/{cid}").status_code == 404

    # owner 自己：可见可删
    login("org1_recA")
    assert tc.delete(f"/candidates/{cid}").status_code == 204
    assert tc.get(f"/candidates/{cid}").status_code == 404


def test_jobs_tenant_isolation(env):
    tc, login, _ = env
    login("org1_recA")
    jid = tc.post("/jobs", json={"title": "后端工程师"}).json()["id"]

    login("org2_admin")
    assert tc.get("/jobs").json() == []
    assert tc.get(f"/jobs/{jid}").status_code == 404

    login("org1_admin")  # 同租户 admin 可见
    assert tc.get(f"/jobs/{jid}").status_code == 200


def test_logs_admin_only(env, monkeypatch):
    tc, login, _ = env
    # monitoring 用自带 session（非 override）；此测试只验角色门禁，桩掉查询保持 hermetic
    import recruitment_assistant.services.monitoring as mon
    monkeypatch.setattr(mon, "list_operations", lambda *a, **k: [])
    monkeypatch.setattr(mon, "operation_summary", lambda *a, **k: {})
    monkeypatch.setattr(mon, "list_ai_usage", lambda *a, **k: [])
    monkeypatch.setattr(mon, "ai_usage_summary", lambda *a, **k: {})

    login("org1_recA")
    assert tc.get("/logs/operations").status_code == 403
    assert tc.get("/logs/ai-usage").status_code == 403

    login("org1_admin")
    assert tc.get("/logs/operations").status_code == 200
    assert tc.get("/logs/ai-usage").status_code == 200


def test_unauthenticated_401(env):
    tc, _, _ = env
    tc.cookies.clear()
    assert tc.get("/candidates").status_code == 401
    assert tc.get("/jobs").status_code == 401
