from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Protocol

CacheFreshness = Literal["fresh", "stale"]


@dataclass(slots=True)
class CacheTtl:
    soft_ttl_ms: int
    hard_ttl_ms: int

    @property
    def softTtlMs(self) -> int:
        return self.soft_ttl_ms

    @property
    def hardTtlMs(self) -> int:
        return self.hard_ttl_ms


@dataclass(slots=True)
class CacheEntry[T]:
    v: T
    soft_expires_at: int
    hard_expires_at: int
    absent: bool = False

    @property
    def softExpiresAt(self) -> int:
        return self.soft_expires_at

    @property
    def hardExpiresAt(self) -> int:
        return self.hard_expires_at


@dataclass(slots=True)
class CacheGetResult[T]:
    value: T | None
    status: CacheFreshness
    absent: bool = False


@dataclass(slots=True)
class CacheGetOrSetOptions:
    ttl: CacheTtl | None = None
    cache_null: bool = False
    negative_ttl: CacheTtl | None = None

    @property
    def cacheNull(self) -> bool:
        return self.cache_null

    @property
    def negativeTtl(self) -> CacheTtl | None:
        return self.negative_ttl


@dataclass(slots=True)
class CacheModuleOptions:
    storage: Literal["memory", "redis"] = "memory"
    key_prefix: str = "cache:"
    default_soft_ttl_ms: int = 60_000
    default_hard_ttl_ms: int = 300_000
    ttl_jitter_ratio: float = 0.0
    loader_lock_ttl_ms: int = 30_000
    loader_wait_ms: int = 5_000
    default_negative_soft_ttl_ms: int | None = None
    default_negative_hard_ttl_ms: int | None = None


class CacheBackend(Protocol):
    async def get(self, key: str) -> CacheEntry[Any] | None: ...

    async def set(self, key: str, entry: CacheEntry[Any], hard_ttl_ms: int) -> None: ...

    async def delete(self, key: str) -> None: ...

    async def quit(self) -> None: ...


class CacheLoaderLock(Protocol):
    async def try_acquire(self, lock_key: str, ttl_ms: int) -> str | None: ...

    async def release(self, lock_key: str, token: str) -> None: ...
