from __future__ import annotations

import asyncio

import httpx
from fastapi import Depends, FastAPI

from app.common.config.parsers import ApiPriorityLevel
from app.common.decorators import skip_inbound_bulkhead
from app.common.filters.all_exceptions_filter import (
    AllExceptionsFilter,
    register_exception_handlers,
)
from app.core.overload.priority_bulkhead_guard import (
    InboundBulkheadService,
    InboundPriorityBulkheadGuard,
    api_priority,
)


def _build(
    max_in_flight: int = 2, *, leveling: bool = False, shedding: bool = True
) -> tuple[FastAPI, InboundBulkheadService, asyncio.Event]:
    service = InboundBulkheadService(
        {"enabled": True, "max_in_flight": max_in_flight},
        {"enabled": leveling, "max_queue": 4, "max_wait_ms": 50, "poll_interval_ms": 5},
    )
    guard = InboundPriorityBulkheadGuard(
        service,
        {
            "enabled": shedding,
            "default_priority": ApiPriorityLevel.NORMAL,
            "thresholds": {
                "critical": 1.0,
                "high": 0.95,
                "normal": 0.85,
                "low": 0.5,
            },
        },
    )
    app = FastAPI(dependencies=[Depends(guard)])
    register_exception_handlers(app, AllExceptionsFilter())
    gate = asyncio.Event()

    @app.get("/slow")
    async def slow() -> dict[str, bool]:
        await gate.wait()
        return {"ok": True}

    @app.get("/low")
    @api_priority(ApiPriorityLevel.LOW)
    async def low() -> dict[str, bool]:
        return {"ok": True}

    @app.get("/critical")
    @api_priority(ApiPriorityLevel.CRITICAL)
    async def critical() -> dict[str, bool]:
        return {"ok": True}

    @app.get("/boom")
    async def boom() -> dict[str, bool]:
        raise RuntimeError("boom")

    @app.get("/exempt")
    @skip_inbound_bulkhead
    async def exempt() -> dict[str, bool]:
        return {"ok": True}

    return app, service, gate


def _client(app: FastAPI) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://t",
    )


async def test_rejects_503_with_retry_after_when_saturated() -> None:
    app, service, gate = _build(2)
    async with _client(app) as client:
        tasks = [asyncio.create_task(client.get("/slow")) for _ in range(2)]
        await asyncio.sleep(0.05)
        assert service.active_count == 2
        rejected = await client.get("/critical")
        assert rejected.status_code == 503
        assert "retry-after" in rejected.headers
        gate.set()
        done = await asyncio.gather(*tasks)
    assert [r.status_code for r in done] == [200, 200]
    assert service.active_count == 0


async def test_low_priority_shed_before_saturation() -> None:
    app, _service, gate = _build(2)
    async with _client(app) as client:
        holder = asyncio.create_task(client.get("/slow"))
        await asyncio.sleep(0.05)
        shed = await client.get("/low")
        assert shed.status_code == 503
        assert "retry-after" in shed.headers
        gate.set()
        await holder


async def test_slot_released_when_handler_raises() -> None:
    app, service, _gate = _build(1)
    async with _client(app) as client:
        res = await client.get("/boom")
        assert res.status_code == 500
    assert service.active_count == 0


async def test_skip_decorator_bypasses_bulkhead() -> None:
    app, service, _gate = _build(1)
    async with _client(app) as client:
        res = await client.get("/exempt")
    assert res.status_code == 200
    assert service.active_count == 0


async def test_load_leveling_queue_times_out_with_503() -> None:
    app, _service, gate = _build(1, leveling=True, shedding=False)
    async with _client(app) as client:
        holder = asyncio.create_task(client.get("/slow"))
        await asyncio.sleep(0.05)
        waited = await client.get("/critical")
        assert waited.status_code == 503
        assert "retry-after" in waited.headers
        gate.set()
        await holder
