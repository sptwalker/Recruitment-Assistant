"""M3 采集表租户隔离：crawl_task / boss_candidate_record 仅 TenantMixin（租户级隔离，
无 owner_id）。验证自动盖章 + 异租户不可见。与 test_tenancy.py 同套事件监听。
"""
import pytest
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import sessionmaker

from recruitment_assistant.storage.resume_models import ResumeBase
from recruitment_assistant.storage import auth_models as _am  # noqa: F401
from recruitment_assistant.storage.models import CrawlTask, BossCandidateRecord
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


def _make_task(NewSession, name, tid, uid) -> int:
    with tenant_scope(tid, uid):
        s = NewSession()
        t = CrawlTask(platform_code="boss", task_name=name, task_type="search")
        s.add(t)
        s.commit()
        tid_ = t.id
        s.close()
    return tid_


def _make_record(NewSession, key, tid, uid) -> int:
    with tenant_scope(tid, uid):
        s = NewSession()
        r = BossCandidateRecord(
            platform_code="boss", target_site="boss", candidate_key=key, name="张三"
        )
        s.add(r)
        s.commit()
        rid = r.id
        s.close()
    return rid


def test_crawl_task_auto_stamps_tenant(NewSession):
    tid_ = _make_task(NewSession, "任务A", 1, 10)
    with tenant_scope(1, 10):
        s = NewSession()
        t = s.get(CrawlTask, tid_)
        assert t.tenant_id == 1
        assert not hasattr(t, "owner_id")  # 仅 TenantMixin，无 owner
        s.close()


def test_crawl_task_other_tenant_cannot_see(NewSession):
    tid_ = _make_task(NewSession, "任务A", 1, 10)
    with tenant_scope(2, 20):
        s = NewSession()
        assert s.scalars(select(CrawlTask)).all() == []
        assert s.get(CrawlTask, tid_) is None
        s.close()
    with tenant_scope(1, 10):
        s = NewSession()
        assert len(s.scalars(select(CrawlTask)).all()) == 1
        s.close()


def test_boss_record_auto_stamps_and_isolates(NewSession):
    rid = _make_record(NewSession, "k-1", 1, 10)
    with tenant_scope(1, 10):
        s = NewSession()
        r = s.get(BossCandidateRecord, rid)
        assert r.tenant_id == 1
        s.close()
    with tenant_scope(2, 20):
        s = NewSession()
        assert s.scalars(select(BossCandidateRecord)).all() == []
        assert s.get(BossCandidateRecord, rid) is None
        s.close()


def test_crawl_no_context_sees_all(NewSession):
    """无租户上下文（本地脚本/桌面）→ 不过滤，单机行为不变。"""
    _make_task(NewSession, "A", 1, 10)
    _make_task(NewSession, "B", 2, 20)
    s = NewSession()
    assert len(s.scalars(select(CrawlTask)).all()) == 2
    s.close()
