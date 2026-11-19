from __future__ import annotations

import asyncio
import os
import urllib.parse
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

config = getattr(context, "config", None)

if config is not None and config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = None


def get_database_url() -> str:
    """Dynamically resolve asyncpg connection URL from environment variables."""
    raw_url = os.environ.get("DATABASE_URL") or os.environ.get("POSTGRES_DSN")
    if raw_url:
        if raw_url.startswith("postgresql://"):
            return raw_url.replace("postgresql://", "postgresql+asyncpg://", 1)
        if raw_url.startswith("postgres://"):
            return raw_url.replace("postgres://", "postgresql+asyncpg://", 1)
        return raw_url

    host = (os.environ.get("POSTGRES_HOST") or "localhost").strip()
    port = (os.environ.get("POSTGRES_PORT") or "5432").strip()
    user = (os.environ.get("POSTGRES_USER") or "postgres").strip()
    password = (os.environ.get("POSTGRES_PASSWORD") or "postgres").strip()
    db = (os.environ.get("POSTGRES_DB") or "postgres").strip()

    quoted_user = urllib.parse.quote_plus(user)
    quoted_password = urllib.parse.quote_plus(password)
    return f"postgresql+asyncpg://{quoted_user}:{quoted_password}@{host}:{port}/{db}"


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode emitting SQL statements."""
    url = get_database_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)

    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """Run migrations in 'online' mode using async SQLAlchemy engine."""
    cfg = config or context.config
    configuration = cfg.get_section(cfg.config_ini_section, {})
    configuration["sqlalchemy.url"] = get_database_url()

    connectable = async_engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode."""
    asyncio.run(run_async_migrations())


if config is not None:
    if context.is_offline_mode():
        run_migrations_offline()
    else:
        run_migrations_online()
