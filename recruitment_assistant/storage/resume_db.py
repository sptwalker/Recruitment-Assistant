"""独立 SQLite 简历归档数据库引擎。

与现有 PostgreSQL 采集任务库完全隔离，数据文件位于 data/resume_archive.db。
"""

from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

RESUME_DB_PATH = Path("data/resume_archive.db")


class ResumeBase(DeclarativeBase):
    pass


def _get_resume_engine():
    RESUME_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(f"sqlite:///{RESUME_DB_PATH}", echo=False)

    @event.listens_for(engine, "connect")
    def _set_sqlite_pragma(dbapi_conn, _connection_record):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    return engine


resume_engine = _get_resume_engine()
ResumeSessionLocal = sessionmaker(bind=resume_engine)


def create_resume_session() -> Session:
    return ResumeSessionLocal()


def init_resume_database() -> None:
    from recruitment_assistant.storage.resume_models import ResumeBase as _  # noqa: F401 ensure models registered
    ResumeBase.metadata.create_all(bind=resume_engine)
