"""PG→SQLite 一次性迁移的拷贝逻辑与幂等 marker（引擎无关，用 SQLite 模拟 source）。"""
from sqlalchemy import create_engine, select

from recruitment_assistant.storage import migrate_pg_to_sqlite as M
from recruitment_assistant.storage.resume_db import ResumeBase
from recruitment_assistant.storage import models as _m  # noqa: F401
from recruitment_assistant.storage import resume_models as _rm  # noqa: F401


def _engine(path, enforce_fk=True):
    from sqlalchemy import event
    eng = create_engine(f"sqlite:///{path}", echo=False)

    if enforce_fk:
        @event.listens_for(eng, "connect")
        def _fk(dbapi, _rec):
            cur = dbapi.cursor(); cur.execute("PRAGMA foreign_keys=ON"); cur.close()

    ResumeBase.metadata.create_all(bind=eng)
    return eng


def test_copy_tables_moves_rows_and_preserves_ids(tmp_path):
    src = _engine(tmp_path / "src.db")
    dst = _engine(tmp_path / "dst.db")
    md = ResumeBase.metadata

    # 在 source 放入岗位 + 采集任务（含显式 id，迁移须原样保留）
    with src.begin() as c:
        c.execute(md.tables["job_position"].insert(), [{"id": 42, "title": "Go 工程师", "status": "active"}])
        c.execute(md.tables["crawl_task"].insert(), [
            {"id": 7, "platform_code": "boss", "task_name": "t", "task_type": "collect", "status": "success"}])

    counts = M.copy_tables(src, dst)
    assert counts["job_position"] == 1 and counts["crawl_task"] == 1

    with dst.connect() as c:
        jp = c.execute(select(md.tables["job_position"])).mappings().all()
        assert len(jp) == 1 and jp[0]["id"] == 42 and jp[0]["title"] == "Go 工程师"

    # 再拷一次：ON CONFLICT DO NOTHING，不重复、不报错
    M.copy_tables(src, dst)
    with dst.connect() as c:
        assert len(c.execute(select(md.tables["job_position"])).all()) == 1


def test_copy_tables_survives_dangling_fk(tmp_path):
    """遗留 boss_candidate_record 指向不存在的 raw_resume/crawl_task 时：
    坏行被跳过，但不连累 job_position（必须迁成功）。"""
    src = _engine(tmp_path / "src.db", enforce_fk=False)  # 模拟 PG source（FK 较松，可有悬空引用）
    dst = _engine(tmp_path / "dst.db")
    md = ResumeBase.metadata
    with src.begin() as c:
        # task_id=777 无对应 crawl_task；raw_resume_id=888 无对应 raw_resume（悬空引用）
        c.execute(md.tables["boss_candidate_record"].insert(), [{
            "id": 1, "platform_code": "boss", "target_site": "zhipin",
            "candidate_key": "k1", "task_id": 777, "raw_resume_id": 888,
        }])
        c.execute(md.tables["job_position"].insert(), [{"id": 5, "title": "岗位", "status": "active"}])
    counts = M.copy_tables(src, dst)   # 不应抛 IntegrityError
    # 悬空引用的坏行被跳过；job_position 仍成功迁移（互不连累）
    assert counts["boss_candidate_record"] == 0
    assert counts["job_position"] == 1
    with dst.connect() as c:
        assert len(c.execute(select(md.tables["job_position"])).all()) == 1


def test_crawl_task_clear_records_no_nameerror(temp_resume_db):
    """回归：clear_records/delete_* 用到 delete()，曾因漏 import 触发 NameError。"""
    from recruitment_assistant.services.crawl_task_service import BossCandidateRecordService
    SessionLocal, _ = temp_resume_db
    s = SessionLocal()
    try:
        svc = BossCandidateRecordService(s)
        # 空库调用也应正常返回 0，不抛 NameError
        assert svc.clear_records("boss") == 0
        assert svc.delete_all_by_platform("boss") == set()
    finally:
        s.close()


def test_migrate_if_needed_fresh_install_writes_marker(tmp_path, monkeypatch):
    monkeypatch.setattr(M, "MARKER_PATH", tmp_path / "data" / ".pg_migrated", raising=True)
    monkeypatch.setattr(M, "PGDATA_DIR", tmp_path / "pgdata", raising=True)  # 不存在=全新装
    status = M.migrate_if_needed()
    assert status == "fresh_install_skip"
    assert M.MARKER_PATH.exists()


def test_migrate_if_needed_idempotent(tmp_path, monkeypatch):
    marker = tmp_path / "data" / ".pg_migrated"
    marker.parent.mkdir(parents=True)
    marker.write_text("done")
    monkeypatch.setattr(M, "MARKER_PATH", marker, raising=True)
    monkeypatch.setattr(M, "PGDATA_DIR", tmp_path / "pgdata", raising=True)
    assert M.migrate_if_needed() == "already_migrated"
