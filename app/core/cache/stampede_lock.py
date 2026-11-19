from __future__ import annotations

import math
import uuid
from typing import Any

RELEASE_SCRIPT = """
if redis.call("get", KEYS[1]) == ARGV[1] then
  return redis.call("del", KEYS[1])
else
  return 0
end
"""


async def acquire_redis_loader_lock(
    redis: Any,
    lock_key: str,
    token: str,
    ttl_ms: int,
) -> bool:
    """Acquire single-flight loader lock using Redis SET NX PX."""
    ttl = max(1, math.ceil(ttl_ms))
    result = await redis.set(lock_key, token, px=ttl, nx=True)
    return result is True or result == "OK"


async def release_redis_loader_lock(
    redis: Any,
    lock_key: str,
    token: str,
) -> None:
    """Release loader lock if and only if the current token matches."""
    await redis.eval(RELEASE_SCRIPT, 1, lock_key, token)


class RedisCacheLoaderLock:
    def __init__(self, redis: Any) -> None:
        self._redis = redis

    async def try_acquire(self, lock_key: str, ttl_ms: int) -> str | None:
        token = str(uuid.uuid4())
        ok = await acquire_redis_loader_lock(self._redis, lock_key, token, ttl_ms)
        return token if ok else None

    async def release(self, lock_key: str, token: str) -> None:
        await release_redis_loader_lock(self._redis, lock_key, token)
