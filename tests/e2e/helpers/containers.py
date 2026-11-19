"""Backing service containers, SQL migrations, and store flush helpers."""

from __future__ import annotations

import asyncio
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse


@dataclass(frozen=True, slots=True)
class PostgresConfig:
    """Connection parameters for the E2E PostgreSQL instance."""

    host: str
    port: int
    user: str
    password: str
    database: str

    @property
    def dsn(self) -> str:
        """Return standard ``postgresql://`` DSN for asyncpg/psycopg."""
        return (
            f"postgresql://{self.user}:{self.password}"
            f"@{self.host}:{self.port}/{self.database}"
        )

    @property
    def sqlalchemy_dsn(self) -> str:
        """Return SQLAlchemy asyncpg DSN."""
        return (
            f"postgresql+asyncpg://{self.user}:{self.password}"
            f"@{self.host}:{self.port}/{self.database}"
        )


DEFAULT_MONGO_DB = "e2e_test"


def mongo_direct_connection_uri(
    uri: str,
    *,
    default_db: str = DEFAULT_MONGO_DB,
) -> str:
    """Ensure ``directConnection=true`` and a database path on a MongoDB URI."""
    parsed = urlparse(uri)
    path = parsed.path
    if path in ("", "/"):
        path = f"/{default_db.lstrip('/')}"
    params = parse_qsl(parsed.query, keep_blank_values=True)
    if not any(k.lower() == "directconnection" for k, _ in params):
        params.append(("directConnection", "true"))
    return urlunparse(parsed._replace(path=path, query=urlencode(params)))


def _parse_postgres_dsn(dsn: str) -> PostgresConfig:
    parsed = urlparse(dsn)
    return PostgresConfig(
        host=parsed.hostname or "127.0.0.1",
        port=parsed.port or 5432,
        user=parsed.username or "postgres",
        password=parsed.password or "postgres",
        database=(parsed.path.lstrip("/") or "e2e_test"),
    )


def _postgres_from_env() -> PostgresConfig | None:
    dsn = os.environ.get("E2E_POSTGRES_DSN", "").strip()
    if dsn:
        return _parse_postgres_dsn(dsn)
    host = os.environ.get("E2E_POSTGRES_HOST", "").strip()
    if not host:
        return None
    return PostgresConfig(
        host=host,
        port=int(os.environ.get("E2E_POSTGRES_PORT", "5432")),
        user=os.environ.get("E2E_POSTGRES_USER", "postgres"),
        password=os.environ.get("E2E_POSTGRES_PASSWORD", "postgres"),
        database=os.environ.get("E2E_POSTGRES_DB", "e2e_test"),
    )


def _redis_from_env() -> tuple[str, int] | None:
    url = os.environ.get("E2E_REDIS_URL", "").strip()
    if url:
        parsed = urlparse(url)
        return (parsed.hostname or "127.0.0.1", parsed.port or 6379)
    host = os.environ.get("E2E_REDIS_HOST", "").strip()
    if not host:
        return None
    return (host, int(os.environ.get("E2E_REDIS_PORT", "6379")))


def _wait_for_mongo_primary(uri: str, timeout_sec: float = 180.0) -> None:
    try:
        import pymongo
    except ImportError:
        return
    deadline = time.monotonic() + timeout_sec
    last_err: Exception | None = None
    while time.monotonic() < deadline:
        client: Any = None
        try:
            client = pymongo.MongoClient(uri, serverSelectionTimeoutMS=2000)
            info = client.admin.command("hello")
            if info.get("isWritablePrimary") or info.get("ismaster"):
                return
        except Exception as exc:
            last_err = exc
        finally:
            if client is not None:
                client.close()
        time.sleep(0.5)
    msg = f"MongoDB did not become writable primary within {timeout_sec}s: {last_err}"
    raise RuntimeError(msg)


def _start_postgres_container(started: list[Any]) -> PostgresConfig:
    try:
        from testcontainers.postgres import PostgresContainer
    except ImportError as exc:
        msg = (
            "Set E2E_POSTGRES_* / E2E_POSTGRES_DSN or install 'testcontainers' "
            "with Docker running"
        )
        raise RuntimeError(msg) from exc
    try:
        container = PostgresContainer("postgres:17-alpine")
        container.start()
        started.append(container)
        return PostgresConfig(
            host=str(container.get_container_host_ip()),
            port=int(container.get_exposed_port(5432)),
            user=str(container.username),
            password=str(container.password),
            database=str(container.dbname),
        )
    except Exception as exc:
        msg = (
            f"Failed to start PostgreSQL testcontainer ({exc}). "
            "Set E2E_POSTGRES_* or start Docker."
        )
        raise RuntimeError(msg) from exc


def _start_mongo_container(started: list[Any]) -> str:
    try:
        from testcontainers.mongodb import MongoDbContainer
    except ImportError as exc:
        msg = "Set E2E_MONGO_URI or install 'testcontainers' with Docker running"
        raise RuntimeError(msg) from exc
    try:
        container = MongoDbContainer("mongo:8.0")
        container.start()
        started.append(container)
        raw_uri = str(container.get_connection_url())
        uri = mongo_direct_connection_uri(raw_uri)
        _wait_for_mongo_primary(uri, timeout_sec=180.0)
        return uri
    except Exception as exc:
        msg = (
            f"Failed to start MongoDB testcontainer ({exc}). "
            "Set E2E_MONGO_URI or start Docker."
        )
        raise RuntimeError(msg) from exc


def _start_redis_container(started: list[Any]) -> tuple[str, int]:
    try:
        from testcontainers.redis import RedisContainer
    except ImportError as exc:
        msg = (
            "Set E2E_REDIS_HOST/E2E_REDIS_PORT or install 'testcontainers' "
            "with Docker running"
        )
        raise RuntimeError(msg) from exc
    try:
        container = RedisContainer("redis:7-alpine")
        container.start()
        started.append(container)
        return (
            str(container.get_container_host_ip()),
            int(container.get_exposed_port(6379)),
        )
    except Exception as exc:
        msg = (
            f"Failed to start Redis testcontainer ({exc}). "
            "Set E2E_REDIS_* or start Docker."
        )
        raise RuntimeError(msg) from exc


@dataclass(slots=True)
class BackingServices:
    """Live E2E Postgres, MongoDB, and Redis endpoints and container lifecycle."""

    postgres: PostgresConfig
    mongo_uri: str
    redis_host: str
    redis_port: int
    _containers: list[Any] = field(default_factory=list, repr=False)

    @property
    def postgres_host(self) -> str:
        """PostgreSQL hostname."""
        return self.postgres.host

    @property
    def postgres_port(self) -> int:
        """PostgreSQL port."""
        return self.postgres.port

    @property
    def postgres_user(self) -> str:
        """PostgreSQL username."""
        return self.postgres.user

    @property
    def postgres_password(self) -> str:
        """PostgreSQL password."""
        return self.postgres.password

    @property
    def postgres_db(self) -> str:
        """PostgreSQL database name."""
        return self.postgres.database

    @property
    def postgres_dsn(self) -> str:
        """Standard ``postgresql://`` DSN."""
        return self.postgres.dsn

    @property
    def sqlalchemy_postgres_dsn(self) -> str:
        """SQLAlchemy ``postgresql+asyncpg://`` DSN."""
        return self.postgres.sqlalchemy_dsn

    @property
    def redis_url(self) -> str:
        """Standard ``redis://`` URL."""
        return f"redis://{self.redis_host}:{self.redis_port}"

    @classmethod
    def start(
        cls,
        *,
        enable_postgres: bool = True,
        enable_mongo: bool = True,
        enable_redis: bool = True,
    ) -> BackingServices:
        """Resolve ``E2E_*`` env endpoints or start Testcontainers lazily."""
        started: list[Any] = []
        try:
            pg = _postgres_from_env()
            if pg is None:
                if enable_postgres:
                    pg = _start_postgres_container(started)
                else:
                    pg = PostgresConfig(
                        "127.0.0.1",
                        5432,
                        "postgres",
                        "postgres",
                        "e2e_test",
                    )

            mongo_env = os.environ.get("E2E_MONGO_URI", "").strip()
            if mongo_env:
                mongo_uri = mongo_direct_connection_uri(mongo_env)
            elif enable_mongo:
                mongo_uri = _start_mongo_container(started)
            else:
                mongo_uri = "mongodb://127.0.0.1:27017/e2e_test?directConnection=true"

            redis_env = _redis_from_env()
            if redis_env is not None:
                redis_host, redis_port = redis_env
            elif enable_redis:
                redis_host, redis_port = _start_redis_container(started)
            else:
                redis_host, redis_port = ("127.0.0.1", 6379)

            return cls(
                postgres=pg,
                mongo_uri=mongo_uri,
                redis_host=redis_host,
                redis_port=redis_port,
                _containers=started,
            )
        except Exception:
            for container in reversed(started):
                try:
                    container.stop()
                except Exception:  # noqa: S110
                    pass
            raise

    def stop(self) -> None:
        """Stop any Testcontainers started by this instance."""
        for container in reversed(self._containers):
            try:
                container.stop()
            except Exception:  # noqa: S110
                pass
        self._containers.clear()

    def __enter__(self) -> BackingServices:
        return self

    def __exit__(self, *exc: object) -> None:
        self.stop()

    def env_overrides(self) -> dict[str, str]:
        """Return app environment variable overrides for these services."""
        return {
            "MONGO_URI": self.mongo_uri,
            "REDIS_HOST": self.redis_host,
            "REDIS_PORT": str(self.redis_port),
            "POSTGRES_HOST": self.postgres.host,
            "POSTGRES_PORT": str(self.postgres.port),
            "POSTGRES_USER": self.postgres.user,
            "POSTGRES_PASSWORD": self.postgres.password,
            "POSTGRES_DB": self.postgres.database,
        }


def resolve_project_root() -> Path:
    """Return repository root relative to ``tests/e2e/helpers/containers.py``."""
    return Path(__file__).resolve().parents[3]


def resolve_migrations_dir(project_root: Path | None = None) -> Path:
    """Locate Postgres SQL migrations in workspace or ``.reference_nestjs``."""
    root = project_root or resolve_project_root()
    primary = root / "migrations" / "postgres"
    if primary.is_dir():
        return primary
    fallback = root / ".reference_nestjs" / "migrations" / "postgres"
    if fallback.is_dir():
        return fallback
    msg = f"Postgres migrations directory not found under {root}"
    raise FileNotFoundError(msg)


def _split_sql_statements(sql_text: str) -> list[str]:
    statements: list[str] = []
    buf: list[str] = []
    in_single_quote = False
    in_line_comment = False
    i = 0
    n = len(sql_text)
    while i < n:
        ch = sql_text[i]
        nxt = sql_text[i + 1] if i + 1 < n else ""
        if in_line_comment:
            buf.append(ch)
            if ch == "\n":
                in_line_comment = False
        elif in_single_quote:
            buf.append(ch)
            if ch == "'" and nxt == "'":
                buf.append(nxt)
                i += 1
            elif ch == "'":
                in_single_quote = False
        elif ch == "-" and nxt == "-":
            buf.append(ch)
            buf.append(nxt)
            in_line_comment = True
            i += 1
        elif ch == "'":
            buf.append(ch)
            in_single_quote = True
        elif ch == ";":
            stmt = "".join(buf).strip()
            if stmt:
                statements.append(stmt)
            buf.clear()
        else:
            buf.append(ch)
        i += 1
    tail = "".join(buf).strip()
    if tail:
        statements.append(tail)
    return statements


def _as_postgres_config(target: PostgresConfig | BackingServices) -> PostgresConfig:
    if isinstance(target, PostgresConfig):
        return target
    return target.postgres


def _strip_leading_sql_comments(sql: str) -> str:
    i = 0
    n = len(sql)
    while i < n:
        if sql[i].isspace():
            i += 1
            continue
        if sql[i : i + 2] == "--":
            newline = sql.find("\n", i + 2)
            if newline == -1:
                return ""
            i = newline + 1
            continue
        if sql[i : i + 2] == "/*":
            end = sql.find("*/", i + 2)
            if end == -1:
                return ""
            i = end + 2
            continue
        break
    return sql[i:]


async def _exec_migration_statements(conn: Any, sql_text: str) -> None:
    for stmt in _split_sql_statements(sql_text):
        stripped = _strip_leading_sql_comments(stmt)
        if not stripped:
            continue
        upper = stripped.upper()
        try:
            await conn.execute(stmt)
        except Exception as exc:
            sqlstate = getattr(exc, "sqlstate", None)
            if upper.startswith("CREATE TYPE") and sqlstate == "42710":
                continue
            raise


async def apply_postgres_migrations(
    postgres: PostgresConfig | BackingServices,
    migrations_dir: Path | None = None,
    *,
    reset: bool = False,
) -> list[str]:
    """Apply Postgres ``migrations/postgres/*.sql`` idempotently."""
    import asyncpg

    cfg = _as_postgres_config(postgres)
    mdir = migrations_dir or resolve_migrations_dir()
    down_files = sorted(mdir.glob("*.down.sql"), reverse=True)
    up_files = sorted(p for p in mdir.glob("*.sql") if not p.name.endswith(".down.sql"))

    applied: list[str] = []
    conn = await asyncpg.connect(cfg.dsn)
    try:
        if reset:
            for down_file in down_files:
                await _exec_migration_statements(conn, down_file.read_text("utf-8"))
        for up_file in up_files:
            await _exec_migration_statements(conn, up_file.read_text("utf-8"))
            applied.append(up_file.name)
    finally:
        await conn.close()
    return applied


def apply_postgres_migrations_sync(
    postgres: PostgresConfig | BackingServices,
    migrations_dir: Path | None = None,
    *,
    reset: bool = False,
) -> list[str]:
    """Synchronous wrapper for :func:`apply_postgres_migrations`."""
    return asyncio.run(apply_postgres_migrations(postgres, migrations_dir, reset=reset))


async def flush_postgres(target: PostgresConfig | BackingServices) -> None:
    """Truncate known E2E Postgres tables if present."""
    import asyncpg

    cfg = _as_postgres_config(target)
    conn = await asyncpg.connect(cfg.dsn)
    try:
        rows = await conn.fetch(
            "SELECT tablename FROM pg_tables "
            "WHERE schemaname = 'public' "
            "AND tablename = ANY($1::text[])",
            ["background_jobs", "country_access"],
        )
        tables = [str(r["tablename"]) for r in rows]
        if tables:
            joined = ", ".join(tables)
            await conn.execute(f"TRUNCATE TABLE {joined} RESTART IDENTITY CASCADE")
    finally:
        await conn.close()


async def flush_mongo(mongo_uri: str, db_name: str | None = None) -> None:
    """Drop all non-system collections in the target MongoDB database."""
    from motor.motor_asyncio import AsyncIOMotorClient

    client: Any = AsyncIOMotorClient(mongo_uri, serverSelectionTimeoutMS=3000)
    try:
        parsed_db = urlparse(mongo_uri).path.lstrip("/")
        target_db_name = db_name or parsed_db or DEFAULT_MONGO_DB
        db = client[target_db_name]
        names = await db.list_collection_names()
        for name in names:
            if not name.startswith("system."):
                await db.drop_collection(name)
    finally:
        client.close()


async def flush_redis(host: str, port: int) -> None:
    """Flush the active Redis database via ``FLUSHDB``."""
    import redis.asyncio as aioredis

    client = aioredis.Redis(host=host, port=port)
    try:
        await client.flushdb()
    finally:
        await client.aclose()


async def flush_all(services: BackingServices) -> None:
    """Clean PostgreSQL, MongoDB, and Redis state for test isolation."""
    await asyncio.gather(
        flush_postgres(services.postgres),
        flush_mongo(services.mongo_uri),
        flush_redis(services.redis_host, services.redis_port),
    )


def flush_all_sync(services: BackingServices) -> None:
    """Synchronous wrapper for :func:`flush_all`."""
    asyncio.run(flush_all(services))


__all__ = [
    "BackingServices",
    "PostgresConfig",
    "apply_postgres_migrations",
    "apply_postgres_migrations_sync",
    "flush_all",
    "flush_all_sync",
    "flush_mongo",
    "flush_postgres",
    "flush_redis",
    "mongo_direct_connection_uri",
    "resolve_migrations_dir",
    "resolve_project_root",
]
