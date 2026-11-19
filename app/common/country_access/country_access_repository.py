from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from app.common.country_access.types import (
    CountryAccessConfig,
    CountryAccessConflictError,
    CountryAccessRepository,
    assert_country_code,
)
from app.common.postgres.postgres_pool import PostgresPool


class PostgresCountryAccessRepository(CountryAccessRepository):
    def __init__(self, pool: PostgresPool) -> None:
        self._pool = pool

    async def get_all(self) -> list[CountryAccessConfig]:
        query_res = await self._pool.query(
            """SELECT country_code, enabled, created_at, updated_at
               FROM country_access
               ORDER BY country_code ASC"""
        )
        return [self._map_row(r) for r in query_res.rows]

    async def get_by_country_code(
        self, country_code: str
    ) -> CountryAccessConfig | None:
        code = assert_country_code(country_code)
        query_res = await self._pool.query(
            """SELECT country_code, enabled, created_at, updated_at
               FROM country_access
               WHERE country_code = $1""",
            [code],
        )
        if not query_res.rows:
            return None
        return self._map_row(query_res.rows[0])

    async def upsert(self, config: CountryAccessConfig) -> CountryAccessConfig:
        code = assert_country_code(config.country_code)
        existing = await self.get_by_country_code(code)

        if existing is None:
            now = datetime.now(UTC)
            created_at = config.created_at or now
            updated_at = config.updated_at or now
            res = await self._pool.query(
                """INSERT INTO country_access
                   (country_code, enabled, created_at, updated_at)
                   VALUES ($1, $2, $3, $4)
                   ON CONFLICT (country_code) DO NOTHING
                   RETURNING country_code, enabled, created_at, updated_at""",
                [code, config.enabled, created_at, updated_at],
            )
            if not res.rows:
                raise CountryAccessConflictError(
                    f"Country access {code} was created concurrently"
                )
            return self._map_row(res.rows[0])

        next_updated_at = datetime.now(UTC)
        res = await self._pool.query(
            """UPDATE country_access
               SET enabled = $2,
                   updated_at = $3
               WHERE country_code = $1
                 AND updated_at = $4
               RETURNING country_code, enabled, created_at, updated_at""",
            [code, config.enabled, next_updated_at, config.updated_at],
        )
        if not res.rows:
            raise CountryAccessConflictError(
                f"Country access {code} was modified concurrently"
            )
        return self._map_row(res.rows[0])

    async def delete(self, country_code: str) -> None:
        code = assert_country_code(country_code)
        await self._pool.query(
            "DELETE FROM country_access WHERE country_code = $1", [code]
        )

    def _map_row(self, row: dict[str, Any]) -> CountryAccessConfig:
        return CountryAccessConfig(
            country_code=assert_country_code(str(row["country_code"]).strip()),
            enabled=bool(row["enabled"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )
