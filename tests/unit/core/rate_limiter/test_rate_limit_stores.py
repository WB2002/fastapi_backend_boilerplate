from __future__ import annotations

import pytest

from app.core.rate_limiter.stores import (
    REDIS_LUA_SCRIPTS,
    ConsumeContext,
    ConsumeResult,
    FallbackRateLimitBackend,
    MemoryRateLimitBackend,
    RateLimitAlgorithm,
    RedisRateLimitBackend,
)

ALGORITHMS = [
    RateLimitAlgorithm.TOKEN_BUCKET,
    RateLimitAlgorithm.LEAKY_BUCKET,
    RateLimitAlgorithm.FIXED_WINDOW_COUNTER,
    RateLimitAlgorithm.SLIDING_WINDOW_LOG,
    RateLimitAlgorithm.SLIDING_WINDOW_COUNTER,
    RateLimitAlgorithm.EXPONENTIAL_WEIGHTED_MOVING_AVERAGE,
]


def _ctx(
    algorithm: RateLimitAlgorithm,
    now_ms: int,
    *,
    key: str = "k",
    limit: int = 3,
    cost: int = 1,
) -> ConsumeContext:
    return ConsumeContext(
        algorithm=algorithm,
        key=key,
        limit=limit,
        duration_ms=60_000,
        cost=cost,
        now_ms=now_ms,
    )


def test_six_algorithms_and_six_lua_scripts() -> None:
    assert len(set(RateLimitAlgorithm)) == 6
    assert len(REDIS_LUA_SCRIPTS) == 6


@pytest.mark.parametrize("algorithm", ALGORITHMS)
async def test_memory_backend_allows_limit_then_denies(
    algorithm: RateLimitAlgorithm,
) -> None:
    backend = MemoryRateLimitBackend()
    now = 1_000_000
    results = [await backend.consume(_ctx(algorithm, now)) for _ in range(4)]
    assert [r.allowed for r in results[:3]] == [True, True, True]
    assert results[3].allowed is False
    assert results[3].retry_after_ms > 0


async def test_fixed_window_remaining_and_reset() -> None:
    backend = MemoryRateLimitBackend()
    now = 5_000_000
    first = await backend.consume(_ctx(RateLimitAlgorithm.FIXED_WINDOW_COUNTER, now))
    assert first.allowed is True
    assert first.remaining == 2
    assert 0 < first.reset_ms <= 60_000
    later = await backend.consume(
        _ctx(RateLimitAlgorithm.FIXED_WINDOW_COUNTER, now + 120_000)
    )
    assert later.allowed is True
    assert later.remaining == 2


async def test_keys_are_isolated() -> None:
    backend = MemoryRateLimitBackend()
    algo = RateLimitAlgorithm.FIXED_WINDOW_COUNTER
    for _ in range(3):
        await backend.consume(_ctx(algo, 1, key="a"))
    assert (await backend.consume(_ctx(algo, 1, key="a"))).allowed is False
    assert (await backend.consume(_ctx(algo, 1, key="b"))).allowed is True


async def test_memory_backend_evicts_when_over_capacity() -> None:
    backend = MemoryRateLimitBackend(max_keys=10)
    algo = RateLimitAlgorithm.FIXED_WINDOW_COUNTER
    for i in range(25):
        await backend.consume(_ctx(algo, 1, key=f"k{i}"))
    assert (await backend.consume(_ctx(algo, 1, key="k24"))).allowed is True


class _ScriptRedis:
    def __init__(self) -> None:
        self.loaded: list[str] = []
        self.calls: list[tuple[str, tuple[object, ...]]] = []
        self.fail_noscript_once = False

    async def script_load(self, source: str) -> str:
        self.loaded.append(source)
        return f"sha{len(self.loaded)}"

    async def evalsha(self, sha: str, numkeys: int, *args: object) -> list[int]:
        if self.fail_noscript_once:
            self.fail_noscript_once = False
            raise RuntimeError("NOSCRIPT No matching script")
        self.calls.append((sha, args))
        return [1, 2, 3000, 0]

    async def eval(self, source: str, numkeys: int, *args: object) -> list[int]:
        self.calls.append(("eval", args))
        return [0, 0, 4000, 4000]


async def test_redis_backend_loads_scripts_and_maps_result() -> None:
    client = _ScriptRedis()
    backend = RedisRateLimitBackend(client)
    result = await backend.consume(_ctx(RateLimitAlgorithm.TOKEN_BUCKET, 10))
    assert len(client.loaded) == 6
    assert result == ConsumeResult(
        allowed=True, remaining=2, reset_ms=3000, retry_after_ms=0
    )
    sha, args = client.calls[0]
    assert sha.startswith("sha")
    assert args[0] == "k"
    assert args[-1] == "3"


async def test_redis_backend_falls_back_to_eval_after_noscript() -> None:
    client = _ScriptRedis()
    client.fail_noscript_once = True
    backend = RedisRateLimitBackend(client)
    result = await backend.consume(_ctx(RateLimitAlgorithm.FIXED_WINDOW_COUNTER, 10))
    assert result.allowed is False
    assert client.calls[-1][0] == "eval"


class _FailingBackend:
    def __init__(self) -> None:
        self.calls = 0

    async def consume(self, context: object) -> ConsumeResult:
        self.calls += 1
        raise ConnectionError("redis down")


async def test_fallback_degrades_with_cooldown_and_scales_limit() -> None:
    clock = [1_000]
    primary = _FailingBackend()
    memory = MemoryRateLimitBackend()
    backend = FallbackRateLimitBackend(
        primary,
        memory,
        instance_count=2,
        cooldown_ms=5_000,
        now_ms=lambda: clock[0],
    )
    algo = RateLimitAlgorithm.FIXED_WINDOW_COUNTER
    first = await backend.consume(_ctx(algo, 1_000, limit=4))
    assert first.allowed is True
    assert primary.calls == 1
    assert backend.degraded_until_ms == 6_000

    # Limit 4 / 2 instances = 2 allowed total (one already consumed).
    assert (await backend.consume(_ctx(algo, 1_000, limit=4))).allowed is True
    assert (await backend.consume(_ctx(algo, 1_000, limit=4))).allowed is False
    assert primary.calls == 1

    clock[0] = 6_001
    await backend.consume(_ctx(algo, 1_000, limit=4))
    assert primary.calls == 2


async def test_fallback_uses_primary_when_healthy() -> None:
    primary = MemoryRateLimitBackend()
    backend = FallbackRateLimitBackend(primary, MemoryRateLimitBackend())
    res = await backend.consume(_ctx(RateLimitAlgorithm.FIXED_WINDOW_COUNTER, 1))
    assert res.allowed is True
    assert backend.degraded_until_ms == 0
