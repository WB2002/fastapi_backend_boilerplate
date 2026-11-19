from __future__ import annotations

import asyncio

import pytest

from app.core.health import HealthSnapshot


async def test_health_snapshot_single_flights_concurrent_in_flight_requests() -> None:
    fixed_now = 1000
    snapshot = HealthSnapshot[str](2000, lambda: fixed_now)

    load_count = 0
    gate = asyncio.Event()

    async def loader() -> str:
        nonlocal load_count
        load_count += 1
        await gate.wait()
        return "healthy"

    t1 = asyncio.create_task(snapshot.get(loader))
    t2 = asyncio.create_task(snapshot.get(loader))
    await asyncio.sleep(0)

    assert load_count == 1
    gate.set()
    res1, res2 = await asyncio.gather(t1, t2)
    assert res1 == "healthy"
    assert res2 == "healthy"
    assert load_count == 1


async def test_health_snapshot_returns_cached_result_within_ttl() -> None:
    current_now = 1000
    snapshot = HealthSnapshot[str](2000, lambda: current_now)
    load_count = 0

    async def loader() -> str:
        nonlocal load_count
        load_count += 1
        return "healthy"

    s1 = await snapshot.get(loader)
    assert s1 == "healthy"
    assert load_count == 1

    current_now += 1000  # 1000ms elapsed, TTL is 2000ms
    s2 = await snapshot.get(loader)
    assert s2 == "healthy"
    assert load_count == 1


async def test_health_snapshot_reloads_after_ttl_expires() -> None:
    current_now = 1000
    snapshot = HealthSnapshot[str](2000, lambda: current_now)
    values = ["first", "second"]
    load_count = 0

    async def loader() -> str:
        nonlocal load_count
        val = values[load_count]
        load_count += 1
        return val

    s1 = await snapshot.get(loader)
    assert s1 == "first"
    assert load_count == 1

    current_now += 2001  # TTL expired
    s2 = await snapshot.get(loader)
    assert s2 == "second"
    assert load_count == 2


async def test_health_snapshot_does_not_cache_rejected_loads() -> None:
    fixed_now = 1000
    snapshot = HealthSnapshot[str](2000, lambda: fixed_now)
    load_count = 0

    async def loader() -> str:
        nonlocal load_count
        load_count += 1
        if load_count == 1:
            raise RuntimeError("fail")
        return "success"

    with pytest.raises(RuntimeError, match="fail"):
        await snapshot.get(loader)
    assert load_count == 1

    # Immediate next call must retry since failure is never cached
    retried = await snapshot.get(loader)
    assert retried == "success"
    assert load_count == 2


async def test_health_snapshot_ttl_zero_disables_caching_but_coalesces_in_flight() -> (
    None
):
    current_now = 1000
    snapshot = HealthSnapshot[int](0, lambda: current_now)
    load_count = 0
    gate = asyncio.Event()

    async def loader() -> int:
        nonlocal load_count
        load_count += 1
        await gate.wait()
        return load_count

    t1 = asyncio.create_task(snapshot.get(loader))
    t2 = asyncio.create_task(snapshot.get(loader))
    await asyncio.sleep(0)
    gate.set()

    r1, r2 = await asyncio.gather(t1, t2)
    assert r1 == 1
    assert r2 == 1
    assert load_count == 1

    # Subsequent call with ttl=0 reloads even at the exact same timestamp
    r3 = await snapshot.get(loader)
    assert r3 == 2
    assert load_count == 2
