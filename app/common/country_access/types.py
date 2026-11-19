from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Protocol

CountryAccessServiceStatus = Literal["inactive", "active"]
UnknownCountryPolicy = Literal["allow", "deny"]

_COUNTRY_CODE_RE = re.compile(r"^[A-Z]{2}$")


class CountryAccessConflictError(Exception):
    """Raised when an optimistic concurrency check fails on country access update."""


def normalize_country_code(code: str | None) -> str | None:
    if not code:
        return None
    trimmed = code.strip().upper()
    return trimmed if _COUNTRY_CODE_RE.match(trimmed) else None


def assert_country_code(code: str | None) -> str:
    normalized = normalize_country_code(code)
    if not normalized:
        raise ValueError(f"Invalid ISO 3166-1 alpha-2 country code: '{code}'")
    return normalized


@dataclass(slots=True)
class CountryAccessConfig:
    country_code: str
    enabled: bool
    created_at: datetime
    updated_at: datetime

    @property
    def countryCode(self) -> str:
        return self.country_code

    @property
    def createdAt(self) -> datetime:
        return self.created_at

    @property
    def updatedAt(self) -> datetime:
        return self.updated_at


class GeoLocationService(Protocol):
    def get_country_code(self, ip: str) -> str | None: ...


class CountryAccessRepository(Protocol):
    async def get_all(self) -> list[CountryAccessConfig]: ...

    async def get_by_country_code(
        self, country_code: str
    ) -> CountryAccessConfig | None: ...

    async def upsert(self, config: CountryAccessConfig) -> CountryAccessConfig: ...

    async def delete(self, country_code: str) -> None: ...
