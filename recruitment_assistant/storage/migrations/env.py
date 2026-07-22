from logging.config import fileConfig
import os

from alembic import context
from sqlalchemy import engine_from_config, pool

# M1 后：统一到单一 SQLite 库。alembic 目标 = 统一 metadata（含候选人 PII + 岗位/采集），
# URL 指向 SQLite。导入两套模型以注册全部表。
from recruitment_assistant.storage.db import Base
from recruitment_assistant.storage import models  # noqa: F401
from recruitment_assistant.storage import resume_models  # noqa: F401
from recruitment_assistant.storage import auth_models  # noqa: F401
from recruitment_assistant.storage.resume_db import resolve_db_url

config = context.config
# ALEMBIC_DB_URL 覆盖（autogenerate 指向空库 / CI 临时库）；否则用 DATABASE_URL 或本地 SQLite。
db_url = os.environ.get("ALEMBIC_DB_URL") or resolve_db_url()
config.set_main_option("sqlalchemy.url", db_url)

# batch 模式仅 SQLite 需要（做 ALTER）；PG 原生支持 ALTER，关掉以免遮蔽 PG 原生迁移操作。
_use_batch = db_url.startswith("sqlite")

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url, target_metadata=target_metadata, literal_binds=True,
        render_as_batch=_use_batch,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection, target_metadata=target_metadata,
            render_as_batch=_use_batch,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
