from __future__ import annotations

import json
import logging
import math
import time
from typing import Any

from app.core.cache.types import CacheEntry

logger = logging.getLogger("CacheBackend")


class MemoryCacheBackend:
    def __init__(self) -> None:
        self._store: dict[str, CacheEntry[Any]] = {}

    async def get(self, key: str) -> CacheEntry[Any] | None:
        entry = self._store.get(key)
        if not entry:
            return None
        now_ms = int(time.time() * 1000)
        if now_ms >= entry.hard_expires_at:
            self._store.pop(key, None)
            return None
        return entry

    async def set(self, key: str, entry: CacheEntry[Any], _hard_ttl_ms: int) -> None:
        self._store[key] = entry

    async def delete(self, key: str) -> None:
        self._store.pop(key, None)

    async def quit(self) -> None:
        self._store.clear()

    def size(self) -> int:
        return len(self._store)


class RedisCacheBackend:
    def __init__(self, redis: Any) -> None:
        self._redis = redis

    def get_redis(self) -> Any:
        return self._redis

    async def get(self, key: str) -> CacheEntry[Any] | None:
        raw = await self._redis.get(key)
        if raw is None:
            return None
        try:
            if isinstance(raw, bytes):
                raw = raw.decode("utf-8")
            data = json.loads(raw)
            entry = CacheEntry(
                v=data["v"],
                soft_expires_at=data["softExpiresAt"],
                hard_expires_at=data["hardExpiresAt"],
                absent=data.get("absent", False),
            )
        except Exception as exc:
            logger.warning("Invalid JSON for cache key %s: %s", key, exc)
            await self._redis.delete(key)
            return None

        now_ms = int(time.time() * 1000)
        if now_ms >= entry.hard_expires_at:
            await self._redis.delete(key)
            return None
        return entry

    async def set(self, key: str, entry: CacheEntry[Any], hard_ttl_ms: int) -> None:
        payload = json.dumps(
            {
                "v": entry.v,
                "softExpiresAt": entry.soft_expires_at,
                "hardExpiresAt": entry.hard_expires_at,
                "absent": entry.absent,
            }
        )
        ttl = max(1, math.ceil(hard_ttl_ms))
        await self._redis.set(key, payload, px=ttl)

    async def delete(self, key: str) -> None:
        await self._redis.delete(key)

    async def quit(self) -> None:
        if hasattr(self._redis, "aclose"):
            await self._redis.aclose()
        elif hasattr(self._redis, "close"):
            res = self._redis.close()
            if hasattr(res, "__await__"):
                await res
