"""M2.2 租户隔离越权矩阵 + 自动盖章 + 审计。这是 M2 安全核心，必须覆盖越权。

事件监听注册在全局 Session 类上（storage.tenancy 导入即注册），故任意 sessionmaker 都生效。
"""
import pytest
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import sessionmaker

from recruitment_assistant.storage.resume_models import (
    Candidate, Education, ResumeBase,
)
from recruitment_assistant.storage import models as _m  # noqa: F401
from recruitment_assistant.storage import auth_models as _am  # noqa: F401
from recruitment_assistant.storage.tenancy import tenant_scope


@pytest.fixture()
def NewSession(tmp_path):
    eng = create_engine(f"sqlite:///{tmp_path/'t.db'}", echo=False)

    @event.listens_for(eng, "connect")
    def _fk(conn, _rec):
        conn.cursor().execute("PRAGMA foreign_keys=ON")

    ResumeBase.metadata.create_all(bind=eng)
    yield sessionmaker(bind=eng)
    eng.dispose()


def _make_candidate(NewSession, name, tid, uid) -> int:
    with tenant_scope(tid, uid):
        s = NewSession()
        c = Candidate(name=name)
        s.add(c)
        s.commit()
        cid = c.candidate_id
        s.close()
    return cid


def test_insert_auto_stamps_tenant_and_owner(NewSession):
    cid = _make_candidate(NewSession, "Alice", 1, 10)
    with tenant_scope(1, 10):
        s = NewSession()
        c = s.get(Candidate, cid)
        assert c.tenant_id == 1 and c.owner_id == 10
        s.close()


def test_other_tenant_cannot_see_or_fetch(NewSession):
    cid = _make_candidate(NewSession, "Alice", 1, 10)
    # 异租户：列表为空，按 id 取也取不到（→ 无法改/删）
    with tenant_scope(2, 20):
        s = NewSession()
        assert s.scalars(select(Candidate)).all() == []
        assert s.get(Candidate, cid) is None
        s.close()
    # 同租户可见
    with tenant_scope(1, 10):
        s = NewSession()
        assert len(s.scalars(select(Candidate)).all()) == 1
        s.close()


def test_no_context_sees_all(NewSession):
    """本地/桌面 Streamlit、脚本无租户上下文 → 不过滤（单机行为不变）。"""
    _make_candidate(NewSession, "A", 1, 10)
    _make_candidate(NewSession, "B", 2, 20)
    s = NewSession()
    assert len(s.scalars(select(Candidate)).all()) == 2
    s.close()


def test_relationship_load_scoped_via_parent(NewSession):
    """深层子表（education 无 tenant_id）经 candidate 关系加载正常；异租户拿不到父 → 拿不到子。"""
    with tenant_scope(1, 10):
        s = NewSession()
        c = Candidate(name="A")
        c.educations.append(Education(school_name="X 大学"))
        s.add(c)
        s.commit()
        cid = c.candidate_id
        s.close()
    with tenant_scope(1, 10):
        s = NewSession()
        assert len(s.get(Candidate, cid).educations) == 1
        s.close()
    with tenant_scope(2, 20):
        s = NewSession()
        assert s.get(Candidate, cid) is None
        s.close()


def test_audit_stamps_actor_and_tenant(temp_resume_db):
    """record_operation 在请求上下文内写入 actor_user_id/tenant_id。"""
    from recruitment_assistant.services import monitoring
    from recruitment_assistant.storage.resume_models import OperationLog

    SessionLocal, _ = temp_resume_db
    with tenant_scope(7, 77):
        monitoring.record_operation("测试动作", target="x", status="完成")
    s = SessionLocal()
    row = s.query(OperationLog).filter_by(action="测试动作").one()
    assert row.actor_user_id == 77 and row.tenant_id == 7
    s.close()
