from __future__ import annotations

import asyncio
from typing import Any

import pytest

from app.common.redis import (
    DEFAULT_CONNECT_TIMEOUT_MS,
    DEFAULT_REDIS_HOST,
    DEFAULT_REDIS_PORT,
    QUIT_TIMEOUT_MS,
    RedisService,
    default_retry_strategy,
)


class _FakeRedisClient:
    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs
        self.store: dict[str, Any] = {}
        self.quit_called = 0
        self.disconnect_called = 0
        self.aclose_called = 0
        self.quit_delay_sec = 0.0
        self.quit_error: Exception | None = None
        self.last_eval: tuple[str, int, tuple[Any, ...]] | None = None
        self.ttls: dict[str, int] = {}

    async def get(self, key: str) -> Any:
        return self.store.get(key)

    async def set(
        self,
        key: str,
        value: Any,
        *,
        ex: int | None = None,
        px: int | None = None,
        nx: bool = False,
        xx: bool = False,
    ) -> bool:
        if nx and key in self.store:
            return False
        if xx and key not in self.store:
            return False
        self.store[key] = value
        if px is not None:
            self.ttls[key] = px
        elif ex is not None:
            self.ttls[key] = ex * 1000
        return True

    async def delete(self, *keys: str) -> int:
        deleted = 0
        for k in keys:
            if k in self.store:
                del self.store[k]
                deleted += 1
        return deleted

    async def eval(
        self,
        script: str,
        numkeys: int,
        *keys_and_args: Any,
    ) -> Any:
        self.last_eval = (script, numkeys, keys_and_args)
        return 1

    async def incrby(self, key: str, amount: int = 1) -> int:
        current = int(self.store.get(key, 0))
        new_val = current + amount
        self.store[key] = new_val
        return new_val

    async def pexpire(self, key: str, time_ms: int) -> bool:
        if key not in self.store:
            return False
        self.ttls[key] = time_ms
        return True

    async def ping(self) -> bool:
        return True

    async def quit(self) -> None:
        self.quit_called += 1
        if self.quit_delay_sec > 0:
            await asyncio.sleep(self.quit_delay_sec)
        if self.quit_error is not None:
            raise self.quit_error

    def disconnect(self, _reconnect: bool = False) -> None:
        self.disconnect_called += 1

    async def aclose(self) -> None:
        self.aclose_called += 1


def test_redis_service_defaults_and_retry_strategy() -> None:
    assert QUIT_TIMEOUT_MS == 1000
    assert DEFAULT_REDIS_HOST == "localhost"
    assert DEFAULT_REDIS_PORT == 6379
    assert DEFAULT_CONNECT_TIMEOUT_MS == 2000
    assert default_retry_strategy(1) == 200
    assert default_retry_strategy(5) == 1000
    assert default_retry_strategy(20) == 2000


def test_redis_service_lazy_init_and_get_client() -> None:
    svc = RedisService(
        env={"REDIS_HOST": "redis.internal", "REDIS_PORT": "6380"},
        client_factory=_FakeRedisClient,
        auto_init=False,
    )
    with pytest.raises(RuntimeError, match="Redis client is not initialized"):
        svc.get_client()

    svc.on_module_init()
    client = svc.get_client()
    assert isinstance(client, _FakeRedisClient)
    assert client.kwargs["host"] == "redis.internal"
    assert client.kwargs["port"] == 6380
    assert client.kwargs["socket_connect_timeout"] == 2.0
    assert svc.getClient() is client


async def test_redis_service_convenience_operations() -> None:
    svc = RedisService(
        env={"REDIS_HOST": "localhost", "REDIS_PORT": "6379"},
        client_factory=_FakeRedisClient,
    )
    assert await svc.ping() is True
    assert await svc.get("missing") is None

    await svc.set("k1", "v1", px=5000)
    assert await svc.get("k1") == "v1"

    val = await svc.incrby("counter", 3)
    assert val == 3
    assert await svc.pexpire("counter", 1500) is True

    eval_res = await svc.eval("return 1", 1, "k1", "arg1")
    assert eval_res == 1

    assert await svc.delete("k1", "counter") == 2
    assert await svc.delete() == 0


async def test_redis_service_duplicate_and_clean_quit() -> None:
    svc = RedisService(
        env={"REDIS_HOST": "localhost", "REDIS_PORT": "6379"},
        client_factory=_FakeRedisClient,
    )
    primary: _FakeRedisClient = svc.get_client()
    dup1: _FakeRedisClient = svc.duplicate(decode_responses=False)
    dup2: _FakeRedisClient = svc.duplicate()

    assert dup1 is not primary
    assert dup1.kwargs["decode_responses"] is False
    assert len(svc.duplicates) == 2

    await svc.on_module_destroy()
    assert primary.quit_called == 1
    assert dup1.quit_called == 1
    assert dup2.quit_called == 1
    assert primary.disconnect_called == 0
    assert len(svc.duplicates) == 0


async def test_redis_service_quit_timeout_falls_back_to_disconnect() -> None:
    svc = RedisService(
        env={"REDIS_HOST": "localhost", "REDIS_PORT": "6379"},
        quit_timeout_ms=50,
        client_factory=_FakeRedisClient,
    )
    primary: _FakeRedisClient = svc.get_client()
    primary.quit_delay_sec = 0.5  # Exceeds 50ms quit timeout

    await svc.on_module_destroy()
    assert primary.quit_called == 1
    assert primary.disconnect_called == 1


async def test_redis_service_quit_error_falls_back_to_disconnect() -> None:
    svc = RedisService(
        env={"REDIS_HOST": "localhost", "REDIS_PORT": "6379"},
        client_factory=_FakeRedisClient,
    )
    primary: _FakeRedisClient = svc.get_client()
    primary.quit_error = ConnectionError("Redis unreachable")

    await svc.close()
    assert primary.quit_called == 1
    assert primary.disconnect_called == 1
