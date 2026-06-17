from collections.abc import Generator

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from recruitment_assistant.config.settings import get_settings


class Base(DeclarativeBase):
    pass


settings = get_settings()
engine = create_engine(settings.database_url, pool_pre_ping=True, future=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


def ensure_database_exists() -> None:
    """Create the configured PostgreSQL database when the server exists but DB is missing."""
    url = make_url(settings.database_url)
    database_name = url.database
    if not database_name or url.get_backend_name() != "postgresql":
        return

    try:
        with engine.connect():
            return
    except OperationalError as exc:
        if database_name not in str(exc):
            raise

    admin_url = url.set(database="postgres")
    admin_engine = create_engine(admin_url, isolation_level="AUTOCOMMIT", future=True)
    try:
        with admin_engine.connect() as conn:
            exists = conn.execute(
                text("SELECT 1 FROM pg_database WHERE datname = :database_name"),
                {"database_name": database_name},
            ).scalar()
            if not exists:
                safe_database_name = database_name.replace('"', '""')
                conn.execute(text(f'CREATE DATABASE "{safe_database_name}"'))
    finally:
        admin_engine.dispose()


def _ensure_boss_candidate_record_columns() -> None:
    """启动时幂等地为 boss_candidate_record 表补齐 talking_position 列。

    通过 information_schema.columns（PostgreSQL）或 PRAGMA table_info（SQLite）
    检查列存在，避免重复 ALTER。所有依赖于该列的 UI/服务层在表/列尚未创建时不应假定其存在。
    """
    backend = engine.dialect.name
    table_name = "boss_candidate_record"
    column_name = "talking_position"
    column_type = "VARCHAR(64)"

    try:
        with engine.begin() as conn:
            if backend == "postgresql":
                exists = conn.execute(
                    text(
                        "SELECT 1 FROM information_schema.columns "
                        "WHERE table_name = :table_name AND column_name = :column_name"
                    ),
                    {"table_name": table_name, "column_name": column_name},
                ).scalar()
                if exists:
                    return
                conn.execute(
                    text(f"ALTER TABLE {table_name} ADD COLUMN IF NOT EXISTS {column_name} {column_type}")
                )
            elif backend == "sqlite":
                rows = conn.execute(text(f"PRAGMA table_info('{table_name}')")).all()
                if any(row[1] == column_name for row in rows):
                    return
                conn.execute(
                    text(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_type}")
                )
            else:
                # 其他后端尝试通用 SQL，失败时静默跳过
                try:
                    conn.execute(
                        text(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_type}")
                    )
                except Exception:
                    pass
    except Exception:
        # 启动迁移失败不应阻塞应用启动；首次运行时表可能尚不存在，此时 create_all 已包含新列。
        pass


def _migrate_job_position_schema() -> None:
    """幂等迁移：将旧版 job_position 列名重命名为当前模型所需名称。

    旧 initial_schema 使用 job_name / city / degree_requirement / salary_min+max /
    experience_min+max_years；当前模型使用 title / work_city / min_education /
    salary_range / min_experience + responsibilities / job_requirements。
    覆盖升级安装场景：pgdata 保留旧表，create_all() 不会 ALTER。
    """
    if engine.dialect.name != "postgresql":
        return
    try:
        with engine.begin() as conn:
            has_old = conn.execute(
                text(
                    "SELECT 1 FROM information_schema.columns "
                    "WHERE table_name = 'job_position' AND column_name = 'job_name'"
                )
            ).scalar()
            if not has_old:
                return

            conn.execute(text("ALTER TABLE job_position RENAME COLUMN job_name TO title"))
            conn.execute(text("ALTER TABLE job_position RENAME COLUMN city TO work_city"))
            conn.execute(text("ALTER TABLE job_position RENAME COLUMN degree_requirement TO min_education"))

            conn.execute(text("ALTER TABLE job_position ADD COLUMN salary_range VARCHAR(100)"))
            conn.execute(text(
                "UPDATE job_position SET salary_range = "
                "CASE WHEN salary_min IS NOT NULL AND salary_max IS NOT NULL "
                "THEN salary_min::text || '-' || salary_max::text "
                "WHEN salary_min IS NOT NULL THEN '>=' || salary_min::text "
                "WHEN salary_max IS NOT NULL THEN '<=' || salary_max::text "
                "ELSE NULL END"
            ))
            conn.execute(text("ALTER TABLE job_position DROP COLUMN IF EXISTS salary_min"))
            conn.execute(text("ALTER TABLE job_position DROP COLUMN IF EXISTS salary_max"))

            conn.execute(text("ALTER TABLE job_position ADD COLUMN min_experience VARCHAR(64)"))
            conn.execute(text(
                "UPDATE job_position SET min_experience = "
                "CASE WHEN experience_min_years IS NOT NULL AND experience_max_years IS NOT NULL "
                "THEN experience_min_years::text || '-' || experience_max_years::text || '年' "
                "WHEN experience_min_years IS NOT NULL "
                "THEN experience_min_years::text || '年以上' "
                "WHEN experience_max_years IS NOT NULL "
                "THEN experience_max_years::text || '年以内' "
                "ELSE NULL END"
            ))
            conn.execute(text("ALTER TABLE job_position DROP COLUMN IF EXISTS experience_min_years"))
            conn.execute(text("ALTER TABLE job_position DROP COLUMN IF EXISTS experience_max_years"))

            conn.execute(text("ALTER TABLE job_position ADD COLUMN IF NOT EXISTS responsibilities TEXT"))
            conn.execute(text("ALTER TABLE job_position ADD COLUMN IF NOT EXISTS job_requirements TEXT"))
    except Exception:
        pass


def init_database() -> None:
    ensure_database_exists()
    import recruitment_assistant.storage.models  # noqa: F401

    Base.metadata.create_all(bind=engine)
    _migrate_job_position_schema()
    _ensure_boss_candidate_record_columns()


def get_session() -> Generator[Session, None, None]:
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


def create_session() -> Session:
    return SessionLocal()
