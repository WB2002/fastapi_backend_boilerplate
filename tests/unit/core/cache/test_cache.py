from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock

import pytest

from app.core.cache import (
    CacheGetOrSetOptions,
    CacheModuleOptions,
    CacheService,
    CacheTtl,
    MemoryCacheBackend,
    RedisCacheLoaderLock,
    acquire_redis_loader_lock,
    apply_ttl_jitter,
    release_redis_loader_lock,
)
from app.core.cache.types import CacheEntry


def test_apply_ttl_jitter() -> None:
    base = CacheTtl(soft_ttl_ms=1000, hard_ttl_ms=2000)
    no_jitter = apply_ttl_jitter(base, 0.0)
    assert no_jitter.soft_ttl_ms == 1000
    assert no_jitter.hard_ttl_ms == 2000

    def mock_rand() -> float:
        return 0.5

    jittered = apply_ttl_jitter(base, 0.2, rand_fn=mock_rand)
    assert jittered.soft_ttl_ms == 1100
    assert jittered.hard_ttl_ms == 2200

    with pytest.raises(ValueError, match="Cache TTL values must be > 0"):
        apply_ttl_jitter(CacheTtl(soft_ttl_ms=-1, hard_ttl_ms=10), 0.1)

    with pytest.raises(ValueError, match="softTtlMs must be <= hardTtlMs"):
        apply_ttl_jitter(CacheTtl(soft_ttl_ms=200, hard_ttl_ms=100), 0.1)


@pytest.mark.asyncio
async def test_memory_cache_backend() -> None:
    backend = MemoryCacheBackend()
    assert backend.size() == 0

    future_ms = int(time.time() * 1000) + 100_000
    entry = CacheEntry(
        v={"hello": "world"}, soft_expires_at=future_ms, hard_expires_at=future_ms
    )
    await backend.set("test-key", entry, 5000)
    assert backend.size() == 1

    got = await backend.get("test-key")
    assert got is not None
    assert got.v == {"hello": "world"}

    # Hard expired
    expired_entry = CacheEntry(v="expired", soft_expires_at=10, hard_expires_at=20)
    await backend.set("expired-key", expired_entry, 10)
    assert await backend.get("expired-key") is None

    await backend.delete("test-key")
    assert await backend.get("test-key") is None


@pytest.mark.asyncio
async def test_cache_service_fresh_and_swr() -> None:
    backend = MemoryCacheBackend()
    service = CacheService(backend, CacheModuleOptions(key_prefix="app:"))

    call_count = 0

    async def loader() -> str:
        nonlocal call_count
        call_count += 1
        return f"value-{call_count}"

    # 1. Cache Miss -> loads and caches
    val1 = await service.get_or_set(
        "item:1", loader, CacheTtl(soft_ttl_ms=50, hard_ttl_ms=500)
    )
    assert val1 == "value-1"
    assert call_count == 1

    # 2. Fresh Hit -> returns cached immediately without loading
    val2 = await service.get_or_set(
        "item:1", loader, CacheTtl(soft_ttl_ms=50, hard_ttl_ms=500)
    )
    assert val2 == "value-1"
    assert call_count == 1

    # 3. Wait until soft TTL expires (stale)
    await asyncio.sleep(0.06)

    # Stale Hit -> returns stale value-1, kicks off background revalidation
    val3 = await service.get_or_set(
        "item:1", loader, CacheTtl(soft_ttl_ms=50, hard_ttl_ms=500)
    )
    assert val3 == "value-1"

    # Allow background revalidation to finish
    await asyncio.sleep(0.02)
    assert call_count == 2

    # Next read gets freshly revalidated value-2
    val4 = await service.get_or_set(
        "item:1", loader, CacheTtl(soft_ttl_ms=50, hard_ttl_ms=500)
    )
    assert val4 == "value-2"


@pytest.mark.asyncio
async def test_cache_service_single_flight() -> None:
    backend = MemoryCacheBackend()
    service = CacheService(backend)

    call_count = 0

    async def slow_loader() -> str:
        nonlocal call_count
        call_count += 1
        await asyncio.sleep(0.03)
        return "slow-result"

    res1, res2, res3 = await asyncio.gather(
        service.get_or_set("heavy:key", slow_loader, CacheTtl(1000, 2000)),
        service.get_or_set("heavy:key", slow_loader, CacheTtl(1000, 2000)),
        service.get_or_set("heavy:key", slow_loader, CacheTtl(1000, 2000)),
    )
    assert res1 == "slow-result"
    assert res2 == "slow-result"
    assert res3 == "slow-result"
    assert call_count == 1


@pytest.mark.asyncio
async def test_cache_service_negative_caching() -> None:
    backend = MemoryCacheBackend()
    options = CacheModuleOptions(
        default_negative_soft_ttl_ms=1000,
        default_negative_hard_ttl_ms=5000,
    )
    service = CacheService(backend, options)

    calls = 0

    async def null_loader() -> str | None:
        nonlocal calls
        calls += 1
        return None

    res = await service.get_or_set(
        "missing:item",
        null_loader,
        CacheGetOrSetOptions(cache_null=True),
    )
    assert res is None
    assert calls == 1

    # Second call should hit the negative cache envelope
    res2 = await service.get_or_set(
        "missing:item",
        null_loader,
        CacheGetOrSetOptions(cache_null=True),
    )
    assert res2 is None
    assert calls == 1


@pytest.mark.asyncio
async def test_redis_loader_lock() -> None:
    redis_mock = AsyncMock()
    redis_mock.set = AsyncMock(return_value=True)
    redis_mock.eval = AsyncMock(return_value=1)

    ok = await acquire_redis_loader_lock(redis_mock, "lock:k", "token-1", 5000)
    assert ok is True
    redis_mock.set.assert_awaited_once_with("lock:k", "token-1", px=5000, nx=True)

    await release_redis_loader_lock(redis_mock, "lock:k", "token-1")
    assert redis_mock.eval.await_count == 1

    lock = RedisCacheLoaderLock(redis_mock)
    tok = await lock.try_acquire("lock:2", 3000)
    assert tok is not None
    await lock.release("lock:2", tok)
