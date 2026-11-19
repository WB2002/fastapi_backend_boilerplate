from __future__ import annotations

import asyncio
import inspect
import os
from collections.abc import Mapping
from typing import Any, Final

import redis.asyncio as aioredis

from app.common.config.parsers import parse_positive_int

QUIT_TIMEOUT_MS: Final[int] = 1_000
DEFAULT_REDIS_HOST: Final[str] = "localhost"
DEFAULT_REDIS_PORT: Final[int] = 6379
DEFAULT_CONNECT_TIMEOUT_MS: Final[int] = 2_000


def default_retry_strategy(times: int) -> int:
    """Mirror NestJS ioredis retryStrategy: `Math.min(times * 200, 2_000)` ms."""
    return min(max(0, times) * 200, 2_000)


class RedisService:
    """Async Redis client lifecycle manager matching NestJS RedisService."""

    def __init__(
        self,
        env: Mapping[str, str | None] | None = None,
        *,
        host: str | None = None,
        port: int | None = None,
        connect_timeout_ms: int = DEFAULT_CONNECT_TIMEOUT_MS,
        quit_timeout_ms: int = QUIT_TIMEOUT_MS,
        client_factory: Any = None,
        auto_init: bool = True,
    ) -> None:
        target = os.environ if env is None else env
        raw_host = host if host is not None else target.get("REDIS_HOST")
        self.host: str = (
            raw_host.strip()
            if raw_host is not None and raw_host.strip()
            else DEFAULT_REDIS_HOST
        )
        self.port: int = (
            port
            if port is not None
            else parse_positive_int(target.get("REDIS_PORT"), DEFAULT_REDIS_PORT)
        )
        self.connect_timeout_ms: int = connect_timeout_ms
        self.quit_timeout_ms: int = quit_timeout_ms
        self._client_factory = client_factory or aioredis.Redis
        self._client: Any = None
        self._duplicates: list[Any] = []
        self._is_closed = False
        if auto_init:
            self.on_module_init()

    @property
    def duplicates(self) -> tuple[Any, ...]:
        return tuple(self._duplicates)

    @property
    def is_connected(self) -> bool:
        return self._client is not None and not self._is_closed

    def _create_client(self, **overrides: Any) -> Any:
        timeout_sec = max(0.001, self.connect_timeout_ms / 1000.0)
        params: dict[str, Any] = {
            "host": self.host,
            "port": self.port,
            "socket_connect_timeout": timeout_sec,
            "socket_timeout": timeout_sec,
            "decode_responses": True,
        }
        params.update(overrides)
        return self._client_factory(**params)

    def on_module_init(self) -> None:
        """Initialize the primary non-blocking Redis client."""
        if self._client is None:
            self._client = self._create_client()

    def onModuleInit(self) -> None:
        self.on_module_init()

    def get_client(self) -> Any:
        """Return the active Redis client or raise if not initialized."""
        if self._client is None:
            raise RuntimeError("Redis client is not initialized")
        return self._client

    def getClient(self) -> Any:
        return self.get_client()

    def duplicate(self, **overrides: Any) -> Any:
        """Create and track a duplicated Redis client (for Pub/Sub or Socket.IO)."""
        primary = self.get_client()
        dup_fn = getattr(primary, "duplicate", None)
        if callable(dup_fn):
            dup = dup_fn(**overrides) if overrides else dup_fn()
        else:
            dup = self._create_client(**overrides)
        self._duplicates.append(dup)
        return dup

    async def get(self, key: str) -> str | None:
        client = self.get_client()
        res = await client.get(key)
        if isinstance(res, (bytes, bytearray)):
            return res.decode("utf-8")
        return str(res) if res is not None else None

    async def set(
        self,
        key: str,
        value: str | bytes | int | float,
        *,
        ex: int | None = None,
        px: int | None = None,
        nx: bool = False,
        xx: bool = False,
    ) -> Any:
        client = self.get_client()
        return await client.set(key, value, ex=ex, px=px, nx=nx, xx=xx)

    async def delete(self, *keys: str) -> int:
        if not keys:
            return 0
        client = self.get_client()
        res = await client.delete(*keys)
        return int(res)

    async def eval(
        self,
        script: str,
        numkeys: int,
        *keys_and_args: Any,
    ) -> Any:
        client = self.get_client()
        return await client.eval(script, numkeys, *keys_and_args)

    async def incrby(self, key: str, amount: int = 1) -> int:
        client = self.get_client()
        res = await client.incrby(key, amount)
        return int(res)

    async def pexpire(self, key: str, time_ms: int) -> bool:
        client = self.get_client()
        res = await client.pexpire(key, time_ms)
        return bool(res)

    async def ping(self) -> bool:
        client = self.get_client()
        res = await client.ping()
        return bool(res)

    async def _close_single_client(self, client: Any) -> None:
        timeout_sec = max(0.001, self.quit_timeout_ms / 1000.0)
        try:
            quit_fn = getattr(client, "quit", None)
            if callable(quit_fn):
                res = quit_fn()
                if inspect.isawaitable(res):
                    await asyncio.wait_for(res, timeout=timeout_sec)
            else:
                aclose_fn = getattr(client, "aclose", None)
                if callable(aclose_fn):
                    res = aclose_fn()
                    if inspect.isawaitable(res):
                        await asyncio.wait_for(res, timeout=timeout_sec)
        except Exception:
            await self._force_disconnect_client(client)
        else:
            aclose_fn = getattr(client, "aclose", None)
            if callable(aclose_fn):
                try:
                    res = aclose_fn()
                    if inspect.isawaitable(res):
                        await asyncio.wait_for(res, timeout=timeout_sec)
                except Exception:  # noqa: S110
                    pass

    async def _force_disconnect_client(self, client: Any) -> None:
        disconnect_fn = getattr(client, "disconnect", None)
        if callable(disconnect_fn):
            try:
                res = disconnect_fn(False)
            except TypeError:
                res = disconnect_fn()
            try:
                if inspect.isawaitable(res):
                    await res
            except Exception:  # noqa: S110
                pass
            return

        pool = getattr(client, "connection_pool", None)
        pool_disconnect = (
            getattr(pool, "disconnect", None) if pool is not None else None
        )
        if callable(pool_disconnect):
            try:
                res = pool_disconnect()
                if inspect.isawaitable(res):
                    await res
            except Exception:  # noqa: S110
                pass
            return

        aclose_fn = getattr(client, "aclose", None)
        if callable(aclose_fn):
            try:
                res = aclose_fn(close_connection_pool=True)
            except TypeError:
                res = aclose_fn()
            try:
                if inspect.isawaitable(res):
                    await res
            except Exception:  # noqa: S110
                pass

    async def on_module_destroy(self) -> None:
        """Gracefully QUIT within 1000ms, falling back to immediate disconnect."""
        duplicates = list(self._duplicates)
        self._duplicates.clear()
        for dup in duplicates:
            await self._close_single_client(dup)

        client = self._client
        if client is None:
            return
        await self._close_single_client(client)

    async def onModuleDestroy(self) -> None:
        await self.on_module_destroy()

    async def close(self) -> None:
        """Alias for `on_module_destroy()`."""
        await self.on_module_destroy()


__all__ = [
    "DEFAULT_CONNECT_TIMEOUT_MS",
    "DEFAULT_REDIS_HOST",
    "DEFAULT_REDIS_PORT",
    "QUIT_TIMEOUT_MS",
    "RedisService",
    "default_retry_strategy",
]
