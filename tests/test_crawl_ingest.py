"""M3 扩展事件入库：handle_boss_event 把 resume_downloaded 落成 BossCandidateRecord，
tenant_id 由外层 tenant_scope 自动盖章；两 org 隔离；采集任务生命周期串联 task_id。

直接调 handle_boss_event（它用全局 create_session）——monkeypatch 指向临时库，
避免碰真 dev 库；ensure_table 用的 engine 一并指过去。
"""
import pytest
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import sessionmaker

from recruitment_assistant.storage.resume_models import ResumeBase
from recruitment_assistant.storage import auth_models as _am  # noqa: F401
from recruitment_assistant.storage.models import BossCandidateRecord, CrawlTask
from recruitment_assistant.storage.tenancy import tenant_scope
from recruitment_assistant.services import crawl_task_service
from backend.app import crawl_ingest


def _resume_event(name, age="28", education="本科", position="后端开发（社招）/Java"):
    return {
        "type": "resume_downloaded",
        "data": {
            "candidate_signature": f"{name}/{age}/{education}",
            "candidate_info": {
                "name": name, "age": age, "education": education,
                "gender": "男", "job_title": "后端工程师",
                "phone": "13800000000", "talking_position": position,
            },
            "filename": f"{name}_简历.pdf",
            "url": "https://boss.example/resume/1",
        },
    }


@pytest.fixture()
def ingest(tmp_path, monkeypatch):
    eng = create_engine(f"sqlite:///{tmp_path/'ingest.db'}", echo=False)

    @event.listens_for(eng, "connect")
    def _fk(conn, _rec):
        conn.cursor().execute("PRAGMA foreign_keys=ON")

    ResumeBase.metadata.create_all(bind=eng)
    TestSession = sessionmaker(bind=eng)
    monkeypatch.setattr(crawl_ingest, "create_session", TestSession)
    monkeypatch.setattr(crawl_task_service, "engine", eng)  # ensure_table 建到临时库
    crawl_ingest._runs.clear()
    yield TestSession
    crawl_ingest._runs.clear()
    eng.dispose()


def test_resume_downloaded_persists_and_stamps_tenant(ingest):
    with tenant_scope(7, 70):
        crawl_ingest.handle_boss_event(70, 7, _resume_event("张三"))
    with tenant_scope(7, 70):
        s = ingest()
        rows = s.scalars(select(BossCandidateRecord)).all()
        assert len(rows) == 1
        r = rows[0]
        assert r.tenant_id == 7            # 自动盖为 org_id
        assert r.name == "张三"
        assert r.platform_code == "boss"
        assert r.target_site == "BOSS直聘"
        assert r.talking_position == "后端开发"   # 去括号 + 取斜杠前段
        assert r.resume_file_name == "张三_简历.pdf"
        s.close()


def test_two_orgs_isolated(ingest):
    # 不同候选人 → 不同 candidate_key，避开全局 uk(platform_code,candidate_key)。
    # ponytail: 两租户采到同一 BOSS 候选人会撞该全局唯一约束——去重表历史为单租户设计，
    # 真要按租户去重需把 tenant_id 并入约束，属后续。
    with tenant_scope(1, 10):
        crawl_ingest.handle_boss_event(10, 1, _resume_event("甲候选"))
    with tenant_scope(2, 20):
        crawl_ingest.handle_boss_event(20, 2, _resume_event("乙候选"))
    with tenant_scope(1, 10):
        s = ingest()
        assert [r.name for r in s.scalars(select(BossCandidateRecord)).all()] == ["甲候选"]
        s.close()
    with tenant_scope(2, 20):
        s = ingest()
        assert [r.name for r in s.scalars(select(BossCandidateRecord)).all()] == ["乙候选"]
        s.close()


def test_non_dict_frame_is_dropped_not_raised(ingest):
    # 数组/字符串/null 等非字典帧不得抛错逃出（否则会掀掉 WS 事件循环）。
    with tenant_scope(9, 90):
        crawl_ingest.handle_boss_event(90, 9, ["not", "a", "dict"])
        crawl_ingest.handle_boss_event(90, 9, "garbage")
        crawl_ingest.handle_boss_event(90, 9, None)  # type: ignore[arg-type]
    with tenant_scope(9, 90):
        s = ingest()
        assert s.scalars(select(BossCandidateRecord)).all() == []
        s.close()


def test_task_lifecycle_links_and_counts(ingest):
    with tenant_scope(3, 30):
        crawl_ingest.handle_boss_event(
            30, 3, {"type": "boss_content_script_collect_started", "data": {}}
        )
        crawl_ingest.handle_boss_event(30, 3, _resume_event("丙候选"))
        crawl_ingest.handle_boss_event(30, 3, {"type": "collect_finished", "data": {}})
    with tenant_scope(3, 30):
        s = ingest()
        task = s.scalars(select(CrawlTask)).one()
        assert task.tenant_id == 3
        assert task.status == "success"
        assert task.success_count == 1
        rec = s.scalars(select(BossCandidateRecord)).one()
        assert rec.task_id == task.id       # 候选人记录挂到本次采集任务
        s.close()
