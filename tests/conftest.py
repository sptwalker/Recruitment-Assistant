"""共享测试夹具。

temp_resume_db：把 resume 归档库指向 tmp_path 下的临时 SQLite，建表后产出 Session，
用于测试去重 / 匹配复用 / 备份等直接读写 resume_archive.db 的逻辑，避免污染真实库。
"""
import importlib

import pytest


@pytest.fixture()
def temp_resume_db(tmp_path, monkeypatch):
    """隔离的临时 resume SQLite 库。返回一个 (session_factory, db_path) 元组。"""
    from sqlalchemy import create_engine, event
    from sqlalchemy.orm import sessionmaker

    import recruitment_assistant.storage.resume_db as resume_db
    import recruitment_assistant.services.backup_service as backup_service

    db_path = tmp_path / "resume_archive.db"
    engine = create_engine(f"sqlite:///{db_path}", echo=False)

    @event.listens_for(engine, "connect")
    def _fk_on(dbapi_conn, _rec):
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

    # 重定向引擎 / 路径 / session 工厂
    monkeypatch.setattr(resume_db, "RESUME_DB_PATH", db_path, raising=True)
    monkeypatch.setattr(resume_db, "resume_engine", engine, raising=True)
    SessionLocal = sessionmaker(bind=engine)
    monkeypatch.setattr(resume_db, "ResumeSessionLocal", SessionLocal, raising=True)
    monkeypatch.setattr(resume_db, "create_resume_session", lambda: SessionLocal(), raising=True)
    # M1 统一库后 db.py 也在同一引擎上有独立 sessionmaker——测试里一并重定向到临时库，
    # 否则用 create_session() 的调用点会打到开发机真实 resume_archive.db。
    import recruitment_assistant.storage.db as db
    monkeypatch.setattr(db, "engine", engine, raising=True)
    monkeypatch.setattr(db, "SessionLocal", SessionLocal, raising=True)
    monkeypatch.setattr(db, "create_session", lambda: SessionLocal(), raising=True)
    # backup_service 在 import 时绑定了 RESUME_DB_PATH，同步指向临时库
    monkeypatch.setattr(backup_service, "RESUME_DB_PATH", db_path, raising=True)

    # 建表（导入两套模型，确保 18 张表全部注册到统一 metadata）
    from recruitment_assistant.storage.resume_models import ResumeBase
    from recruitment_assistant.storage import models as _m  # noqa: F401
    ResumeBase.metadata.create_all(bind=engine)

    yield SessionLocal, db_path

    engine.dispose()
