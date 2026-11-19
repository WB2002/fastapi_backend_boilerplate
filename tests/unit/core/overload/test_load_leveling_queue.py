from __future__ import annotations

import asyncio

import pytest

from app.common.config.parsers import ApiPriorityLevel
from app.core.overload.errors import (
    LoadLevelQueueFullError,
    LoadLevelWaitTimeoutError,
)
from app.core.overload.load_leveling_queue import InboundLoadLevelQueue


async def test_grants_in_priority_then_fifo_order() -> None:
    queue = InboundLoadLevelQueue(max_queue=10, poll_interval_ms=5)
    order: list[str] = []

    async def waiter(name: str, prio: ApiPriorityLevel) -> None:
        await queue.enqueue(priority=prio, max_wait_ms=2000)
        order.append(name)

    tasks = [
        asyncio.create_task(waiter("low", ApiPriorityLevel.LOW)),
        asyncio.create_task(waiter("normal-1", ApiPriorityLevel.NORMAL)),
        asyncio.create_task(waiter("critical", ApiPriorityLevel.CRITICAL)),
        asyncio.create_task(waiter("normal-2", ApiPriorityLevel.NORMAL)),
    ]
    for _ in range(3):
        await asyncio.sleep(0.01)
    assert queue.size == 4
    while queue.size:
        grant = queue.dequeue_next_waiter()
        assert grant is not None
        grant()
        await asyncio.sleep(0)
    await asyncio.gather(*tasks)
    assert order == ["critical", "normal-1", "normal-2", "low"]


async def test_queue_full_raises() -> None:
    queue = InboundLoadLevelQueue(max_queue=1, poll_interval_ms=5)
    first = asyncio.create_task(queue.enqueue(max_wait_ms=500))
    await asyncio.sleep(0.01)
    with pytest.raises(LoadLevelQueueFullError):
        await queue.enqueue(max_wait_ms=500)
    first.cancel()
    await asyncio.gather(first, return_exceptions=True)


async def test_wait_timeout_raises_and_cleans_up() -> None:
    queue = InboundLoadLevelQueue(max_queue=2, poll_interval_ms=5)
    with pytest.raises(LoadLevelWaitTimeoutError):
        await queue.enqueue(max_wait_ms=30)
    assert queue.size == 0


def test_dequeue_empty_returns_none() -> None:
    assert InboundLoadLevelQueue().dequeue_next_waiter() is None
