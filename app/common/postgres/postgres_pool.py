from __future__ import annotations

import asyncio
import os
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Final

import asyncpg

from app.common.config.parsers import parse_positive_int
from app.core.logger.logger_config import get_logger

DEFAULT_POSTGRES_HOST: Final[str] = "localhost"
DEFAULT_POSTGRES_PORT: Final[int] = 5432
DEFAULT_CONNECTION_TIMEOUT_MS: Final[int] = 2_000
DEFAULT_IDLE_TIMEOUT_MS: Final[int] = 30_000


@dataclass(frozen=True, slots=True)
class PostgresPoolOptions:
    """Configuration options for PostgresPool matching NestJS pg.Pool options."""

    host: str = DEFAULT_POSTGRES_HOST
    port: int = DEFAULT_POSTGRES_PORT
    user: str | None = None
    password: str | None = None
    database: str | None = None
    min_size: int = 0
    max_size: int = 10
    connection_timeout_ms: int = DEFAULT_CONNECTION_TIMEOUT_MS
    idle_timeout_ms: int = DEFAULT_IDLE_TIMEOUT_MS

    @property
    def dsn(self) -> str:
        user_part = self.user or ""
        auth_part = (
            f"{user_part}:{self.password}@"
            if self.password is not None and user_part
            else (f"{user_part}@" if user_part else "")
        )
        db_part = self.database or ""
        return f"postgresql://{auth_part}{self.host}:{self.port}/{db_part}"

    @classmethod
    def from_env(
        cls,
        env: Mapping[str, str | None] | None = None,
        *,
        max_size: int = 10,
        connection_timeout_ms: int = DEFAULT_CONNECTION_TIMEOUT_MS,
        idle_timeout_ms: int = DEFAULT_IDLE_TIMEOUT_MS,
    ) -> PostgresPoolOptions:
        target = os.environ if env is None else env
        raw_host = target.get("POSTGRES_HOST")
        host = (
            raw_host.strip()
            if raw_host is not None and raw_host.strip()
            else DEFAULT_POSTGRES_HOST
        )
        port = parse_positive_int(target.get("POSTGRES_PORT"), DEFAULT_POSTGRES_PORT)
        raw_user = target.get("POSTGRES_USER")
        user = raw_user.strip() if raw_user is not None and raw_user.strip() else None
        password = target.get("POSTGRES_PASSWORD")
        raw_db = target.get("POSTGRES_DB")
        database = raw_db.strip() if raw_db is not None and raw_db.strip() else None
        return cls(
            host=host,
            port=port,
            user=user,
            password=password,
            database=database,
            min_size=0,
            max_size=max_size,
            connection_timeout_ms=connection_timeout_ms,
            idle_timeout_ms=idle_timeout_ms,
        )


@dataclass(frozen=True, slots=True)
class QueryResult:
    """pg.Pool-compatible query result wrapper exposing `.rows`."""

    rows: list[dict[str, Any]]
    row_count: int


class PostgresPool:
    """Async PostgreSQL connection pool wrapper over asyncpg with lazy init."""

    def __init__(
        self,
        options: PostgresPoolOptions | None = None,
        *,
        env: Mapping[str, str | None] | None = None,
        pool_factory: Any = None,
    ) -> None:
        self.options = options or PostgresPoolOptions.from_env(env)
        self._pool: Any = None
        self._pool_lock = asyncio.Lock()
        self._pool_factory = pool_factory or asyncpg.create_pool
        self._closed = False
        self._logger = get_logger("PostgresPool")

    @property
    def is_initialized(self) -> bool:
        return self._pool is not None

    @property
    def is_closed(self) -> bool:
        return self._closed

    async def get_pool(self) -> Any:
        if self._closed:
            raise RuntimeError("PostgresPool has been closed")
        if self._pool is not None:
            return self._pool
        async with self._pool_lock:
            if self._closed:
                raise RuntimeError("PostgresPool has been closed")
            if self._pool is None:
                timeout_sec = max(0.001, self.options.connection_timeout_ms / 1000.0)
                idle_sec = max(
                    1.0,
                    self.options.idle_timeout_ms / 1000.0,
                )
                self._pool = await self._pool_factory(
                    host=self.options.host,
                    port=self.options.port,
                    user=self.options.user,
                    password=self.options.password,
                    database=self.options.database,
                    min_size=self.options.min_size,
                    max_size=max(1, self.options.max_size),
                    timeout=timeout_sec,
                    command_timeout=timeout_sec,
                    max_inactive_connection_lifetime=idle_sec,
                )
            return self._pool

    async def fetch(
        self,
        sql: str,
        *args: Any,
        query_timeout: float | None = None,
    ) -> list[dict[str, Any]]:
        pool = await self.get_pool()
        records = await pool.fetch(sql, *args, timeout=query_timeout)
        return [dict(r) for r in records]

    async def fetchrow(
        self,
        sql: str,
        *args: Any,
        query_timeout: float | None = None,
    ) -> dict[str, Any] | None:
        pool = await self.get_pool()
        record = await pool.fetchrow(sql, *args, timeout=query_timeout)
        return dict(record) if record is not None else None

    async def fetchval(
        self,
        sql: str,
        *args: Any,
        column: int = 0,
        query_timeout: float | None = None,
    ) -> Any:
        pool = await self.get_pool()
        return await pool.fetchval(sql, *args, column=column, timeout=query_timeout)

    async def execute(
        self,
        sql: str,
        *args: Any,
        query_timeout: float | None = None,
    ) -> str:
        pool = await self.get_pool()
        result = await pool.execute(sql, *args, timeout=query_timeout)
        return str(result)

    async def query(
        self,
        sql: str,
        params: list[Any] | tuple[Any, ...] | None = None,
        *,
        query_timeout: float | None = None,
    ) -> QueryResult:
        """pg.Pool-compatible `.query(sql, params)` returning `QueryResult`."""
        args = tuple(params) if params is not None else ()
        rows = await self.fetch(sql, *args, query_timeout=query_timeout)
        return QueryResult(rows=rows, row_count=len(rows))

    async def ping(self) -> bool:
        """Execute `SELECT 1` to check database connectivity."""
        await self.fetchval("SELECT 1")
        return True

    async def close(self) -> None:
        """Close the connection pool cleanly without raising on teardown failure."""
        self._closed = True
        pool = self._pool
        self._pool = None
        if pool is None:
            return
        try:
            close_fn = getattr(pool, "close", None) or getattr(pool, "end", None)
            if callable(close_fn):
                res = close_fn()
                if asyncio.iscoroutine(res):
                    await asyncio.wait_for(res, timeout=2.0)
        except Exception as exc:
            self._logger.warn(f"Failed to close Postgres pool: {exc}")
            terminate_fn = getattr(pool, "terminate", None)
            if callable(terminate_fn):
                try:
                    terminate_fn()
                except Exception:  # noqa: S110
                    pass

    async def end(self) -> None:
        """Alias for `close()` matching Node `pg.Pool.end()`."""
        await self.close()

    async def on_module_destroy(self) -> None:
        """Lifecycle hook matching NestJS `OnModuleDestroy`."""
        await self.close()


def create_postgres_pool(
    options: PostgresPoolOptions | Mapping[str, Any] | None = None,
    *,
    env: Mapping[str, str | None] | None = None,
    max_size: int = 10,
    connection_timeout_ms: int = 3_000,
    idle_timeout_ms: int = DEFAULT_IDLE_TIMEOUT_MS,
) -> PostgresPool:
    """Create a configured PostgresPool instance."""
    if isinstance(options, PostgresPoolOptions):
        return PostgresPool(options)
    if isinstance(options, Mapping):
        resolved = PostgresPoolOptions(
            host=str(options.get("host") or DEFAULT_POSTGRES_HOST),
            port=int(options.get("port") or DEFAULT_POSTGRES_PORT),
            user=(str(options["user"]) if options.get("user") is not None else None),
            password=(
                str(options["password"])
                if options.get("password") is not None
                else None
            ),
            database=(
                str(options["database"])
                if options.get("database") is not None
                else None
            ),
            max_size=int(options.get("max_size") or max_size),
            connection_timeout_ms=int(
                options.get("connection_timeout_ms") or connection_timeout_ms
            ),
            idle_timeout_ms=int(options.get("idle_timeout_ms") or idle_timeout_ms),
        )
        return PostgresPool(resolved)
    return PostgresPool(
        PostgresPoolOptions.from_env(
            env,
            max_size=max_size,
            connection_timeout_ms=connection_timeout_ms,
            idle_timeout_ms=idle_timeout_ms,
        )
    )


__all__ = [
    "DEFAULT_CONNECTION_TIMEOUT_MS",
    "DEFAULT_IDLE_TIMEOUT_MS",
    "DEFAULT_POSTGRES_HOST",
    "DEFAULT_POSTGRES_PORT",
    "PostgresPool",
    "PostgresPoolOptions",
    "QueryResult",
    "create_postgres_pool",
]
