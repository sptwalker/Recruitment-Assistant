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
    _migrate_add_attachment_works_path()
    _migrate_add_match_dimensions()


def _migrate_add_attachment_works_path() -> None:
    """旧库 idempotent 加列：resume_source.attachment_works_path TEXT NULL。

    Base.metadata.create_all 不会 ALTER 已存在的表；老库启动时手动补列，重复启动安全。
    """
    with resume_engine.begin() as conn:
        cols = {row[1] for row in conn.exec_driver_sql("PRAGMA table_info(resume_source)").fetchall()}
        if "attachment_works_path" not in cols:
            conn.exec_driver_sql("ALTER TABLE resume_source ADD COLUMN attachment_works_path TEXT")


def _migrate_add_match_dimensions() -> None:
    """✨ 旧库 idempotent 加列：position_matches 多维度评分字段。

    添加 skill_match, experience_match, education_match, location_match 四个维度字段。
    """
    with resume_engine.begin() as conn:
        cols = {row[1] for row in conn.exec_driver_sql("PRAGMA table_info(position_matches)").fetchall()}
        if "skill_match" not in cols:
            conn.exec_driver_sql("ALTER TABLE position_matches ADD COLUMN skill_match INTEGER")
        if "experience_match" not in cols:
            conn.exec_driver_sql("ALTER TABLE position_matches ADD COLUMN experience_match INTEGER")
        if "education_match" not in cols:
            conn.exec_driver_sql("ALTER TABLE position_matches ADD COLUMN education_match INTEGER")
        if "location_match" not in cols:
            conn.exec_driver_sql("ALTER TABLE position_matches ADD COLUMN location_match INTEGER")
