"""一次性升级迁移：把旧版捆绑 PostgreSQL 的 5 张活表搬进统一 SQLite 库。

M1 前：job_position / crawl_task / boss_candidate_record / raw_resume / platform_account
在捆绑 PG；M1 后统一到 SQLite。老用户升级时需把这些数据搬过来，否则岗位/去重记录丢失。

设计要点（安全第一）：
- 幂等 marker `data/.pg_migrated`：迁移过就不再迁。
- 迁移前先备份 SQLite（backup_service）。
- `INSERT ... ON CONFLICT DO NOTHING`：保留 SQLite 既有行，保留 PG 原始 id
  （position_matches.position_id 引用 job_position.id，id 必须原样保留）。
- **不删 PG 数据**（留作可恢复回退）。
- 全新安装（无 pgdata）：直接写 marker 跳过。
"""
from __future__ import annotations

from pathlib import Path

from loguru import logger
from sqlalchemy import select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

MARKER_PATH = Path("data/.pg_migrated")
PGDATA_DIR = Path("pgdata")

# 依赖顺序（先父后子），保证 FK 目标先就位
MIGRATION_TABLES = [
    "platform_account",
    "crawl_task",
    "raw_resume",
    "boss_candidate_record",
    "job_position",
]


def copy_tables(src_engine, dst_engine, tables: list[str] = MIGRATION_TABLES) -> dict[str, int]:
    """把 tables 从 src 引擎逐表拷到 dst（SQLite），ON CONFLICT DO NOTHING。返回每表拷贝行数。"""
    from recruitment_assistant.storage.resume_db import ResumeBase
    # 确保两套模型都已注册到统一 metadata
    from recruitment_assistant.storage import models as _m  # noqa: F401
    from recruitment_assistant.storage import resume_models as _rm  # noqa: F401

    md = ResumeBase.metadata
    counts: dict[str, int] = {}
    # 逐表拷贝，每表独立事务 + best-effort：
    # - 表按父→子顺序（MIGRATION_TABLES），正常情况下 FK 目标先就位。
    # - 若某表整批插入失败（如遗留悬空引用），回退逐行插入、跳过坏行——
    #   保证一张表的问题不会连累其余表（尤其 job_position 是独立的、必须迁成功）。
    with src_engine.connect() as src:
        for tname in tables:
            table = md.tables[tname]
            rows = [dict(r._mapping) for r in src.execute(select(table))]
            if not rows:
                counts[tname] = 0
                continue
            counts[tname] = _insert_rows(dst_engine, table, rows)
    return counts


def _insert_rows(dst_engine, table, rows) -> int:
    """整批 ON CONFLICT DO NOTHING 插入；失败则逐行插入跳过坏行。返回成功行数。"""
    ok = 0
    try:
        with dst_engine.begin() as dst:
            dst.execute(sqlite_insert(table).on_conflict_do_nothing(), rows)
        return len(rows)
    except Exception as exc:
        logger.warning("[PG迁移] 表 {} 整批插入失败，改逐行跳过坏行: {}", table.name, exc)
    for row in rows:
        try:
            with dst_engine.begin() as dst:
                dst.execute(sqlite_insert(table).on_conflict_do_nothing(), [row])
            ok += 1
        except Exception as exc:
            logger.warning("[PG迁移] 表 {} 跳过一行（{}）: {}", table.name, row.get("id"), exc)
    return ok


def migrate_if_needed(pg_url: str | None = None) -> str:
    """老库升级入口。返回状态字符串。绝不抛异常（迁移失败不阻塞启动）。"""
    try:
        if MARKER_PATH.exists():
            return "already_migrated"

        if not PGDATA_DIR.exists():
            # 全新安装：无 PG 数据，直接标记跳过
            MARKER_PATH.parent.mkdir(parents=True, exist_ok=True)
            MARKER_PATH.write_text("fresh_install\n", encoding="utf-8")
            return "fresh_install_skip"

        # 老装机升级：连接老版「捆绑 PG」拷数据。
        # 用固定 DSN（与 launcher 的 DB_NAME/PORT/USER + trust 鉴权一致），
        # 不用 settings.database_url——避免用户 .env 覆盖把迁移指向错误/空库导致静默丢数据。
        from sqlalchemy import create_engine
        from recruitment_assistant.storage.resume_db import resume_engine, init_resume_database

        init_resume_database()  # 确保 SQLite 侧 18 张表就绪

        # 迁移前备份 SQLite
        try:
            from recruitment_assistant.services.backup_service import backup_resume_db
            bak = backup_resume_db()
            logger.info("[PG迁移] 迁移前已备份 SQLite: {}", bak)
        except Exception as exc:
            logger.warning("[PG迁移] 备份 SQLite 失败（继续迁移）: {}", exc)

        url = pg_url or "postgresql+psycopg://postgres@localhost:5432/recruitment_assistant"
        src_engine = create_engine(url, pool_pre_ping=True)
        try:
            counts = copy_tables(src_engine, resume_engine)
        finally:
            src_engine.dispose()

        MARKER_PATH.write_text(f"migrated {counts}\n", encoding="utf-8")
        logger.info("[PG迁移] 完成，各表拷贝行数: {}", counts)
        return f"migrated:{counts}"
    except Exception as exc:
        # 连不上 PG / 其他错误：不写 marker，下次启动再试；绝不阻塞启动
        logger.error("[PG迁移] 失败（不阻塞启动，下次重试）: {}", exc)
        return f"failed:{exc}"
