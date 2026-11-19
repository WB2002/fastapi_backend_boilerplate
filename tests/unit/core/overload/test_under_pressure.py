from __future__ import annotations

import httpx
from fastapi import FastAPI

from app.core.overload.under_pressure import (
    UnderPressureMiddleware,
    UnderPressureMonitor,
    is_pressure_exempt_path,
    normalize_request_path,
)


def _app(delay_ms: float, *, status_route: bool = False) -> FastAPI:
    config = {
        "enabled": True,
        "max_event_loop_delay_ms": 100,
        "retry_after_sec": 7,
        "expose_status_route": status_route,
    }
    monitor = UnderPressureMonitor(config, event_loop_delay_sampler=lambda: delay_ms)
    app = FastAPI()
    app.add_middleware(UnderPressureMiddleware, config=config, monitor=monitor)

    @app.get("/work")
    async def work() -> dict[str, bool]:
        return {"ok": True}

    @app.get("/health")
    async def health() -> dict[str, bool]:
        return {"ok": True}

    return app


def _client(app: FastAPI) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://t"
    )


async def test_passes_when_below_threshold() -> None:
    async with _client(_app(10)) as client:
        assert (await client.get("/work")).status_code == 200


async def test_returns_503_with_retry_after_under_event_loop_lag() -> None:
    async with _client(_app(500)) as client:
        res = await client.get("/work")
    assert res.status_code == 503
    assert res.headers["retry-after"] == "7"
    assert res.json()["statusCode"] == 503


async def test_health_is_exempt_from_pressure() -> None:
    async with _client(_app(500)) as client:
        assert (await client.get("/health")).status_code == 200


async def test_optional_status_route() -> None:
    async with _client(_app(10, status_route=True)) as client:
        res = await client.get("/status")
    assert res.status_code == 200


def test_memory_thresholds() -> None:
    monitor = UnderPressureMonitor(
        {"enabled": True, "max_rss_bytes": 100},
        rss_bytes_sampler=lambda: 1_000,
    )
    assert monitor.check_pressure() == ("rssBytes", 1_000)


def test_disabled_monitor_never_pressures() -> None:
    monitor = UnderPressureMonitor(
        {"enabled": False, "max_rss_bytes": 1}, rss_bytes_sampler=lambda: 10
    )
    assert monitor.check_pressure() is None


def test_path_helpers() -> None:
    assert normalize_request_path("/health/?x=1") == "/health"
    assert is_pressure_exempt_path("/health/ready")
    assert not is_pressure_exempt_path("/health/live")
