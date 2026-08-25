"""Alembic async migration environment.

Reads DATABASE_URL from the app settings so there is a single source of truth.
All domain models must be imported below so that Alembic can discover them via
Base.metadata.
"""

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from backend.config import settings
from backend.models import Base  # noqa: F401 — registers the metadata

# ── Import every model module so Alembic sees all tables ─────────────────────
from backend.auth import models as _auth_models  # noqa: F401

# ─────────────────────────────────────────────────────────────────────────────
config = context.config

# Override the sqlalchemy.url from the app settings (single source of truth)
config.set_main_option("sqlalchemy.url", str(settings.DATABASE_URL))

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata

_is_sqlite = "sqlite" in str(settings.DATABASE_URL)


# ── Offline (no DB connection, emit SQL to stdout) ────────────────────────────

def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=_is_sqlite,
    )
    with context.begin_transaction():
        context.run_migrations()


# ── Online (connect to a real DB) ─────────────────────────────────────────────

def do_run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        # SQLite does not support ALTER TABLE natively; batch mode rewrites tables.
        render_as_batch=_is_sqlite,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
