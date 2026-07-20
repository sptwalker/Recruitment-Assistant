"""统一数据库入口（M1 后：单一 SQLite 库）。

历史上此模块承载捆绑 PostgreSQL 引擎；M1 地基整改后，全部表统一到 SQLite
（`resume_db.py` 的 `resume_engine` / `ResumeBase`）。本模块保留原有公开名字
（`Base` / `engine` / `SessionLocal` / `create_session` / `get_db` / `init_database`
/ `_ensure_boss_candidate_record_columns`）作为兼容层，让 ~40 处调用点无需改动，
底层引擎已从 PostgreSQL 变为 SQLite。
"""
from collections.abc import Generator

from sqlalchemy.orm import Session, sessionmaker

from recruitment_assistant.storage.resume_db import (
    ResumeBase,
    init_resume_database,
    resume_engine,
)

# 统一 Base / 引擎（指向 SQLite）
Base = ResumeBase
engine = resume_engine

SessionLocal = sessionmaker(bind=resume_engine, autoflush=False, autocommit=False, future=True)


def create_session() -> Session:
    return SessionLocal()


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_database() -> None:
    """建全部表（候选人 PII + 岗位/采集），统一由 SQLite 承载。"""
    init_resume_database()


def _ensure_boss_candidate_record_columns() -> None:
    """兼容旧调用：统一 SQLite 库后，boss_candidate_record 的列由模型 + create_all 保证，
    无需再手动 ALTER。保留空实现避免改调用点。"""
    return None
