from __future__ import annotations

import asyncio
from typing import Any

from fastapi import HTTPException, status

from app.conformance.dto import ConformanceCacheRequestDto
from app.core.cache import CacheGetOrSetOptions, CacheService, CacheTtl


class ConformanceCacheService:
    def __init__(self, cache_service: CacheService) -> None:
        self._cache_service = cache_service
        self._loader_call_counts: dict[str, int] = {}

    async def get_or_set(self, request: ConformanceCacheRequestDto) -> dict[str, Any]:
        cache_key = f"conformance:{request.key}"
        count_before = self._loader_call_counts.get(request.key, 0)

        async def loader() -> str | None:
            next_count = self._loader_call_counts.get(request.key, 0) + 1
            self._loader_call_counts[request.key] = next_count

            if request.loaderDelayMs and request.loaderDelayMs > 0:
                await asyncio.sleep(request.loaderDelayMs / 1000.0)

            if request.shouldFailLoader:
                raise HTTPException(
                    status_code=status.HTTP_502_BAD_GATEWAY,
                    detail="Conformance loader failed",
                )

            return request.value

        negative_ttl: CacheTtl | None = None
        if (
            request.negativeSoftTtlMs is not None
            and request.negativeHardTtlMs is not None
        ):
            negative_ttl = CacheTtl(
                soft_ttl_ms=request.negativeSoftTtlMs,
                hard_ttl_ms=request.negativeHardTtlMs,
            )

        cache_options = CacheGetOrSetOptions(
            ttl=CacheTtl(
                soft_ttl_ms=request.softTtlMs,
                hard_ttl_ms=request.hardTtlMs,
            ),
            cache_null=(
                request.shouldCacheNull
                if request.shouldCacheNull is not None
                else False
            ),
            negative_ttl=negative_ttl,
        )

        try:
            cached_val = await self._cache_service.get_or_set(
                cache_key,
                loader,
                options=cache_options,
            )
        except HTTPException:
            raise
        except Exception:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="Conformance loader failed",
            ) from None

        count_after = self._loader_call_counts.get(request.key, 0)

        return {
            "cachedValue": cached_val,
            "loaderCallCount": count_after,
            "isFromLoader": count_after > count_before,
        }

    async def reset(self, key: str) -> None:
        cache_key = f"conformance:{key}"
        await self._cache_service.delete(cache_key)
        self._loader_call_counts.pop(key, None)
