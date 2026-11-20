from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from app.common.redis.redis_service import RedisService

GQL_BUDGET_KEY_PREFIX: str = "gql:budget:"
GQL_CONCURRENCY_KEY_PREFIX: str = "gql:concurrency:"
GQL_BUDGET_WINDOW_SEC: int = 60

_ACQUIRE_CONCURRENCY_LUA: str = """
local current = tonumber(redis.call('GET', KEYS[1]) or '0')
local limit = tonumber(ARGV[1])
if current >= limit then
  return 0
end
current = redis.call('INCR', KEYS[1])
if current == 1 then
  redis.call('EXPIRE', KEYS[1], tonumber(ARGV[2]))
end
return current
"""


class RedisBudgetClient(Protocol):
    async def get(self, key: str) -> str | None: ...

    async def set(
        self,
        key: str,
        value: str,
        *,
        ex: int | None = None,
        nx: bool = False,
    ) -> bool: ...

    async def decrby(self, key: str, amount: int) -> int: ...

    async def eval(
        self,
        script: str,
        numkeys: int,
        *keys_and_args: str | int,
    ) -> int: ...

    async def decr(self, key: str) -> int: ...


@dataclass(slots=True)
class MemoryRedisBudgetClient:
    """In-memory Redis stand-in for unit tests and SDL-only schema export."""

    store: dict[str, str] = field(default_factory=dict)
    ttls: dict[str, int] = field(default_factory=dict)

    async def get(self, key: str) -> str | None:
        return self.store.get(key)

    async def set(
        self,
        key: str,
        value: str,
        *,
        ex: int | None = None,
        nx: bool = False,
    ) -> bool:
        if nx and key in self.store:
            return False
        self.store[key] = value
        if ex is not None:
            self.ttls[key] = ex
        return True

    async def decrby(self, key: str, amount: int) -> int:
        current = int(self.store.get(key, "0"))
        new_val = current - amount
        self.store[key] = str(new_val)
        return new_val

    async def decr(self, key: str) -> int:
        return await self.decrby(key, 1)

    async def eval(
        self,
        script: str,
        numkeys: int,
        *keys_and_args: str | int,
    ) -> int:
        if numkeys != 1:
            raise ValueError("MemoryRedisBudgetClient supports one key scripts only")
        key = str(keys_and_args[0])
        limit = int(keys_and_args[1])
        current = int(self.store.get(key, "0"))
        if current >= limit:
            return 0
        current += 1
        self.store[key] = str(current)
        if current == 1 and len(keys_and_args) > 2:
            self.ttls[key] = int(keys_and_args[2])
        return current


def budget_key(subject: str) -> str:
    return f"{GQL_BUDGET_KEY_PREFIX}{subject}"


def concurrency_key(subject: str) -> str:
    return f"{GQL_CONCURRENCY_KEY_PREFIX}{subject}"


async def ensure_budget_initialized(
    client: RedisBudgetClient,
    *,
    subject: str,
    user_budget: int,
) -> int:
    key = budget_key(subject)
    raw = await client.get(key)
    if raw is None:
        await client.set(key, str(user_budget), ex=GQL_BUDGET_WINDOW_SEC, nx=True)
        raw = await client.get(key)
        if raw is None:
            raw = str(user_budget)
    return int(raw)


async def assert_budget_available(
    client: RedisBudgetClient,
    *,
    subject: str,
    user_budget: int,
    complexity: int,
) -> None:
    remaining = await ensure_budget_initialized(
        client, subject=subject, user_budget=user_budget
    )
    if complexity > remaining:
        from app.graphql.query_limits import GraphqlLimitError

        raise GraphqlLimitError(
            code="OUT_OF_BUDGET",
            message=(
                f"Query complexity {complexity} exceeds remaining budget {remaining}"
            ),
        )


async def consume_budget(
    client: RedisBudgetClient,
    *,
    subject: str,
    user_budget: int,
    complexity: int,
) -> None:
    await assert_budget_available(
        client,
        subject=subject,
        user_budget=user_budget,
        complexity=complexity,
    )
    await client.decrby(budget_key(subject), complexity)


async def try_acquire_concurrency(
    client: RedisBudgetClient,
    *,
    subject: str,
    max_concurrency: int,
    ttl_sec: int,
) -> bool:
    key = concurrency_key(subject)
    acquired = await client.eval(
        _ACQUIRE_CONCURRENCY_LUA,
        1,
        key,
        max_concurrency,
        ttl_sec,
    )
    return int(acquired) > 0


async def release_concurrency(client: RedisBudgetClient, *, subject: str) -> None:
    key = concurrency_key(subject)
    remaining = await client.decr(key)
    if remaining <= 0:
        await client.set(key, "0", ex=1)


@dataclass(slots=True)
class RedisServiceBudgetClient:
    """Maps :class:`RedisService` to :class:`RedisBudgetClient`."""

    redis_service: RedisService

    async def get(self, key: str) -> str | None:
        return await self.redis_service.get(key)

    async def set(
        self,
        key: str,
        value: str,
        *,
        ex: int | None = None,
        nx: bool = False,
    ) -> bool:
        result = await self.redis_service.set(key, value, ex=ex, nx=nx)
        return bool(result)

    async def decrby(self, key: str, amount: int) -> int:
        client = self.redis_service.get_client()
        return int(await client.decrby(key, amount))

    async def decr(self, key: str) -> int:
        client = self.redis_service.get_client()
        return int(await client.decr(key))

    async def eval(
        self,
        script: str,
        numkeys: int,
        *keys_and_args: str | int,
    ) -> int:
        result = await self.redis_service.eval(script, numkeys, *keys_and_args)
        return int(result)


__all__ = [
    "GQL_BUDGET_WINDOW_SEC",
    "MemoryRedisBudgetClient",
    "RedisBudgetClient",
    "RedisServiceBudgetClient",
    "assert_budget_available",
    "budget_key",
    "concurrency_key",
    "consume_budget",
    "release_concurrency",
    "try_acquire_concurrency",
]
