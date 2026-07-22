"""统一数据库引擎（M2 起支持双方言）。

默认 SQLite（`data/resume_archive.db`，本地/桌面/测试）；设置环境变量
`DATABASE_URL=postgresql+psycopg://…` 即切到 PostgreSQL（多用户部署）。
方言相关处（外键 PRAGMA、alembic batch 模式、upsert）按 dialect 分支。
"""

import os
from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

RESUME_DB_PATH = Path("data/resume_archive.db")


def resolve_db_url() -> str:
    """引擎/迁移共用的 URL 解析：DATABASE_URL 优先，否则默认本地 SQLite。"""
    url = os.environ.get("DATABASE_URL", "").strip()
    if url:
        return url
    RESUME_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    return f"sqlite:///{RESUME_DB_PATH.as_posix()}"


class ResumeBase(DeclarativeBase):
    pass


def _get_resume_engine():
    engine = create_engine(resolve_db_url(), echo=False)

    if engine.dialect.name == "sqlite":
        @event.listens_for(engine, "connect")
        def _set_sqlite_pragma(dbapi_conn, _connection_record):
            cursor = dbapi_conn.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

    return engine


resume_engine = _get_resume_engine()
ResumeSessionLocal = sessionmaker(bind=resume_engine)

_MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"
_SCHEMA_READY = False


def create_resume_session() -> Session:
    return ResumeSessionLocal()


def _alembic_config():
    """程序化构建 alembic Config（不依赖 cwd 下的 alembic.ini）。

    script_location 用本模块旁的 migrations 目录绝对路径 → 打包后 cwd 变化也可靠。
    URL 由 env.py 从 RESUME_DB_PATH 兜底设置，这里显式再设一遍保持一致。
    """
    from alembic.config import Config

    cfg = Config()
    cfg.set_main_option("script_location", str(_MIGRATIONS_DIR))
    cfg.set_main_option("sqlalchemy.url", os.environ.get("ALEMBIC_DB_URL") or resolve_db_url())
    return cfg


def init_resume_database() -> None:
    """确保统一 SQLite schema 到最新——alembic 为唯一迁移源。

    进程内只跑一次（Streamlit 每次 rerun 都会调用本函数）。三种库形态：
    - **全新库**：`alembic upgrade head` 按迁移脚本建全部表。
    - **老库（create_all 时代，无 alembic_version）**：先 create_all 补齐缺失表 +
      幂等补齐历史手工列 → 打标到基线版本 → 再 upgrade head 应用后续迁移。
      （create_all / 手工 ALTER 仅作老库一次性引导，之后所有 schema 变更都走 alembic。）
    - **已纳管库**：`alembic upgrade head` 应用尚未执行的迁移。
    """
    global _SCHEMA_READY
    if _SCHEMA_READY:
        return

    from sqlalchemy import inspect
    from alembic import command
    from alembic.script import ScriptDirectory

    # 注册两套模型到统一 metadata（env.py 也依赖）
    from recruitment_assistant.storage import resume_models as _rm  # noqa: F401
    from recruitment_assistant.storage import models as _m  # noqa: F401

    insp = inspect(resume_engine)
    has_app_tables = insp.has_table("resume_source")
    has_alembic = insp.has_table("alembic_version")

    cfg = _alembic_config()
    if has_app_tables and not has_alembic and resume_engine.dialect.name == "sqlite":
        # 老库引导（仅 SQLite）：补齐到当前模型 schema（== 基线），再打标基线，最后 upgrade
        ResumeBase.metadata.create_all(bind=resume_engine)  # 补任何缺失的表
        _migrate_add_attachment_works_path()                # 补历史缺失的列
        _migrate_add_match_dimensions()
        base_rev = ScriptDirectory.from_config(cfg).get_bases()[0]
        command.stamp(cfg, base_rev)
    command.upgrade(cfg, "head")
    _SCHEMA_READY = True


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
        # ✨ jd_hash：记录评分时使用的 JD 文本哈希，JD 未变则跳过重复调用 LLM
        if "jd_hash" not in cols:
            conn.exec_driver_sql("ALTER TABLE position_matches ADD COLUMN jd_hash TEXT")
