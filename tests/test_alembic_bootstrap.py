"""alembic 为唯一迁移源：全新库 upgrade head 建表；老库 create_all 引导后 stamp+upgrade 纳管。"""
from sqlalchemy import create_engine, event, inspect


def _temp_engine(path):
    eng = create_engine(f"sqlite:///{path.as_posix()}", echo=False)

    @event.listens_for(eng, "connect")
    def _fk(conn, _rec):
        cur = conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

    return eng


def _point_to(monkeypatch, path, engine):
    import recruitment_assistant.storage.resume_db as rdb
    monkeypatch.setenv("ALEMBIC_DB_URL", f"sqlite:///{path.as_posix()}")
    monkeypatch.setattr(rdb, "RESUME_DB_PATH", path, raising=True)
    monkeypatch.setattr(rdb, "resume_engine", engine, raising=True)
    monkeypatch.setattr(rdb, "_SCHEMA_READY", False, raising=True)
    return rdb


def test_fresh_db_built_by_alembic(tmp_path, monkeypatch):
    path = tmp_path / "resume_archive.db"
    eng = _temp_engine(path)
    rdb = _point_to(monkeypatch, path, eng)
    rdb.init_resume_database()
    insp = inspect(eng)
    assert insp.has_table("candidates")
    assert insp.has_table("position_matches")
    assert insp.has_table("operation_log")
    assert insp.has_table("alembic_version")   # 纳入 alembic 管理
    eng.dispose()


def test_legacy_db_stamped_and_upgraded(tmp_path, monkeypatch):
    path = tmp_path / "resume_archive.db"
    eng = _temp_engine(path)
    # 模拟 create_all 时代老库：建表但无 alembic_version
    from recruitment_assistant.storage.resume_models import ResumeBase
    from recruitment_assistant.storage import models as _m  # noqa: F401
    ResumeBase.metadata.create_all(bind=eng)
    assert not inspect(eng).has_table("alembic_version")

    rdb = _point_to(monkeypatch, path, eng)
    rdb.init_resume_database()
    assert inspect(eng).has_table("alembic_version")   # 已打标纳管，数据表仍在
    assert inspect(eng).has_table("candidates")
    eng.dispose()
