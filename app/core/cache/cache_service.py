from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

from app.core.cache.ttl import apply_ttl_jitter
from app.core.cache.types import (
    CacheBackend,
    CacheEntry,
    CacheFreshness,
    CacheGetOrSetOptions,
    CacheGetResult,
    CacheLoaderLock,
    CacheModuleOptions,
    CacheTtl,
)

T = TypeVar("T")
logger = logging.getLogger("CacheService")

PEER_POLL_MS = 25
CACHE_LOADER_LOCK_PREFIX = "lock:cache:loader:"


class CacheService:
    def __init__(
        self,
        backend: CacheBackend,
        options: CacheModuleOptions | None = None,
        loader_lock: CacheLoaderLock | None = None,
    ) -> None:
        self._backend = backend
        self._options = options or CacheModuleOptions()
        self._loader_lock = loader_lock
        self._in_flight: dict[str, asyncio.Future[Any]] = {}
        self._key_prefix = self._options.key_prefix
        self._background_tasks: set[asyncio.Task[Any]] = set()

    async def get(self, key: str) -> CacheGetResult[Any] | None:
        return await self._safe_backend_get(key)

    async def set(self, key: str, value: Any, ttl: CacheTtl | None = None) -> None:
        resolved = self._resolve_ttl_for_write(ttl)
        now_ms = int(time.time() * 1000)
        entry: CacheEntry[Any] = CacheEntry(
            v=value,
            soft_expires_at=now_ms + resolved.soft_ttl_ms,
            hard_expires_at=now_ms + resolved.hard_ttl_ms,
        )
        await self._safe_backend_set(key, entry, resolved.hard_ttl_ms)

    async def get_or_set(
        self,
        key: str,
        loader: Callable[[], Awaitable[T]],
        options: CacheTtl | CacheGetOrSetOptions | None = None,
    ) -> T:
        resolved_opts = self._normalize_options(options)
        resolved_ttl = self._resolve_ttl(resolved_opts.ttl)

        existing = await self.get(key)
        if existing and existing.absent:
            if resolved_opts.cache_null:
                if existing.status == "fresh":
                    return None  # type: ignore[return-value]
                self._revalidate_in_background(key, loader, resolved_ttl, resolved_opts)
                return None  # type: ignore[return-value]
            return await self._load_and_set(key, loader, resolved_ttl, resolved_opts)

        if existing and existing.status == "fresh":
            return existing.value  # type: ignore[return-value]

        if existing and existing.status == "stale":
            self._revalidate_in_background(key, loader, resolved_ttl, resolved_opts)
            return existing.value  # type: ignore[return-value]

        return await self._load_and_set(key, loader, resolved_ttl, resolved_opts)

    async def delete(self, key: str) -> None:
        await self._safe_backend_delete(key)

    def _prefixed(self, key: str) -> str:
        return f"{self._key_prefix}{key}"

    def _loader_lock_key(self, user_key: str) -> str:
        return f"{CACHE_LOADER_LOCK_PREFIX}{user_key}"

    def _normalize_options(
        self, options: CacheTtl | CacheGetOrSetOptions | None
    ) -> CacheGetOrSetOptions:
        if options is None:
            return CacheGetOrSetOptions()
        if isinstance(options, CacheTtl):
            return CacheGetOrSetOptions(ttl=options)
        return options

    def _resolve_ttl_for_write(self, ttl: CacheTtl | None) -> CacheTtl:
        resolved = self._resolve_ttl(ttl)
        return apply_ttl_jitter(resolved, self._options.ttl_jitter_ratio)

    def _resolve_ttl(self, ttl: CacheTtl | None) -> CacheTtl:
        soft = ttl.soft_ttl_ms if ttl else self._options.default_soft_ttl_ms
        hard = ttl.hard_ttl_ms if ttl else self._options.default_hard_ttl_ms
        if soft is None or hard is None:
            raise ValueError(
                "Cache TTL requires soft_ttl_ms and hard_ttl_ms "
                "(pass them or set module defaults)"
            )
        if soft <= 0 or hard <= 0:
            raise ValueError("Cache TTL values must be > 0")
        if soft > hard:
            raise ValueError("soft_ttl_ms must be <= hard_ttl_ms")
        return CacheTtl(soft_ttl_ms=soft, hard_ttl_ms=hard)

    def _resolve_negative_ttl(self, options: CacheGetOrSetOptions) -> CacheTtl:
        ttl = options.negative_ttl
        if ttl is None:
            neg_soft = self._options.default_negative_soft_ttl_ms
            neg_hard = self._options.default_negative_hard_ttl_ms
            if neg_soft is not None and neg_hard is not None:
                ttl = CacheTtl(soft_ttl_ms=neg_soft, hard_ttl_ms=neg_hard)
        if ttl is None:
            raise ValueError(
                "cache_null requires negative_ttl or module "
                "defaultNegativeSoftTtlMs/defaultNegativeHardTtlMs"
            )
        return self._resolve_ttl(ttl)

    def _revalidate_in_background(
        self,
        key: str,
        loader: Callable[[], Awaitable[T]],
        ttl: CacheTtl,
        options: CacheGetOrSetOptions,
    ) -> None:
        async def _revalidate() -> None:
            try:
                await self._load_and_set(key, loader, ttl, options)
            except Exception as exc:
                logger.warning("Background revalidate failed for %s: %s", key, exc)

        task = asyncio.create_task(_revalidate())
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)

    async def _load_and_set(
        self,
        key: str,
        loader: Callable[[], Awaitable[T]],
        ttl: CacheTtl,
        options: CacheGetOrSetOptions,
    ) -> T:
        flight_key = self._prefixed(key)
        if flight_key in self._in_flight:
            res: T = await self._in_flight[flight_key]
            return res

        loop = asyncio.get_running_loop()
        future: asyncio.Future[Any] = loop.create_future()
        self._in_flight[flight_key] = future

        try:
            val = await self._run_coalesced_load(key, loader, ttl, options)
            future.set_result(val)
            return val
        except Exception as exc:
            future.set_exception(exc)
            raise
        finally:
            self._in_flight.pop(flight_key, None)

    async def _run_coalesced_load(
        self,
        key: str,
        loader: Callable[[], Awaitable[T]],
        ttl: CacheTtl,
        options: CacheGetOrSetOptions,
    ) -> T:
        if self._loader_lock is not None:
            lock_key = self._loader_lock_key(key)
            lock_ttl = self._options.loader_lock_ttl_ms
            state = {"threw": False}
            token = await self._safe_try_acquire(lock_key, lock_ttl, state)

            if state["threw"]:
                return await self._execute_loader(key, loader, ttl, options)

            if not token:
                peer = await self._wait_for_peer(key, options)
                if peer["hit"]:
                    return peer["value"]  # type: ignore[no-any-return]
                return await self._execute_loader(key, loader, ttl, options)

            try:
                again = await self.get(key)
                if again and again.absent and options.cache_null:
                    return None  # type: ignore[return-value]
                if again and not again.absent and again.status in ("fresh", "stale"):
                    return again.value  # type: ignore[return-value]
                return await self._execute_loader(key, loader, ttl, options)
            finally:
                await self._safe_release(lock_key, token)

        return await self._execute_loader(key, loader, ttl, options)

    async def _wait_for_peer(
        self, key: str, options: CacheGetOrSetOptions
    ) -> dict[str, Any]:
        deadline_ms = int(time.time() * 1000) + self._options.loader_wait_ms
        poll_sec = PEER_POLL_MS / 1000.0

        while int(time.time() * 1000) < deadline_ms:
            await asyncio.sleep(poll_sec)
            hit = await self.get(key)
            if hit and hit.absent and options.cache_null:
                return {"hit": True, "value": None}
            if hit and not hit.absent and hit.status in ("fresh", "stale"):
                return {"hit": True, "value": hit.value}

        logger.debug("Peer wait expired for cache key %s; fail-open load", key)
        return {"hit": False, "value": None}

    async def _execute_loader(
        self,
        key: str,
        loader: Callable[[], Awaitable[T]],
        ttl: CacheTtl,
        options: CacheGetOrSetOptions,
    ) -> T:
        value = await loader()
        if value is None and options.cache_null:
            neg_ttl = self._resolve_negative_ttl(options)
            await self._set_negative(key, neg_ttl)
        else:
            await self.set(key, value, ttl)
        return value

    async def _set_negative(self, key: str, ttl: CacheTtl) -> None:
        resolved = self._resolve_ttl_for_write(ttl)
        now_ms = int(time.time() * 1000)
        entry: CacheEntry[None] = CacheEntry(
            v=None,
            soft_expires_at=now_ms + resolved.soft_ttl_ms,
            hard_expires_at=now_ms + resolved.hard_ttl_ms,
            absent=True,
        )
        await self._safe_backend_set(key, entry, resolved.hard_ttl_ms)

    async def _safe_backend_get(self, key: str) -> CacheGetResult[Any] | None:
        try:
            entry = await self._backend.get(self._prefixed(key))
            if not entry:
                return None
            now_ms = int(time.time() * 1000)
            if now_ms >= entry.hard_expires_at:
                await self._safe_backend_delete(key)
                return None
            status: CacheFreshness = (
                "stale" if now_ms >= entry.soft_expires_at else "fresh"
            )
            if entry.absent:
                return CacheGetResult(value=None, status=status, absent=True)
            return CacheGetResult(value=entry.v, status=status, absent=False)
        except Exception as exc:
            logger.warning('Cache backend get failed for key "%s": %s', key, exc)
            return None

    async def _safe_backend_set(
        self, key: str, entry: CacheEntry[Any], ttl_ms: int
    ) -> None:
        try:
            await self._backend.set(self._prefixed(key), entry, ttl_ms)
        except Exception as exc:
            logger.warning('Cache backend set failed for key "%s": %s', key, exc)

    async def _safe_backend_delete(self, key: str) -> None:
        try:
            await self._backend.delete(self._prefixed(key))
        except Exception as exc:
            logger.warning('Cache backend delete failed for key "%s": %s', key, exc)

    async def _safe_try_acquire(
        self, lock_key: str, ttl_ms: int, state: dict[str, bool]
    ) -> str | None:
        if not self._loader_lock:
            return None
        try:
            return await self._loader_lock.try_acquire(lock_key, ttl_ms)
        except Exception as exc:
            state["threw"] = True
            logger.warning(
                'Cache loader lock try_acquire failed for key "%s": %s',
                lock_key,
                exc,
            )
            return None

    async def _safe_release(self, lock_key: str, token: str) -> None:
        if not self._loader_lock:
            return
        try:
            await self._loader_lock.release(lock_key, token)
        except Exception as exc:
            logger.warning(
                'Cache loader lock release failed for key "%s": %s',
                lock_key,
                exc,
            )
