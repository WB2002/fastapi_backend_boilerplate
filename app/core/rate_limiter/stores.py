from __future__ import annotations

import inspect
import math
import time
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Final, Literal, Protocol

from app.common.config.resolve_coordination_storage import RateLimitStorage
from app.core.logger.logger_config import get_logger

MEMORY_BACKEND_MAX_KEYS: Final[int] = 50_000
MEMORY_KEY_TTL_MULTIPLIER: Final[int] = 2
MEMORY_EVICT_RATIO: Final[float] = 0.1


class RateLimitAlgorithm(StrEnum):
    TOKEN_BUCKET = "TokenBucket"  # noqa: S105
    LEAKY_BUCKET = "LeakyBucket"
    FIXED_WINDOW_COUNTER = "FixedWindowCounter"
    SLIDING_WINDOW_LOG = "SlidingWindowLog"
    SLIDING_WINDOW_COUNTER = "SlidingWindowCounter"
    EXPONENTIAL_WEIGHTED_MOVING_AVERAGE = "ExponentialWeightedMovingAverage"
    GCRA = "ExponentialWeightedMovingAverage"

    # CamelCase aliases matching TypeScript enum member names
    TokenBucket = "TokenBucket"
    LeakyBucket = "LeakyBucket"
    FixedWindowCounter = "FixedWindowCounter"
    SlidingWindowLog = "SlidingWindowLog"
    SlidingWindowCounter = "SlidingWindowCounter"
    ExponentialWeightedMovingAverage = "ExponentialWeightedMovingAverage"


class RateLimitIdentifier(StrEnum):
    IP = "Ip"
    USER_ID = "UserId"
    DEVICE_FINGERPRINT = "DeviceFingerprint"
    GUEST_ID = "GuestId"

    # CamelCase aliases matching TypeScript enum member names
    Ip = "Ip"
    UserId = "UserId"
    DeviceFingerprint = "DeviceFingerprint"
    GuestId = "GuestId"


def normalize_algorithm(raw: RateLimitAlgorithm | str | None) -> RateLimitAlgorithm:
    if isinstance(raw, RateLimitAlgorithm):
        return raw
    if not isinstance(raw, str):
        return RateLimitAlgorithm.FIXED_WINDOW_COUNTER
    norm = raw.strip().lower().replace("_", "-")
    if norm in ("tokenbucket", "token-bucket"):
        return RateLimitAlgorithm.TOKEN_BUCKET
    if norm in ("leakybucket", "leaky-bucket"):
        return RateLimitAlgorithm.LEAKY_BUCKET
    if norm in ("fixedwindowcounter", "fixed-window-counter", "fixed-window"):
        return RateLimitAlgorithm.FIXED_WINDOW_COUNTER
    if norm in ("slidingwindowlog", "sliding-window-log", "sliding-log"):
        return RateLimitAlgorithm.SLIDING_WINDOW_LOG
    if norm in ("slidingwindowcounter", "sliding-window-counter", "sliding-counter"):
        return RateLimitAlgorithm.SLIDING_WINDOW_COUNTER
    if norm in (
        "exponentialweightedmovingaverage",
        "exponential-weighted-moving-average",
        "ewma",
        "gcra",
    ):
        return RateLimitAlgorithm.EXPONENTIAL_WEIGHTED_MOVING_AVERAGE
    return RateLimitAlgorithm.FIXED_WINDOW_COUNTER


def normalize_identifier(raw: RateLimitIdentifier | str | None) -> RateLimitIdentifier:
    if isinstance(raw, RateLimitIdentifier):
        return raw
    if not isinstance(raw, str):
        return RateLimitIdentifier.IP
    norm = raw.strip().lower().replace("_", "-")
    if norm in ("userid", "user-id", "user"):
        return RateLimitIdentifier.USER_ID
    if norm in ("devicefingerprint", "device-fingerprint", "fingerprint"):
        return RateLimitIdentifier.DEVICE_FINGERPRINT
    if norm in ("guestid", "guest-id", "guest"):
        return RateLimitIdentifier.GUEST_ID
    return RateLimitIdentifier.IP


@dataclass(frozen=True, slots=True)
class ConsumeContext:
    algorithm: RateLimitAlgorithm | str
    key: str
    limit: int
    duration_ms: int
    cost: int
    now_ms: int
    burst: int | None = None
    policy: str | None = None

    @property
    def durationMs(self) -> int:
        return self.duration_ms

    @property
    def nowMs(self) -> int:
        return self.now_ms

    def __getitem__(self, item: str) -> Any:
        if item == "durationMs":
            return self.duration_ms
        if item == "nowMs":
            return self.now_ms
        return getattr(self, item)


@dataclass(frozen=True, slots=True)
class ConsumeResult:
    allowed: bool
    remaining: int
    reset_ms: int
    retry_after_ms: int

    @property
    def resetMs(self) -> int:
        return self.reset_ms

    @property
    def retryAfterMs(self) -> int:
        return self.retry_after_ms

    def __getitem__(self, item: str) -> Any:
        if item == "resetMs":
            return self.reset_ms
        if item == "retryAfterMs":
            return self.retry_after_ms
        return getattr(self, item)


def _coerce_consume_context(ctx: ConsumeContext | Mapping[str, Any]) -> ConsumeContext:
    if isinstance(ctx, ConsumeContext):
        return ctx
    raw_algo = ctx.get("algorithm", RateLimitAlgorithm.FIXED_WINDOW_COUNTER)
    duration_ms = int(
        ctx["duration_ms"] if "duration_ms" in ctx else ctx.get("durationMs", 60_000)
    )
    now_ms = int(
        ctx["now_ms"] if "now_ms" in ctx else ctx.get("nowMs", int(time.time() * 1000))
    )
    burst_raw = ctx.get("burst")
    return ConsumeContext(
        algorithm=raw_algo,
        key=str(ctx["key"]),
        limit=int(ctx.get("limit", 10)),
        duration_ms=duration_ms,
        cost=int(ctx.get("cost", 1)),
        now_ms=now_ms,
        burst=int(burst_raw) if burst_raw is not None else None,
        policy=str(ctx["policy"]) if ctx.get("policy") is not None else None,
    )


class RateLimitBackend(Protocol):
    async def consume(
        self,
        context: ConsumeContext | Mapping[str, Any],
    ) -> ConsumeResult: ...


type _MemoryStoreName = Literal[
    "tokenBuckets",
    "leakyBuckets",
    "fixedWindows",
    "slidingLogs",
    "slidingCounters",
    "ewmas",
]


@dataclass(slots=True)
class _TokenBucketState:
    tokens: float
    last_refill_at: int


@dataclass(slots=True)
class _LeakyBucketState:
    level: float
    last_leak_at: int


@dataclass(slots=True)
class _FixedWindowState:
    window_start: int
    count: int


@dataclass(slots=True)
class _SlidingLogEntry:
    ts: int
    cost: int


@dataclass(slots=True)
class _SlidingCounterState:
    window_start: int
    previous_count: int
    current_count: int


@dataclass(slots=True)
class _EwmaState:
    ewma: float
    last_at: int


@dataclass(slots=True)
class _MemoryKeyMeta:
    expires_at: int
    last_access: int
    store: _MemoryStoreName


class MemoryRateLimitBackend:
    """In-memory store for all 6 algorithms with TTL + LRU eviction."""

    def __init__(self, max_keys: int = MEMORY_BACKEND_MAX_KEYS) -> None:
        self.max_keys = max(1, int(max_keys))
        self._token_buckets: dict[str, _TokenBucketState] = {}
        self._leaky_buckets: dict[str, _LeakyBucketState] = {}
        self._fixed_windows: dict[str, _FixedWindowState] = {}
        self._sliding_logs: dict[str, list[_SlidingLogEntry]] = {}
        self._sliding_counters: dict[str, _SlidingCounterState] = {}
        self._ewmas: dict[str, _EwmaState] = {}
        self._meta: dict[str, _MemoryKeyMeta] = {}

    @property
    def size(self) -> int:
        return len(self._meta)

    async def consume(
        self,
        context: ConsumeContext | Mapping[str, Any],
    ) -> ConsumeResult:
        ctx = _coerce_consume_context(context)
        self._evict_expired(ctx.now_ms)
        self._enforce_cap()

        algo = normalize_algorithm(ctx.algorithm)
        if algo == RateLimitAlgorithm.TOKEN_BUCKET:
            return self._token_bucket(ctx)
        if algo == RateLimitAlgorithm.LEAKY_BUCKET:
            return self._leaky_bucket(ctx)
        if algo == RateLimitAlgorithm.FIXED_WINDOW_COUNTER:
            return self._fixed_window(ctx)
        if algo == RateLimitAlgorithm.SLIDING_WINDOW_LOG:
            return self._sliding_window_log(ctx)
        if algo == RateLimitAlgorithm.SLIDING_WINDOW_COUNTER:
            return self._sliding_window_counter(ctx)
        if algo == RateLimitAlgorithm.EXPONENTIAL_WEIGHTED_MOVING_AVERAGE:
            return self._ewma(ctx)
        return self._fixed_window(ctx)

    def _touch(
        self,
        key: str,
        store: _MemoryStoreName,
        ctx: ConsumeContext,
    ) -> None:
        self._meta[key] = _MemoryKeyMeta(
            expires_at=ctx.now_ms + ctx.duration_ms * MEMORY_KEY_TTL_MULTIPLIER,
            last_access=ctx.now_ms,
            store=store,
        )

    def _evict_expired(self, now_ms: int) -> None:
        expired = [
            (key, info.store)
            for key, info in self._meta.items()
            if info.expires_at <= now_ms
        ]
        for key, store in expired:
            self._delete_key(key, store)

    def _enforce_cap(self) -> None:
        if len(self._meta) < self.max_keys:
            return
        sorted_entries = sorted(
            self._meta.items(),
            key=lambda item: item[1].last_access,
        )
        to_remove = max(1, math.floor(self.max_keys * MEMORY_EVICT_RATIO))
        for key, info in sorted_entries[:to_remove]:
            self._delete_key(key, info.store)

    def _delete_key(self, key: str, store: _MemoryStoreName) -> None:
        self._meta.pop(key, None)
        if store == "tokenBuckets":
            self._token_buckets.pop(key, None)
        elif store == "leakyBuckets":
            self._leaky_buckets.pop(key, None)
        elif store == "fixedWindows":
            self._fixed_windows.pop(key, None)
        elif store == "slidingLogs":
            self._sliding_logs.pop(key, None)
        elif store == "slidingCounters":
            self._sliding_counters.pop(key, None)
        elif store == "ewmas":
            self._ewmas.pop(key, None)

    def _token_bucket(self, ctx: ConsumeContext) -> ConsumeResult:
        capacity = float(
            ctx.burst if ctx.burst is not None and ctx.burst > 0 else ctx.limit
        )
        refill_per_ms = ctx.limit / ctx.duration_ms
        state = self._token_buckets.get(ctx.key)
        if state is None:
            state = _TokenBucketState(tokens=capacity, last_refill_at=ctx.now_ms)
            self._token_buckets[ctx.key] = state
        self._touch(ctx.key, "tokenBuckets", ctx)

        elapsed = max(0, ctx.now_ms - state.last_refill_at)
        state.tokens = min(capacity, state.tokens + elapsed * refill_per_ms)
        state.last_refill_at = ctx.now_ms

        if state.tokens < ctx.cost:
            deficit = ctx.cost - state.tokens
            retry_after_ms = (
                math.ceil(deficit / refill_per_ms)
                if refill_per_ms > 0
                else ctx.duration_ms
            )
            reset_ms = (
                math.ceil((capacity - state.tokens) / refill_per_ms)
                if refill_per_ms > 0
                else ctx.duration_ms
            )
            return ConsumeResult(
                allowed=False,
                remaining=math.floor(state.tokens),
                reset_ms=reset_ms,
                retry_after_ms=retry_after_ms,
            )

        state.tokens -= ctx.cost
        reset_ms = (
            math.ceil((capacity - state.tokens) / refill_per_ms)
            if refill_per_ms > 0
            else ctx.duration_ms
        )
        return ConsumeResult(
            allowed=True,
            remaining=math.floor(state.tokens),
            reset_ms=reset_ms,
            retry_after_ms=0,
        )

    def _leaky_bucket(self, ctx: ConsumeContext) -> ConsumeResult:
        capacity = float(
            ctx.burst if ctx.burst is not None and ctx.burst > 0 else ctx.limit
        )
        leak_per_ms = ctx.limit / ctx.duration_ms
        state = self._leaky_buckets.get(ctx.key)
        if state is None:
            state = _LeakyBucketState(level=0.0, last_leak_at=ctx.now_ms)
            self._leaky_buckets[ctx.key] = state
        self._touch(ctx.key, "leakyBuckets", ctx)

        elapsed = max(0, ctx.now_ms - state.last_leak_at)
        state.level = max(0.0, state.level - elapsed * leak_per_ms)
        state.last_leak_at = ctx.now_ms

        if state.level + ctx.cost > capacity:
            overflow = state.level + ctx.cost - capacity
            retry_after_ms = (
                math.ceil(overflow / leak_per_ms)
                if leak_per_ms > 0
                else ctx.duration_ms
            )
            reset_ms = (
                math.ceil(state.level / leak_per_ms)
                if leak_per_ms > 0
                else ctx.duration_ms
            )
            return ConsumeResult(
                allowed=False,
                remaining=max(0, math.floor(capacity - state.level)),
                reset_ms=reset_ms,
                retry_after_ms=retry_after_ms,
            )

        state.level += ctx.cost
        reset_ms = (
            math.ceil(state.level / leak_per_ms) if leak_per_ms > 0 else ctx.duration_ms
        )
        return ConsumeResult(
            allowed=True,
            remaining=max(0, math.floor(capacity - state.level)),
            reset_ms=reset_ms,
            retry_after_ms=0,
        )

    def _fixed_window(self, ctx: ConsumeContext) -> ConsumeResult:
        state = self._fixed_windows.get(ctx.key)
        if state is None or ctx.now_ms >= state.window_start + ctx.duration_ms:
            state = _FixedWindowState(window_start=ctx.now_ms, count=0)
            self._fixed_windows[ctx.key] = state
        self._touch(ctx.key, "fixedWindows", ctx)

        reset_ms = max(0, state.window_start + ctx.duration_ms - ctx.now_ms)
        if state.count + ctx.cost > ctx.limit:
            return ConsumeResult(
                allowed=False,
                remaining=max(0, ctx.limit - state.count),
                reset_ms=reset_ms,
                retry_after_ms=reset_ms,
            )

        state.count += ctx.cost
        return ConsumeResult(
            allowed=True,
            remaining=max(0, ctx.limit - state.count),
            reset_ms=reset_ms,
            retry_after_ms=0,
        )

    def _sliding_window_log(self, ctx: ConsumeContext) -> ConsumeResult:
        cutoff = ctx.now_ms - ctx.duration_ms
        entries = [e for e in self._sliding_logs.get(ctx.key, []) if e.ts > cutoff]
        used = sum(e.cost for e in entries)

        if used + ctx.cost > ctx.limit:
            oldest = entries[0] if entries else None
            retry_after_ms = (
                max(0, oldest.ts + ctx.duration_ms - ctx.now_ms)
                if oldest is not None
                else ctx.duration_ms
            )
            self._sliding_logs[ctx.key] = entries
            self._touch(ctx.key, "slidingLogs", ctx)
            return ConsumeResult(
                allowed=False,
                remaining=max(0, ctx.limit - used),
                reset_ms=retry_after_ms,
                retry_after_ms=retry_after_ms,
            )

        entries.append(_SlidingLogEntry(ts=ctx.now_ms, cost=ctx.cost))
        self._sliding_logs[ctx.key] = entries
        self._touch(ctx.key, "slidingLogs", ctx)
        new_used = used + ctx.cost
        oldest = entries[0] if entries else None
        reset_ms = (
            max(0, oldest.ts + ctx.duration_ms - ctx.now_ms)
            if oldest is not None
            else ctx.duration_ms
        )
        return ConsumeResult(
            allowed=True,
            remaining=max(0, ctx.limit - new_used),
            reset_ms=reset_ms,
            retry_after_ms=0,
        )

    def _sliding_window_counter(self, ctx: ConsumeContext) -> ConsumeResult:
        state = self._sliding_counters.get(ctx.key)
        if state is None:
            state = _SlidingCounterState(
                window_start=ctx.now_ms,
                previous_count=0,
                current_count=0,
            )
            self._sliding_counters[ctx.key] = state
        self._touch(ctx.key, "slidingCounters", ctx)

        while ctx.now_ms >= state.window_start + ctx.duration_ms:
            state.previous_count = state.current_count
            state.current_count = 0
            state.window_start += ctx.duration_ms
            if ctx.now_ms >= state.window_start + ctx.duration_ms * 2:
                state.previous_count = 0
                state.current_count = 0
                state.window_start = ctx.now_ms
                break

        elapsed = ctx.now_ms - state.window_start
        weight = 1.0 - (elapsed / ctx.duration_ms)
        estimate = state.previous_count * weight + state.current_count
        reset_ms = max(0, state.window_start + ctx.duration_ms - ctx.now_ms)

        if estimate + ctx.cost > ctx.limit:
            return ConsumeResult(
                allowed=False,
                remaining=max(0, math.floor(ctx.limit - estimate)),
                reset_ms=reset_ms,
                retry_after_ms=reset_ms,
            )

        state.current_count += ctx.cost
        new_estimate = state.previous_count * weight + state.current_count
        return ConsumeResult(
            allowed=True,
            remaining=max(0, math.floor(ctx.limit - new_estimate)),
            reset_ms=reset_ms,
            retry_after_ms=0,
        )

    def _ewma(self, ctx: ConsumeContext) -> ConsumeResult:
        state = self._ewmas.get(ctx.key)
        if state is None:
            state = _EwmaState(ewma=0.0, last_at=ctx.now_ms)
            self._ewmas[ctx.key] = state
        self._touch(ctx.key, "ewmas", ctx)

        dt = max(0, ctx.now_ms - state.last_at)
        decay = math.exp(-dt / ctx.duration_ms)
        projected = state.ewma * decay + ctx.cost

        if projected > ctx.limit:
            retry_after_ms = ctx.duration_ms
            if state.ewma > 0 and ctx.cost <= ctx.limit:
                target = (ctx.limit - ctx.cost) / state.ewma
                if target > 0:
                    retry_after_ms = math.ceil(-ctx.duration_ms * math.log(target))
            state.last_at = ctx.now_ms
            return ConsumeResult(
                allowed=False,
                remaining=max(0, math.floor(ctx.limit - state.ewma * decay)),
                reset_ms=retry_after_ms,
                retry_after_ms=retry_after_ms,
            )

        state.ewma = projected
        state.last_at = ctx.now_ms
        return ConsumeResult(
            allowed=True,
            remaining=max(0, math.floor(ctx.limit - state.ewma)),
            reset_ms=ctx.duration_ms,
            retry_after_ms=0,
        )


FIXED_WINDOW_LUA: Final[str] = """-- KEYS[1] = key
-- ARGV: nowMs, limit, durationMs, cost
local key = KEYS[1]
local now = tonumber(ARGV[1])
local limit = tonumber(ARGV[2])
local duration = tonumber(ARGV[3])
local cost = tonumber(ARGV[4])

local data = redis.call('HMGET', key, 'start', 'count')
local start = tonumber(data[1])
local count = tonumber(data[2])

if start == nil or now >= start + duration then
  start = now
  count = 0
end

local reset = math.max(0, start + duration - now)

if count + cost > limit then
  redis.call('HSET', key, 'start', start, 'count', count)
  redis.call('PEXPIRE', key, duration * 2)
  return {0, math.max(0, limit - count), reset, reset}
end

count = count + cost
redis.call('HSET', key, 'start', start, 'count', count)
redis.call('PEXPIRE', key, duration * 2)
return {1, math.max(0, limit - count), reset, 0}
"""

TOKEN_BUCKET_LUA: Final[str] = """-- KEYS[1] = key
-- ARGV: nowMs, limit, durationMs, cost, burst
local key = KEYS[1]
local now = tonumber(ARGV[1])
local limit = tonumber(ARGV[2])
local duration = tonumber(ARGV[3])
local cost = tonumber(ARGV[4])
local burst = tonumber(ARGV[5])
local capacity = burst
if capacity <= 0 then capacity = limit end

local data = redis.call('HMGET', key, 'tokens', 'last')
local tokens = tonumber(data[1])
local last = tonumber(data[2])
if tokens == nil then
  tokens = capacity
  last = now
end

local refill = limit / duration
local elapsed = math.max(0, now - last)
tokens = math.min(capacity, tokens + elapsed * refill)

if tokens < cost then
  local deficit = cost - tokens
  local retry = 0
  local reset = 0
  if refill > 0 then
    retry = math.ceil(deficit / refill)
    reset = math.ceil((capacity - tokens) / refill)
  else
    retry = duration
    reset = duration
  end
  redis.call('HSET', key, 'tokens', tokens, 'last', now)
  redis.call('PEXPIRE', key, duration * 2)
  return {0, math.floor(tokens), reset, retry}
end

tokens = tokens - cost
local reset = 0
if refill > 0 then
  reset = math.ceil((capacity - tokens) / refill)
else
  reset = duration
end
redis.call('HSET', key, 'tokens', tokens, 'last', now)
redis.call('PEXPIRE', key, duration * 2)
return {1, math.floor(tokens), reset, 0}
"""  # noqa: S105

LEAKY_BUCKET_LUA: Final[str] = """-- KEYS[1] = key
-- ARGV: nowMs, limit, durationMs, cost, burst
local key = KEYS[1]
local now = tonumber(ARGV[1])
local limit = tonumber(ARGV[2])
local duration = tonumber(ARGV[3])
local cost = tonumber(ARGV[4])
local burst = tonumber(ARGV[5])
local capacity = burst
if capacity <= 0 then capacity = limit end

local data = redis.call('HMGET', key, 'level', 'last')
local level = tonumber(data[1])
local last = tonumber(data[2])
if level == nil then
  level = 0
  last = now
end

local leak = limit / duration
local elapsed = math.max(0, now - last)
level = math.max(0, level - elapsed * leak)

if level + cost > capacity then
  local overflow = level + cost - capacity
  local retry = 0
  local reset = 0
  if leak > 0 then
    retry = math.ceil(overflow / leak)
    reset = math.ceil(level / leak)
  else
    retry = duration
    reset = duration
  end
  redis.call('HSET', key, 'level', level, 'last', now)
  redis.call('PEXPIRE', key, duration * 2)
  return {0, math.max(0, math.floor(capacity - level)), reset, retry}
end

level = level + cost
local reset = 0
if leak > 0 then
  reset = math.ceil(level / leak)
else
  reset = duration
end
redis.call('HSET', key, 'level', level, 'last', now)
redis.call('PEXPIRE', key, duration * 2)
return {1, math.max(0, math.floor(capacity - level)), reset, 0}
"""

SLIDING_WINDOW_LOG_LUA: Final[
    str
] = """-- KEYS[1] = key (sorted set: member = unique id, score = ts)
-- ARGV: nowMs, limit, durationMs, cost, member
local key = KEYS[1]
local now = tonumber(ARGV[1])
local limit = tonumber(ARGV[2])
local duration = tonumber(ARGV[3])
local cost = tonumber(ARGV[4])
local member = ARGV[5]
local cutoff = now - duration

redis.call('ZREMRANGEBYSCORE', key, '-inf', cutoff)

local entries = redis.call('ZRANGE', key, 0, -1, 'WITHSCORES')
local used = 0
-- members encoded as cost:uuid — sum costs
for i = 1, #entries, 2 do
  local m = entries[i]
  local c = tonumber(string.match(m, '^(%d+):')) or 1
  used = used + c
end

if used + cost > limit then
  local oldestScore = redis.call('ZRANGE', key, 0, 0, 'WITHSCORES')
  local retry = duration
  if #oldestScore >= 2 then
    retry = math.max(0, tonumber(oldestScore[2]) + duration - now)
  end
  redis.call('PEXPIRE', key, duration * 2)
  return {0, math.max(0, limit - used), retry, retry}
end

redis.call('ZADD', key, now, cost .. ':' .. member)
redis.call('PEXPIRE', key, duration * 2)
local newUsed = used + cost
local oldestScore = redis.call('ZRANGE', key, 0, 0, 'WITHSCORES')
local reset = duration
if #oldestScore >= 2 then
  reset = math.max(0, tonumber(oldestScore[2]) + duration - now)
end
return {1, math.max(0, limit - newUsed), reset, 0}
"""

SLIDING_WINDOW_COUNTER_LUA: Final[str] = """-- KEYS[1] = key
-- ARGV: nowMs, limit, durationMs, cost
local key = KEYS[1]
local now = tonumber(ARGV[1])
local limit = tonumber(ARGV[2])
local duration = tonumber(ARGV[3])
local cost = tonumber(ARGV[4])

local data = redis.call('HMGET', key, 'start', 'prev', 'curr')
local start = tonumber(data[1])
local prev = tonumber(data[2]) or 0
local curr = tonumber(data[3]) or 0

if start == nil then
  start = now
  prev = 0
  curr = 0
end

while now >= start + duration do
  prev = curr
  curr = 0
  start = start + duration
  if now >= start + duration * 2 then
    prev = 0
    curr = 0
    start = now
    break
  end
end

local elapsed = now - start
local weight = 1 - (elapsed / duration)
local estimate = prev * weight + curr
local reset = math.max(0, start + duration - now)

if estimate + cost > limit then
  redis.call('HSET', key, 'start', start, 'prev', prev, 'curr', curr)
  redis.call('PEXPIRE', key, duration * 2)
  return {0, math.max(0, math.floor(limit - estimate)), reset, reset}
end

curr = curr + cost
estimate = prev * weight + curr
redis.call('HSET', key, 'start', start, 'prev', prev, 'curr', curr)
redis.call('PEXPIRE', key, duration * 2)
return {1, math.max(0, math.floor(limit - estimate)), reset, 0}
"""

EWMA_LUA: Final[str] = """-- KEYS[1] = key
-- ARGV: nowMs, limit, durationMs, cost
local key = KEYS[1]
local now = tonumber(ARGV[1])
local limit = tonumber(ARGV[2])
local duration = tonumber(ARGV[3])
local cost = tonumber(ARGV[4])

local data = redis.call('HMGET', key, 'ewma', 'last')
local ewma = tonumber(data[1])
local last = tonumber(data[2])
if ewma == nil then
  ewma = 0
  last = now
end

local dt = math.max(0, now - last)
local decay = math.exp(-dt / duration)
local projected = ewma * decay + cost

if projected > limit then
  local retry = duration
  if ewma > 0 and cost <= limit then
    local target = (limit - cost) / ewma
    if target > 0 then
      retry = math.ceil(-duration * math.log(target))
    end
  end
  redis.call('HSET', key, 'ewma', ewma, 'last', now)
  redis.call('PEXPIRE', key, duration * 2)
  return {0, math.max(0, math.floor(limit - ewma * decay)), retry, retry}
end

ewma = projected
redis.call('HSET', key, 'ewma', ewma, 'last', now)
redis.call('PEXPIRE', key, duration * 2)
return {1, math.max(0, math.floor(limit - ewma)), duration, 0}
"""

type RedisLuaScriptName = Literal[
    "token_bucket",
    "leaky_bucket",
    "fixed_window",
    "sliding_window_log",
    "sliding_window_counter",
    "ewma",
]

REDIS_LUA_SCRIPTS: Final[dict[RedisLuaScriptName, str]] = {
    "token_bucket": TOKEN_BUCKET_LUA,
    "leaky_bucket": LEAKY_BUCKET_LUA,
    "fixed_window": FIXED_WINDOW_LUA,
    "sliding_window_log": SLIDING_WINDOW_LOG_LUA,
    "sliding_window_counter": SLIDING_WINDOW_COUNTER_LUA,
    "ewma": EWMA_LUA,
}


class RedisRateLimitBackend:
    """Redis rate limit backend executing atomic Lua scripts via EVALSHA/EVAL."""

    def __init__(self, redis_client: Any) -> None:
        self._redis = redis_client
        self._logger = get_logger("RedisRateLimitBackend")
        self.scripts: dict[RedisLuaScriptName, str] = dict(REDIS_LUA_SCRIPTS)
        self._shas: dict[RedisLuaScriptName, str] = {}
        self._loaded = False

    def _resolve_client(self) -> Any:
        get_client = getattr(self._redis, "get_client", None) or getattr(
            self._redis, "getClient", None
        )
        if callable(get_client):
            return get_client()
        return self._redis

    async def ensure_scripts_loaded(self) -> None:
        if self._loaded:
            return
        client = self._resolve_client()
        script_load_fn = getattr(client, "script_load", None)
        script_fn = getattr(client, "script", None)

        if not callable(script_load_fn) and not callable(script_fn):
            self._loaded = True
            return

        for name, source in self.scripts.items():
            if callable(script_load_fn):
                res = script_load_fn(source)
                sha = await res if inspect.isawaitable(res) else res
                self._shas[name] = str(sha)
            elif callable(script_fn):
                res = script_fn("LOAD", source)
                sha = await res if inspect.isawaitable(res) else res
                self._shas[name] = str(sha)
        self._loaded = True

    async def ensureScriptsLoaded(self) -> None:
        await self.ensure_scripts_loaded()

    async def _eval_script(
        self,
        name: RedisLuaScriptName,
        key: str,
        args: list[str],
    ) -> list[int]:
        client = self._resolve_client()
        sha = self._shas.get(name)
        evalsha_fn = getattr(client, "evalsha", None)

        if sha and callable(evalsha_fn):
            try:
                res = evalsha_fn(sha, 1, key, *args)
                raw = await res if inspect.isawaitable(res) else res
                return [int(x) for x in raw]
            except Exception as exc:
                msg = str(exc)
                if "NOSCRIPT" not in msg:
                    raise
                self._logger.warn(f"Redis NOSCRIPT for {name}; reloading scripts")
                self._loaded = False
                await self.ensure_scripts_loaded()

        eval_fn = getattr(client, "eval", None)
        if not callable(eval_fn):
            raise RuntimeError("Redis client does not support eval")
        res = eval_fn(self.scripts[name], 1, key, *args)
        raw = await res if inspect.isawaitable(res) else res
        return [int(x) for x in raw]

    async def consume(
        self,
        context: ConsumeContext | Mapping[str, Any],
    ) -> ConsumeResult:
        await self.ensure_scripts_loaded()
        ctx = _coerce_consume_context(context)

        burst = ctx.burst if ctx.burst is not None else ctx.limit
        base_args = [
            str(ctx.now_ms),
            str(ctx.limit),
            str(ctx.duration_ms),
            str(ctx.cost),
        ]

        algo = normalize_algorithm(ctx.algorithm)
        name: RedisLuaScriptName
        eval_args: list[str]
        if algo == RateLimitAlgorithm.TOKEN_BUCKET:
            name = "token_bucket"
            eval_args = [*base_args, str(burst)]
        elif algo == RateLimitAlgorithm.LEAKY_BUCKET:
            name = "leaky_bucket"
            eval_args = [*base_args, str(burst)]
        elif algo == RateLimitAlgorithm.SLIDING_WINDOW_LOG:
            name = "sliding_window_log"
            eval_args = [*base_args, str(uuid.uuid4())]
        elif algo == RateLimitAlgorithm.SLIDING_WINDOW_COUNTER:
            name = "sliding_window_counter"
            eval_args = base_args
        elif algo == RateLimitAlgorithm.EXPONENTIAL_WEIGHTED_MOVING_AVERAGE:
            name = "ewma"
            eval_args = base_args
        else:
            name = "fixed_window"
            eval_args = base_args

        raw = await self._eval_script(name, ctx.key, eval_args)
        return ConsumeResult(
            allowed=int(raw[0]) == 1,
            remaining=int(raw[1]),
            reset_ms=int(raw[2]),
            retry_after_ms=int(raw[3]),
        )

    async def quit(self) -> None:
        client = self._resolve_client()
        quit_fn = getattr(client, "quit", None) or getattr(client, "aclose", None)
        if callable(quit_fn):
            res = quit_fn()
            if inspect.isawaitable(res):
                await res


@dataclass(frozen=True, slots=True)
class FallbackRateLimitBackendOptions:
    instance_count: int = 1
    cooldown_ms: int = 5000
    now_ms: Callable[[], int] | None = None


class FallbackRateLimitBackend:
    """Wraps primary Redis backend and memory fallback with cooldown + limit scaling."""

    def __init__(
        self,
        primary: RateLimitBackend | Any,
        fallback: RateLimitBackend | Any,
        options: FallbackRateLimitBackendOptions | Mapping[str, Any] | None = None,
        *,
        instance_count: int | None = None,
        cooldown_ms: int | None = None,
        now_ms: Callable[[], int] | None = None,
    ) -> None:
        self.primary = primary
        self.fallback = fallback
        self._logger = get_logger("FallbackRateLimitBackend")
        self._degraded_until_ms = 0

        resolved_ic = 1
        resolved_cd = 5000
        resolved_now: Callable[[], int] | None = None

        if isinstance(options, FallbackRateLimitBackendOptions):
            resolved_ic = options.instance_count
            resolved_cd = options.cooldown_ms
            resolved_now = options.now_ms
        elif isinstance(options, Mapping):
            raw_ic = (
                options["instance_count"]
                if "instance_count" in options
                else options.get("instanceCount", 1)
            )
            resolved_ic = int(raw_ic or 1)
            raw_cd = (
                options["cooldown_ms"]
                if "cooldown_ms" in options
                else options.get("cooldownMs", 5000)
            )
            resolved_cd = int(raw_cd if raw_cd is not None else 5000)
            resolved_now = options.get("now_ms") or options["nowMs"]

        if instance_count is not None:
            resolved_ic = instance_count
        if cooldown_ms is not None:
            resolved_cd = cooldown_ms
        if now_ms is not None:
            resolved_now = now_ms

        self.instance_count: int = max(1, math.floor(resolved_ic or 1))
        self.cooldown_ms: int = resolved_cd
        self._now_ms: Callable[[], int] = resolved_now or (
            lambda: int(time.time() * 1000)
        )

    @property
    def degraded_until_ms(self) -> int:
        return self._degraded_until_ms

    async def consume(
        self,
        context: ConsumeContext | Mapping[str, Any],
    ) -> ConsumeResult:
        ctx = _coerce_consume_context(context)
        now_timestamp_ms = self._now_ms()
        is_degraded = now_timestamp_ms < self._degraded_until_ms

        if not is_degraded:
            try:
                res = self.primary.consume(ctx)
                return await res if inspect.isawaitable(res) else res
            except Exception as primary_error:
                self._degraded_until_ms = now_timestamp_ms + self.cooldown_ms
                self._logger.warn(
                    f"Primary rate-limit backend failed, degrading to memory fallback "
                    f"for {self.cooldown_ms}ms: {primary_error}"
                )

        scaled_limit = max(1, math.floor(ctx.limit / self.instance_count))
        scaled_burst = (
            max(1, math.floor(ctx.burst / self.instance_count))
            if ctx.burst is not None
            else None
        )
        fallback_ctx = ConsumeContext(
            algorithm=ctx.algorithm,
            key=ctx.key,
            limit=scaled_limit,
            duration_ms=ctx.duration_ms,
            cost=ctx.cost,
            now_ms=ctx.now_ms,
            burst=scaled_burst,
            policy=ctx.policy,
        )
        res = self.fallback.consume(fallback_ctx)
        return await res if inspect.isawaitable(res) else res

    async def quit(self) -> None:
        for backend in (self.primary, self.fallback):
            quit_fn = getattr(backend, "quit", None)
            if callable(quit_fn):
                res = quit_fn()
                if inspect.isawaitable(res):
                    await res


MemoryRateLimitStore = MemoryRateLimitBackend
RedisRateLimitStore = RedisRateLimitBackend
FallbackRateLimitStore = FallbackRateLimitBackend

__all__ = [
    "EWMA_LUA",
    "FIXED_WINDOW_LUA",
    "LEAKY_BUCKET_LUA",
    "MEMORY_BACKEND_MAX_KEYS",
    "MEMORY_EVICT_RATIO",
    "MEMORY_KEY_TTL_MULTIPLIER",
    "REDIS_LUA_SCRIPTS",
    "SLIDING_WINDOW_COUNTER_LUA",
    "SLIDING_WINDOW_LOG_LUA",
    "TOKEN_BUCKET_LUA",
    "ConsumeContext",
    "ConsumeResult",
    "FallbackRateLimitBackend",
    "FallbackRateLimitBackendOptions",
    "FallbackRateLimitStore",
    "MemoryRateLimitBackend",
    "MemoryRateLimitStore",
    "RateLimitAlgorithm",
    "RateLimitBackend",
    "RateLimitIdentifier",
    "RateLimitStorage",
    "RedisLuaScriptName",
    "RedisRateLimitBackend",
    "RedisRateLimitStore",
    "normalize_algorithm",
    "normalize_identifier",
]
