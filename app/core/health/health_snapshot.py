from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable


class HealthSnapshot[T]:
    """Single-flight in-flight deduplication and TTL caching for health checks."""

    def __init__(
        self,
        ttl_ms: int,
        now_ms: Callable[[], int] | None = None,
    ) -> None:
        self._ttl_ms = ttl_ms
        self._now_ms: Callable[[], int] = (
            now_ms if now_ms is not None else lambda: int(time.time() * 1000)
        )
        self._cached_value: T | None = None
        self._has_cached_value: bool = False
        self._cached_at: int | None = None
        self._in_flight: asyncio.Future[T] | None = None

    @property
    def ttl_ms(self) -> int:
        return self._ttl_ms

    async def get(self, load: Callable[[], Awaitable[T]]) -> T:
        now = self._now_ms()
        if (
            self._has_cached_value
            and self._cached_at is not None
            and now - self._cached_at < self._ttl_ms
        ):
            return self._cached_value  # type: ignore[return-value]

        if self._in_flight is not None:
            return await asyncio.shield(self._in_flight)

        loop = asyncio.get_running_loop()
        future: asyncio.Future[T] = loop.create_future()
        self._in_flight = future
        try:
            loaded_value = await load()
            self._cached_value = loaded_value
            self._has_cached_value = True
            self._cached_at = self._now_ms()
            if not future.done():
                future.set_result(loaded_value)
            return loaded_value
        except BaseException as exc:
            if not future.done():
                future.set_exception(exc)
                future.exception()  # Mark retrieved so asyncio does not warn
            raise
        finally:
            self._in_flight = None


__all__ = ["HealthSnapshot"]
