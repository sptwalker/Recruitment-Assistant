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


def init_database() -> None:
    ensure_database_exists()
    import recruitment_assistant.storage.models  # noqa: F401

    Base.metadata.create_all(bind=engine)


def get_session() -> Generator[Session, None, None]:
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


def create_session() -> Session:
    return SessionLocal()
