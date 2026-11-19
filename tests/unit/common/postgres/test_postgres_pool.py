from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from app.common.postgres import (
    DEFAULT_CONNECTION_TIMEOUT_MS,
    DEFAULT_IDLE_TIMEOUT_MS,
    PostgresMigrator,
    PostgresPool,
    PostgresPoolOptions,
    create_postgres_pool,
    resolve_default_migrations_dir,
    split_sql_statements,
)


class _FakeAsyncpgPool:
    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs
        self.closed = False
        self.executed: list[tuple[str, tuple[Any, ...]]] = []
        self.rows: list[dict[str, Any]] = [{"id": 1, "code": "US"}]

    async def fetch(
        self,
        sql: str,
        *args: Any,
        **_kwargs: Any,
    ) -> list[dict[str, Any]]:
        self.executed.append((sql, args))
        return list(self.rows)

    async def fetchrow(
        self,
        sql: str,
        *args: Any,
        **_kwargs: Any,
    ) -> dict[str, Any] | None:
        self.executed.append((sql, args))
        return self.rows[0] if self.rows else None

    async def fetchval(
        self,
        sql: str,
        *args: Any,
        column: int = 0,
        **_kwargs: Any,
    ) -> Any:
        self.executed.append((sql, args))
        if not self.rows:
            return None
        vals = list(self.rows[0].values())
        return vals[column]

    async def execute(
        self,
        sql: str,
        *args: Any,
        **_kwargs: Any,
    ) -> str:
        self.executed.append((sql, args))
        return "OK"

    async def close(self) -> None:
        self.closed = True


def test_postgres_pool_options_from_env() -> None:
    assert DEFAULT_CONNECTION_TIMEOUT_MS == 2000
    assert DEFAULT_IDLE_TIMEOUT_MS == 30000

    opts = PostgresPoolOptions.from_env(
        {
            "POSTGRES_HOST": "pg.internal",
            "POSTGRES_PORT": "5433",
            "POSTGRES_USER": "app_user",
            "POSTGRES_PASSWORD": "secret",
            "POSTGRES_DB": "app_db",
        },
        max_size=5,
    )
    assert opts.host == "pg.internal"
    assert opts.port == 5433
    assert opts.user == "app_user"
    assert opts.password == "secret"
    assert opts.database == "app_db"
    assert opts.max_size == 5
    assert opts.dsn == "postgresql://app_user:secret@pg.internal:5433/app_db"


async def test_postgres_pool_query_helpers_and_close() -> None:
    created_pools: list[_FakeAsyncpgPool] = []

    async def _factory(**kwargs: Any) -> _FakeAsyncpgPool:
        p = _FakeAsyncpgPool(**kwargs)
        created_pools.append(p)
        return p

    pool = PostgresPool(
        PostgresPoolOptions(
            host="localhost",
            port=5432,
            user="postgres",
            password="pw",
            database="testdb",
        ),
        pool_factory=_factory,
    )
    assert pool.is_initialized is False

    rows = await pool.fetch("SELECT * FROM country_access WHERE enabled = $1", True)
    assert pool.is_initialized is True
    assert len(created_pools) == 1
    assert rows == [{"id": 1, "code": "US"}]

    row = await pool.fetchrow("SELECT * FROM country_access LIMIT 1")
    assert row == {"id": 1, "code": "US"}

    q_res = await pool.query(
        "SELECT * FROM country_access WHERE country_code = $1", ["US"]
    )
    assert q_res.rows == [{"id": 1, "code": "US"}]
    assert q_res.row_count == 1

    status = await pool.execute(
        "DELETE FROM country_access WHERE country_code = $1", "US"
    )
    assert status == "OK"

    assert await pool.ping() is True

    await pool.close()
    assert pool.is_closed is True
    assert created_pools[0].closed is True

    with pytest.raises(RuntimeError, match="PostgresPool has been closed"):
        await pool.fetch("SELECT 1")


def test_create_postgres_pool_factory() -> None:
    p1 = create_postgres_pool({"host": "db1", "port": 5432, "database": "d1"})
    assert p1.options.host == "db1"
    assert p1.options.database == "d1"


async def test_postgres_migrator_up_and_down_idempotency() -> None:
    migrations_dir = resolve_default_migrations_dir()
    assert migrations_dir.is_dir()

    fake_pool = _FakeAsyncpgPool()
    migrator = PostgresMigrator(fake_pool, migrations_dir=migrations_dir)

    up_files = migrator.list_up_files()
    down_files = migrator.list_down_files()
    assert [f.name for f in up_files] == [
        "001_create_country_access.sql",
        "002_create_background_jobs.sql",
    ]
    assert [f.name for f in down_files] == [
        "002_create_background_jobs.down.sql",
        "001_create_country_access.down.sql",
    ]

    applied_up = await migrator.migrate_up()
    assert applied_up == [
        "001_create_country_access.sql",
        "002_create_background_jobs.sql",
    ]
    sql_texts = [sql for sql, _ in fake_pool.executed]
    assert any("CREATE TABLE IF NOT EXISTS country_access" in s for s in sql_texts)
    assert any("CREATE TABLE IF NOT EXISTS background_jobs" in s for s in sql_texts)

    fake_pool.executed.clear()
    applied_down = await migrator.migrate_down()
    assert applied_down == [
        "002_create_background_jobs.down.sql",
        "001_create_country_access.down.sql",
    ]
    down_sqls = [sql for sql, _ in fake_pool.executed]
    assert any("DROP TABLE IF EXISTS background_jobs" in s for s in down_sqls)
    assert any("DROP TABLE IF EXISTS country_access" in s for s in down_sqls)


def test_split_sql_statements_handles_quotes_and_comments() -> None:
    sql = """
    -- Comment with ; semicolon
    CREATE TABLE t (a TEXT DEFAULT 'val;ue');
    INSERT INTO t VALUES ('it''s;fine');
    """
    stmts = split_sql_statements(sql)
    assert len(stmts) == 2
    assert "DEFAULT 'val;ue'" in stmts[0]
    assert "'it''s;fine'" in stmts[1]


def test_migrations_exist_and_non_empty() -> None:
    repo_root = Path(__file__).resolve().parents[4]
    target_dir = repo_root / "migrations" / "postgres"

    for fname in (
        "001_create_country_access.sql",
        "001_create_country_access.down.sql",
        "002_create_background_jobs.sql",
        "002_create_background_jobs.down.sql",
    ):
        file_path = target_dir / fname
        assert file_path.is_file()
        assert len(file_path.read_bytes()) > 0
