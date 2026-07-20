"""M1 统一单库：JobPosition 与候选人/匹配同库，create_session 走 SQLite。"""
from recruitment_assistant.services.resume_archive_service import ResumeArchiveService
from recruitment_assistant.storage.models import JobPosition
from recruitment_assistant.storage.resume_models import Candidate


def test_create_session_is_sqlite_unified():
    # M1 后 create_session 与 create_resume_session 同引擎（SQLite）
    from recruitment_assistant.storage.db import engine as db_engine
    from recruitment_assistant.storage.resume_db import resume_engine
    assert db_engine is resume_engine
    assert db_engine.url.get_backend_name() == "sqlite"


def test_job_position_and_match_same_db_join(temp_resume_db):
    SessionLocal, _ = temp_resume_db
    s = SessionLocal()
    try:
        # 岗位 + 候选人在同一个库
        pos = JobPosition(title="后端", status="active")
        cand = Candidate(name="王五", age=30, education_level="本科", phone="13600000001")
        s.add_all([pos, cand]); s.commit()

        svc = ResumeArchiveService(s)
        svc.save_position_match(pos.id, cand.candidate_id, 91, "match", jd_hash="H")
        # 跨表 join（position_matches ↔ candidates 同库）
        rows = svc.list_position_matches(pos.id, min_score=0)
        assert len(rows) == 1
        match_row, joined_cand = rows[0]
        assert match_row.position_id == pos.id
        assert joined_cand.name == "王五"
        # position_id synonym 可用
        assert pos.position_id == pos.id
    finally:
        s.close()


def test_no_dead_pg_candidate_table():
    from recruitment_assistant.storage.resume_db import ResumeBase
    tables = set(ResumeBase.metadata.tables.keys())
    assert "candidate" not in tables       # 死 PG candidate 已删
    assert "resume" not in tables
    assert "export_record" not in tables
    assert {"job_position", "candidates", "position_matches", "crawl_task",
            "boss_candidate_record"} <= tables
