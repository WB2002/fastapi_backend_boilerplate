from __future__ import annotations

import asyncio

import pytest

from app.auth.password.password_hash_bulkhead import PasswordHashBulkhead
from app.common.filters.all_exceptions_filter import OverloadRejectedError


async def test_hash_and_verify_roundtrip_uses_argon2id() -> None:
    bulkhead = PasswordHashBulkhead(max_concurrent=2, max_queue=32, max_wait_ms=2000)
    digest = await bulkhead.hash("correct horse battery")
    assert digest.startswith("$argon2id$")
    assert await bulkhead.verify(digest, "correct horse battery") is True
    assert await bulkhead.verify(digest, "wrong password") is False


async def test_defaults_match_reference() -> None:
    bulkhead = PasswordHashBulkhead()
    assert bulkhead.max_concurrent == 2
    assert bulkhead.max_queue == 32
    assert bulkhead.max_wait_ms == 2000


async def test_queue_full_rejects_with_overload() -> None:
    bulkhead = PasswordHashBulkhead(max_concurrent=1, max_queue=1, max_wait_ms=2000)
    gate = asyncio.Event()

    async def blocker() -> str:
        await gate.wait()
        return "done"

    first = asyncio.create_task(bulkhead.run(blocker))
    await asyncio.sleep(0)
    second = asyncio.create_task(bulkhead.run(blocker))
    await asyncio.sleep(0.01)
    assert bulkhead.queued_count == 1
    with pytest.raises(OverloadRejectedError):
        await bulkhead.run(blocker)
    gate.set()
    assert await first == "done"
    assert await second == "done"
    assert bulkhead.active_count == 0


async def test_wait_timeout_rejects_with_overload() -> None:
    bulkhead = PasswordHashBulkhead(max_concurrent=1, max_queue=4, max_wait_ms=30)
    gate = asyncio.Event()

    async def blocker() -> None:
        await gate.wait()

    holder = asyncio.create_task(bulkhead.run(blocker))
    await asyncio.sleep(0)
    with pytest.raises(OverloadRejectedError):
        await bulkhead.run(blocker)
    gate.set()
    await holder


async def test_permit_released_when_task_raises() -> None:
    bulkhead = PasswordHashBulkhead(max_concurrent=1, max_queue=1, max_wait_ms=50)

    async def boom() -> None:
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError):
        await bulkhead.run(boom)
    assert bulkhead.active_count == 0
    assert await bulkhead.run(lambda: 7) == 7
