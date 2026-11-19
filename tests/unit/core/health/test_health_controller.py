from __future__ import annotations

from typing import Any

import httpx
import pytest

from app.common.decorators import (
    is_public_endpoint,
    should_skip_country_access,
    should_skip_inbound_bulkhead,
    should_skip_rate_limit,
    should_skip_request_deadline,
)
from app.core.health import (
    FeatureFlagHealthIndicator,
    HealthCheckService,
    HealthController,
    HealthIndicatorFunction,
    HealthIndicatorResult,
    ServiceUnavailableException,
)
from app.core.shutdown.shutdown_state import ShutdownState
from app.core.telemetry.otel import should_skip_tracing
from app.main import create_app


class _MockHealthCheckService(HealthCheckService):
    def __init__(self) -> None:
        self.calls: list[list[HealthIndicatorFunction]] = []

    async def check(  # type: ignore[override]
        self,
        indicators: list[HealthIndicatorFunction],
    ) -> dict[str, Any]:
        self.calls.append(list(indicators))
        return await super().check(indicators)


class _StubIndicator:
    def __init__(self, key: str, result: HealthIndicatorResult | Exception) -> None:
        self.key = key
        self.result = result
        self.calls = 0

    async def ping_check(
        self, key: str = "", *_args: Any, **_kwargs: Any
    ) -> HealthIndicatorResult:
        self.calls += 1
        if isinstance(self.result, Exception):
            raise self.result
        return self.result

    async def check_heap(self, key: str = "", *_args: Any) -> HealthIndicatorResult:
        return await self.ping_check(key)

    async def check_storage(self, key: str = "", *_args: Any) -> HealthIndicatorResult:
        return await self.ping_check(key)

    async def is_healthy(self, key: str = "") -> HealthIndicatorResult:
        return await self.ping_check(key)


def test_health_controller_skip_decorators() -> None:
    assert is_public_endpoint(HealthController) is True
    assert should_skip_rate_limit(HealthController) is True
    assert should_skip_request_deadline(HealthController) is True
    assert should_skip_country_access(HealthController) is True
    assert should_skip_inbound_bulkhead(HealthController) is True
    assert should_skip_tracing(controller_class=HealthController) is True


def test_health_live_returns_ok_or_503_on_shutdown() -> None:
    health_svc = _MockHealthCheckService()
    shutdown_state = ShutdownState()
    controller = HealthController(
        health=health_svc,
        config_service={},
        shutdown_state=shutdown_state,
    )

    assert controller.live() == {"status": "ok"}
    assert len(health_svc.calls) == 0

    shutdown_state.begin_shutdown()
    with pytest.raises(ServiceUnavailableException) as exc_info:
        controller.live()
    assert exc_info.value.status_code == 503
    assert exc_info.value.get_response() == "Application is shutting down"
    assert len(health_svc.calls) == 0


async def test_health_ready_and_check_behavior() -> None:
    health_svc = _MockHealthCheckService()
    shutdown_state = ShutdownState()
    mongo = _StubIndicator("mongo", {"mongo": {"status": "up"}})
    memory = _StubIndicator("memory_heap", {"memory_heap": {"status": "up"}})
    disk = _StubIndicator("storage", {"storage": {"status": "up"}})
    ff = FeatureFlagHealthIndicator()

    controller = HealthController(
        health=health_svc,
        mongoose=mongo,
        memory=memory,
        disk=disk,
        config_service={},
        shutdown_state=shutdown_state,
        feature_flag_health=ff,
    )

    res1 = await controller.ready()
    res2 = await controller.ready()
    assert res1["status"] == "ok"
    assert res2["status"] == "ok"
    assert res1["details"]["mongo"] == {"status": "up"}
    assert res1["details"]["feature_flags"]["status"] == "up"
    # Called twice within 2000ms TTL -> health.check executed once
    assert len(health_svc.calls) == 1

    # When shutdown begins, ready() fails immediately with shuttingDown: down
    shutdown_state.begin_shutdown()
    with pytest.raises(ServiceUnavailableException) as exc_info:
        await controller.ready()
    payload = exc_info.value.get_response()
    assert isinstance(payload, dict)
    assert payload["status"] == "error"
    assert payload["details"]["shuttingDown"] == {
        "status": "down",
        "message": "Application is shutting down",
    }

    # Meanwhile check() (/health) still returns 200 without shuttingDown
    check_res = await controller.check()
    assert check_res["status"] == "ok"
    assert "shuttingDown" not in check_res["details"]


async def test_health_controller_on_module_destroy_and_ping_postgres() -> None:
    closed = False
    queried: list[str] = []

    class _FakePgPool:
        async def query(self, sql: str) -> dict[str, Any]:
            queried.append(sql)
            return {"rows": [{"?column?": 1}]}

        async def end(self) -> None:
            nonlocal closed
            closed = True

    controller = HealthController(config_service={}, pg_pool=_FakePgPool())
    ping_res = await controller.ping_postgres()
    assert ping_res == {"postgres": {"status": "up"}}
    assert queried == ["SELECT 1"]

    await controller.on_module_destroy()
    assert closed is True
    assert controller.pg_pool is None
    # Second destroy is a no-op
    await controller.on_module_destroy()


async def test_health_controller_ping_postgres_failure() -> None:
    class _FailingPgPool:
        async def query(self, _sql: str) -> None:
            raise ConnectionError("connection timeout")

    controller = HealthController(config_service={}, pg_pool=_FailingPgPool())
    ping_res = await controller.ping_postgres()
    assert ping_res == {"postgres": {"status": "down", "message": "connection timeout"}}


async def test_health_http_endpoints_wire_contract(
    valid_base_env: dict[str, str],
    capsys: pytest.CaptureFixture[str],
) -> None:
    shutdown_state = ShutdownState()
    app = create_app(env=valid_base_env, shutdown_state=shutdown_state)

    # Override indicators on app.state.health_controller with healthy stubs
    ctrl: HealthController = app.state.health_controller
    ctrl.mongoose = _StubIndicator("mongo", {"mongo": {"status": "up"}})
    ctrl.memory = _StubIndicator("memory_heap", {"memory_heap": {"status": "up"}})
    ctrl.disk = _StubIndicator("storage", {"storage": {"status": "up"}})
    ctrl.microservice = _StubIndicator("redis", {"redis": {"status": "up"}})

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        live_res = await client.get("/health/live")
        assert live_res.status_code == 200
        assert live_res.json() == {"status": "ok"}

        ready_res = await client.get("/health/ready")
        assert ready_res.status_code == 200
        ready_body = ready_res.json()
        assert ready_body["status"] == "ok"
        assert ready_body["info"]["mongo"] == {"status": "up"}
        assert ready_body["info"]["redis"] == {"status": "up"}
        assert ready_body["error"] == {}

        # Begin shutdown -> /health/live & /health/ready return 503, /health 200
        shutdown_state.begin_shutdown()

        live_503 = await client.get("/health/live")
        assert live_503.status_code == 503
        assert live_503.json()["statusCode"] == 503
        assert live_503.json()["message"] == "Application is shutting down"

        ready_503 = await client.get("/health/ready")
        assert ready_503.status_code == 503
        r503_body = ready_503.json()
        assert r503_body["statusCode"] == 503
        assert r503_body["status"] == "error"
        assert r503_body["path"] == "/health/ready"
        assert "timestamp" in r503_body
        assert "requestId" in r503_body
        assert r503_body["details"]["shuttingDown"] == {
            "status": "down",
            "message": "Application is shutting down",
        }

        health_200 = await client.get("/health")
        assert health_200.status_code == 200
        assert "shuttingDown" not in health_200.json()["details"]

        # Verify HealthCheck503 EMF metric was emitted to stdout
        captured = capsys.readouterr()
        assert '"ServiceUnavailable"' in captured.out
        assert '"health_check"' in captured.out
