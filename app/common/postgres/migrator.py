from __future__ import annotations

from pathlib import Path
from typing import Any

import asyncpg

from app.common.postgres.postgres_pool import PostgresPool, PostgresPoolOptions


def resolve_default_migrations_dir() -> Path:
    """Return the workspace `migrations/postgres` directory path."""
    root = Path(__file__).resolve().parents[3]
    return root / "migrations" / "postgres"


def split_sql_statements(sql_text: str) -> list[str]:
    """Split a SQL script into individual statements, respecting quotes and comments."""
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


async def execute_migration_sql(executor: Any, sql_text: str) -> None:
    """Execute SQL statements idempotently (tolerating duplicate CREATE TYPE 42710)."""
    for stmt in split_sql_statements(sql_text):
        stripped = _strip_leading_sql_comments(stmt)
        if not stripped:
            continue
        upper = stripped.upper()
        try:
            await executor.execute(stmt)
        except Exception as exc:
            sqlstate = getattr(exc, "sqlstate", None)
            if upper.startswith("CREATE TYPE") and sqlstate == "42710":
                continue
            raise


class PostgresMigrator:
    """Applies `migrations/postgres/*.sql` up and `.down.sql` down idempotently."""

    def __init__(
        self,
        target: PostgresPool | PostgresPoolOptions | str | Any,
        migrations_dir: Path | None = None,
    ) -> None:
        self._target = target
        self._migrations_dir = migrations_dir or resolve_default_migrations_dir()

    @property
    def migrations_dir(self) -> Path:
        return self._migrations_dir

    def list_up_files(self) -> list[Path]:
        return sorted(
            p
            for p in self._migrations_dir.glob("*.sql")
            if not p.name.endswith(".down.sql")
        )

    def list_down_files(self) -> list[Path]:
        return sorted(self._migrations_dir.glob("*.down.sql"), reverse=True)

    async def _run_with_executor(
        self,
        files: list[Path],
    ) -> list[str]:
        applied: list[str] = []
        if hasattr(self._target, "execute") and callable(self._target.execute):
            for file_path in files:
                await execute_migration_sql(
                    self._target,
                    file_path.read_text("utf-8"),
                )
                applied.append(file_path.name)
            return applied

        dsn = (
            self._target.dsn
            if isinstance(self._target, PostgresPoolOptions)
            else str(self._target)
        )
        conn = await asyncpg.connect(dsn)
        try:
            for file_path in files:
                await execute_migration_sql(conn, file_path.read_text("utf-8"))
                applied.append(file_path.name)
        finally:
            await conn.close()
        return applied

    async def migrate_up(self, *, reset: bool = False) -> list[str]:
        if reset:
            await self.migrate_down()
        return await self._run_with_executor(self.list_up_files())

    async def migrate_down(self) -> list[str]:
        return await self._run_with_executor(self.list_down_files())


async def run_postgres_migrations_up(
    target: PostgresPool | PostgresPoolOptions | str | Any,
    migrations_dir: Path | None = None,
    *,
    reset: bool = False,
) -> list[str]:
    migrator = PostgresMigrator(target, migrations_dir=migrations_dir)
    return await migrator.migrate_up(reset=reset)


async def run_postgres_migrations_down(
    target: PostgresPool | PostgresPoolOptions | str | Any,
    migrations_dir: Path | None = None,
) -> list[str]:
    migrator = PostgresMigrator(target, migrations_dir=migrations_dir)
    return await migrator.migrate_down()


__all__ = [
    "PostgresMigrator",
    "execute_migration_sql",
    "resolve_default_migrations_dir",
    "run_postgres_migrations_down",
    "run_postgres_migrations_up",
    "split_sql_statements",
]
