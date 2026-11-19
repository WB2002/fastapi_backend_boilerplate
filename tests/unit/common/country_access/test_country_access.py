from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException, Request

from app.common.country_access import (
    CountryAccessConfig,
    CountryAccessConflictError,
    CountryAccessGuard,
    CountryAccessModuleOptions,
    CountryAccessService,
    MaxMindGeoLocationService,
    PostgresCountryAccessRepository,
    assert_country_code,
    normalize_country_code,
    skip_country_access,
)


def test_country_code_validation() -> None:
    assert normalize_country_code("us") == "US"
    assert normalize_country_code("GB") == "GB"
    assert normalize_country_code("USA") is None
    assert normalize_country_code("") is None

    assert assert_country_code("fr") == "FR"
    with pytest.raises(ValueError, match="Invalid ISO 3166-1 alpha-2"):
        assert_country_code("123")


@pytest.mark.asyncio
async def test_postgres_country_access_repository() -> None:
    pool_mock = AsyncMock()

    now = datetime.now(UTC)
    pool_mock.query.return_value = MagicMock(
        rows=[
            {
                "country_code": "US",
                "enabled": True,
                "created_at": now,
                "updated_at": now,
            }
        ]
    )

    repo = PostgresCountryAccessRepository(pool_mock)
    all_configs = await repo.get_all()
    assert len(all_configs) == 1
    assert all_configs[0].country_code == "US"
    assert all_configs[0].enabled is True

    # Upsert existing with conflict
    pool_mock.query.side_effect = [
        # get_by_country_code -> existing
        MagicMock(
            rows=[
                {
                    "country_code": "US",
                    "enabled": True,
                    "created_at": now,
                    "updated_at": now,
                }
            ]
        ),
        # update -> 0 rows modified (concurrent update)
        MagicMock(rows=[]),
    ]

    with pytest.raises(CountryAccessConflictError):
        await repo.upsert(
            CountryAccessConfig(
                country_code="US",
                enabled=False,
                created_at=now,
                updated_at=now,
            )
        )


@pytest.mark.asyncio
async def test_geo_location_service() -> None:
    reader_mock = MagicMock()
    mock_response = MagicMock()
    mock_response.country.iso_code = "DE"
    reader_mock.country.return_value = mock_response

    geo = MaxMindGeoLocationService(reader=reader_mock)

    # Public IP lookup
    code = geo.get_country_code("8.8.8.8")
    assert code == "DE"

    # Private IP check
    assert geo.get_country_code("127.0.0.1") is None
    assert geo.get_country_code("192.168.1.1") is None
    assert geo.get_country_code("10.0.0.1") is None

    # Invalid IP
    assert geo.get_country_code("invalid-ip") is None


@pytest.mark.asyncio
async def test_country_access_service_and_guard() -> None:
    repo_mock = AsyncMock()
    now = datetime.now(UTC)
    repo_mock.get_all.return_value = [
        CountryAccessConfig("US", True, now, now),
        CountryAccessConfig("CA", False, now, now),
    ]

    service = CountryAccessService(
        repo_mock,
        CountryAccessModuleOptions(
            unknown_country_policy="deny",
            bypass_paths=["/health"],
        ),
    )
    assert not service.is_active()

    # Initial state allows all until refresh
    assert service.is_country_allowed("US") is True
    assert service.is_country_allowed("XX") is True

    await service.refresh_snapshot()
    assert service.is_active()
    assert service.is_country_allowed("US") is True
    assert service.is_country_allowed("CA") is False
    assert service.is_country_allowed("DE") is False

    # Guard tests
    geo_mock = MagicMock()
    geo_mock.get_country_code.return_value = "US"

    guard = CountryAccessGuard(service, geo_mock)

    # 1. Allowed request
    req1 = MagicMock(spec=Request)
    req1.url.path = "/api/v1/resource"
    req1.scope = {}
    req1.headers = {"x-forwarded-for": "1.2.3.4"}
    req1.client.host = "1.2.3.4"
    await guard.check_access(req1)

    # 2. Bypass path
    req_bypass = MagicMock(spec=Request)
    req_bypass.url.path = "/health"
    req_bypass.scope = {}
    await guard.check_access(req_bypass)

    # 3. Disallowed country
    geo_mock.get_country_code.return_value = "DE"
    with pytest.raises(HTTPException) as exc_info:
        await guard.check_access(req1)
    assert exc_info.value.status_code == 403

    # 4. Skip decorator
    @skip_country_access
    def endpoint_fn() -> None:
        pass

    req1.scope = {"endpoint": endpoint_fn}
    await guard.check_access(req1)
