from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from app.conformance.assert_loopback_target_url import assert_loopback_target_url
from app.conformance.conformance_cache_service import ConformanceCacheService
from app.conformance.conformance_deadline_controller import slow
from app.conformance.conformance_http_client_service import (
    ConformanceHttpClientService,
)
from app.conformance.conformance_priority_controller import critical
from app.conformance.conformance_redaction_controller import (
    flatten_to_dotted_keys,
    redact,
)
from app.conformance.dto import (
    ConformanceCacheRequestDto,
    ConformanceDeadlineQueryDto,
    ConformanceHttpClientRequestDto,
    ConformancePriorityQueryDto,
    ConformanceRedactionRequestDto,
)
from app.core.http_client import CircuitOpenError, HttpTimeoutError


def test_assert_loopback_target_url_accepts_loopback() -> None:
    assert (
        assert_loopback_target_url("http://127.0.0.1:4010/upstream").hostname
        == "127.0.0.1"
    )
    assert assert_loopback_target_url("http://localhost:3000/").hostname == "localhost"


def test_assert_loopback_target_url_rejects_non_loopback() -> None:
    with pytest.raises(HTTPException) as exc_info:
        assert_loopback_target_url("http://169.254.169.254/latest/meta-data")
    assert exc_info.value.status_code == 400
    assert "targetUrl must be http://127.0.0.1 or http://localhost" in str(
        exc_info.value.detail
    )


def test_assert_loopback_target_url_rejects_credentials() -> None:
    with pytest.raises(HTTPException) as exc_info:
        assert_loopback_target_url("http://user:pass@127.0.0.1:3000/")
    assert exc_info.value.status_code == 400


def test_assert_loopback_target_url_rejects_https() -> None:
    with pytest.raises(HTTPException) as exc_info:
        assert_loopback_target_url("https://localhost:3000/")
    assert exc_info.value.status_code == 400


@pytest.mark.asyncio
async def test_conformance_cache_service() -> None:
    mock_cache = MagicMock()
    mock_cache.get_or_set = AsyncMock(return_value="cached-val")
    mock_cache.delete = AsyncMock()

    service = ConformanceCacheService(mock_cache)
    dto = ConformanceCacheRequestDto(
        key="test-key",
        value="cached-val",
        softTtlMs=5000,
        hardTtlMs=10000,
    )
    res = await service.get_or_set(dto)
    assert res["cachedValue"] == "cached-val"
    assert "loaderCallCount" in res
    assert "isFromLoader" in res

    await service.reset("test-key")
    mock_cache.delete.assert_awaited_once_with("conformance:test-key")


@pytest.mark.asyncio
async def test_conformance_http_client_service_circuit_open_error() -> None:
    mock_client = MagicMock()
    mock_configured = MagicMock()
    mock_configured.get = AsyncMock(side_effect=CircuitOpenError("Circuit is open"))
    mock_client.internal = MagicMock(return_value=mock_configured)
    mock_configured.add_base_url = MagicMock(return_value=mock_configured)

    service = ConformanceHttpClientService(mock_client)
    dto = ConformanceHttpClientRequestDto(
        clientKind="internal",
        clientName="test-client",
        targetUrl="http://127.0.0.1:4010/api",
    )
    with pytest.raises(HTTPException) as exc_info:
        await service.execute(dto)
    assert exc_info.value.status_code == 503


@pytest.mark.asyncio
async def test_conformance_http_client_service_timeout_error() -> None:
    mock_client = MagicMock()
    mock_configured = MagicMock()
    mock_configured.get = AsyncMock(side_effect=HttpTimeoutError("Request timed out"))
    mock_client.internal = MagicMock(return_value=mock_configured)
    mock_configured.add_base_url = MagicMock(return_value=mock_configured)

    service = ConformanceHttpClientService(mock_client)
    dto = ConformanceHttpClientRequestDto(
        clientKind="internal",
        clientName="test-client",
        targetUrl="http://127.0.0.1:4010/api",
    )
    with pytest.raises(HTTPException) as exc_info:
        await service.execute(dto)
    assert exc_info.value.status_code == 504


@pytest.mark.asyncio
async def test_conformance_deadline_slow() -> None:
    dto = ConformanceDeadlineQueryDto(delayMs=5)
    res = await slow(dto)
    assert "completedAfterMs" in res
    assert "remainingBudgetMs" in res


@pytest.mark.asyncio
async def test_conformance_priority_critical() -> None:
    dto = ConformancePriorityQueryDto(holdMs=0)
    res = await critical(dto)
    assert res == {"priority": "critical"}


def test_conformance_redaction() -> None:
    payload = {
        "email": "user@test.com",
        "password": "secret-pass",
        "nested": {"token": "jwt-token"},
    }
    dotted = flatten_to_dotted_keys(payload)
    assert dotted["email"] == "user@test.com"
    assert dotted["nested.token"] == "jwt-token"

    dto = ConformanceRedactionRequestDto(payload=payload)
    res = redact(dto)
    assert res["redactedPayload"]["email"] == "[REDACTED]"
    assert res["redactedPayload"]["password"] == "[REDACTED]"
