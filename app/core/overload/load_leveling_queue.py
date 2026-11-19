from __future__ import annotations

import asyncio
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from app.common.config.parsers import (
    DEFAULT_INBOUND_LOAD_LEVEL_MAX_QUEUE,
    DEFAULT_INBOUND_LOAD_LEVEL_POLL_MS,
    ApiPriorityLevel,
)
from app.core.overload.errors import (
    LoadLevelQueueFullError,
    LoadLevelWaitTimeoutError,
)
from app.core.request_deadline.deadline_context import (
    RequestAbortSignal,
    RequestCancelledError,
)


def _priority_rank(priority: ApiPriorityLevel | str | int) -> int:
    if isinstance(priority, ApiPriorityLevel):
        return int(priority.value)
    if isinstance(priority, int) and not isinstance(priority, bool):
        return priority
    normalized = str(priority).strip().lower()
    if normalized == "critical":
        return int(ApiPriorityLevel.CRITICAL)
    if normalized == "high":
        return int(ApiPriorityLevel.HIGH)
    if normalized == "low":
        return int(ApiPriorityLevel.LOW)
    return int(ApiPriorityLevel.NORMAL)


@dataclass(slots=True)
class _Waiter:
    priority: int
    enqueued_at: float
    future: asyncio.Future[None]
    cleanup: Callable[[], None]


class InboundLoadLevelQueue:
    """Priority-ordered FIFO bounded wait queue (`CRITICAL > HIGH > NORMAL > LOW`)."""

    def __init__(
        self,
        max_queue: int = DEFAULT_INBOUND_LOAD_LEVEL_MAX_QUEUE,
        poll_interval_ms: int = DEFAULT_INBOUND_LOAD_LEVEL_POLL_MS,
    ) -> None:
        self.max_queue = max(1, int(max_queue))
        self.poll_interval_ms = max(1, int(poll_interval_ms))
        self._waiters: list[_Waiter] = []

    @property
    def maxQueue(self) -> int:
        return self.max_queue

    @property
    def size(self) -> int:
        return len(self._waiters)

    def _remove_waiter(self, waiter: _Waiter) -> None:
        try:
            self._waiters.remove(waiter)
        except ValueError:
            pass

    def _insert_sorted(self, waiter: _Waiter) -> None:
        insert_idx = len(self._waiters)
        for idx, existing in enumerate(self._waiters):
            if existing.priority > waiter.priority or (
                existing.priority == waiter.priority
                and existing.enqueued_at > waiter.enqueued_at
            ):
                insert_idx = idx
                break
        self._waiters.insert(insert_idx, waiter)

    async def enqueue(  # noqa: C901
        self,
        options: Mapping[str, Any] | None = None,
        *,
        priority: ApiPriorityLevel | str | int = ApiPriorityLevel.NORMAL,
        max_wait_ms: int | None = None,
        signal: RequestAbortSignal | Any | None = None,
    ) -> None:
        if isinstance(options, Mapping):
            raw_prio = options.get("priority", priority)
            raw_wait = (
                options.get("max_wait_ms")
                if "max_wait_ms" in options
                else options.get("maxWaitMs", max_wait_ms)
            )
            raw_sig = options.get("signal", signal)
        else:
            raw_prio = priority
            raw_wait = max_wait_ms
            raw_sig = signal

        effective_wait_ms = 2_000 if raw_wait is None else int(raw_wait)
        rank = _priority_rank(raw_prio)

        if len(self._waiters) >= self.max_queue:
            raise LoadLevelQueueFullError()

        loop = asyncio.get_running_loop()
        future: asyncio.Future[None] = loop.create_future()
        started_at = time.monotonic()

        poll_task: asyncio.Task[None] | None = None
        abort_listener: Callable[[], None] | None = None

        add_listener = (
            getattr(raw_sig, "add_event_listener", None)
            or getattr(raw_sig, "addEventListener", None)
            if raw_sig is not None
            else None
        )
        remove_listener = (
            getattr(raw_sig, "remove_event_listener", None)
            or getattr(raw_sig, "removeEventListener", None)
            if raw_sig is not None
            else None
        )

        def _cleanup() -> None:
            nonlocal poll_task, abort_listener
            if poll_task is not None and not poll_task.done():
                poll_task.cancel()
            poll_task = None
            if abort_listener is not None and callable(remove_listener):
                try:
                    remove_listener(abort_listener)
                except TypeError:
                    remove_listener("abort", abort_listener)
            abort_listener = None

        waiter = _Waiter(
            priority=rank,
            enqueued_at=started_at,
            future=future,
            cleanup=_cleanup,
        )
        self._insert_sorted(waiter)

        is_aborted = bool(getattr(raw_sig, "aborted", False)) if raw_sig else False
        if is_aborted or (isinstance(raw_sig, asyncio.Event) and raw_sig.is_set()):
            self._remove_waiter(waiter)
            _cleanup()
            raise RequestCancelledError()

        def _on_abort() -> None:
            if not future.done():
                self._remove_waiter(waiter)
                _cleanup()
                future.set_exception(RequestCancelledError())

        abort_listener = _on_abort
        if callable(add_listener):
            try:
                add_listener(_on_abort)
            except TypeError:
                add_listener("abort", _on_abort)

        poll_sec = max(0.001, self.poll_interval_ms / 1000.0)
        max_wait_sec = max(0.0, effective_wait_ms / 1000.0)

        async def _poll_timeout() -> None:
            try:
                while not future.done():
                    await asyncio.sleep(poll_sec)
                    if (time.monotonic() - started_at) >= max_wait_sec:
                        if not future.done():
                            self._remove_waiter(waiter)
                            _cleanup()
                            future.set_exception(LoadLevelWaitTimeoutError())
                        return
            except asyncio.CancelledError:
                return

        if effective_wait_ms <= 0:
            self._remove_waiter(waiter)
            _cleanup()
            raise LoadLevelWaitTimeoutError()

        poll_task = asyncio.create_task(_poll_timeout())

        try:
            await future
        finally:
            self._remove_waiter(waiter)
            _cleanup()

    def dequeue_next_waiter(self) -> Callable[[], None] | None:
        while self._waiters:
            next_waiter = self._waiters.pop(0)
            if not next_waiter.future.done():

                def _grant(w: _Waiter = next_waiter) -> None:
                    w.cleanup()
                    self._remove_waiter(w)
                    if not w.future.done():
                        w.future.set_result(None)

                return _grant
        return None

    def dequeueNextWaiter(self) -> Callable[[], None] | None:
        return self.dequeue_next_waiter()


LoadLevelingQueue = InboundLoadLevelQueue

__all__ = [
    "InboundLoadLevelQueue",
    "LoadLevelingQueue",
]
