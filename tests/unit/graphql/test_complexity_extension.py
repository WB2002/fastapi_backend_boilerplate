from __future__ import annotations

import pytest
from graphql import parse

from app.graphql.complexity_extension import GraphqlProtectionExtension
from app.graphql.query_limits import GraphqlLimitError
from app.graphql.redis_budget import MemoryRedisBudgetClient


@pytest.mark.asyncio
async def test_budget_rejects_when_insufficient() -> None:
    redis = MemoryRedisBudgetClient()
    redis.store["gql:budget:guest:abc"] = "2"
    ext = GraphqlProtectionExtension(
        redis_client=redis,
        max_complexity=50,
        user_budget=1000,
        max_concurrency=3,
        concurrency_ttl_sec=300,
    )
    ext._subject = "guest:abc"
    doc = parse("query { categories { id name description } }")
    with pytest.raises(GraphqlLimitError) as exc:
        await ext.validate_before_execute(doc)
    assert exc.value.code == "OUT_OF_BUDGET"


@pytest.mark.asyncio
async def test_concurrency_respects_cap() -> None:
    redis = MemoryRedisBudgetClient()
    subject = "guest:xyz"
    for _ in range(3):
        acquired = await ext_acquire(redis, subject)
        assert acquired is True
    assert await ext_acquire(redis, subject) is False


async def ext_acquire(redis: MemoryRedisBudgetClient, subject: str) -> bool:
    from app.graphql.redis_budget import try_acquire_concurrency

    return await try_acquire_concurrency(
        redis,
        subject=subject,
        max_concurrency=3,
        ttl_sec=300,
    )
