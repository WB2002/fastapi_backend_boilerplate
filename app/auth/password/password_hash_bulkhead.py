from __future__ import annotations

import asyncio
import inspect
import math
from collections import deque
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any, TypeVar, overload

from argon2 import PasswordHasher, Type
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

from app.auth.constants import (
    ARGON2_MEMORY_COST,
    ARGON2_PARALLELISM,
    ARGON2_TIME_COST,
    DEFAULT_AUTH_PASSWORD_HASH_MAX_CONCURRENT,
    DEFAULT_AUTH_PASSWORD_HASH_MAX_QUEUE,
    DEFAULT_AUTH_PASSWORD_HASH_MAX_WAIT_MS,
)
from app.common.config.parsers import parse_js_number
from app.common.filters.all_exceptions_filter import OverloadRejectedError
from app.core.request_deadline.deadline_context import (
    RequestAbortSignal,
    RequestCancelledError,
    get_request_signal,
    remaining_ms,
    throw_if_aborted,
)

T = TypeVar("T")


def _parse_non_negative_int(value: object, default_value: int) -> int:
    if value is None or value == "" or isinstance(value, bool):
        return default_value
    parsed = (
        float(value) if isinstance(value, (int, float)) else parse_js_number(str(value))
    )
    if not math.isfinite(parsed) or parsed < 0:
        return default_value
    return math.floor(parsed)


def _parse_positive_int(value: object, default_value: int) -> int:
    parsed = _parse_non_negative_int(value, default_value)
    return max(1, parsed)


def _read_config_val(config: Any, key: str) -> Any:
    if config is None:
        return None
    if isinstance(config, Mapping):
        return config.get(key)
    get_fn = getattr(config, "get", None)
    if callable(get_fn):
        return get_fn(key)
    return None


@dataclass(frozen=True, slots=True)
class PasswordHashBulkheadOptions:
    max_concurrent: int = DEFAULT_AUTH_PASSWORD_HASH_MAX_CONCURRENT
    max_queue: int = DEFAULT_AUTH_PASSWORD_HASH_MAX_QUEUE
    max_wait_ms: int = DEFAULT_AUTH_PASSWORD_HASH_MAX_WAIT_MS

    @property
    def maxConcurrent(self) -> int:
        return self.max_concurrent

    @property
    def maxQueue(self) -> int:
        return self.max_queue

    @property
    def maxWaitMs(self) -> int:
        return self.max_wait_ms


class _FIFOConcurrencyLimiter:
    """FIFO permit queue with immediate abort cleanup and permit handoff."""

    def __init__(self, max_concurrent: int) -> None:
        self.max_concurrent = max(1, max_concurrent)
        self._active = 0
        self._queue: deque[asyncio.Future[None]] = deque()

    @property
    def active_count(self) -> int:
        return self._active

    @property
    def queued_count(self) -> int:
        return sum(1 for fut in self._queue if not fut.done())

    def release(self) -> None:
        while self._queue:
            next_fut = self._queue.popleft()
            if not next_fut.done():
                next_fut.set_result(None)
                return
        if self._active > 0:
            self._active -= 1

    async def acquire(  # noqa: C901
        self,
        *,
        wait_timeout_ms: int,
        inbound_signal: RequestAbortSignal | Any | None = None,
    ) -> Callable[[], None]:
        if self._active < self.max_concurrent:
            self._active += 1
            released = False

            def _release_once() -> None:
                nonlocal released
                if not released:
                    released = True
                    self.release()

            return _release_once

        loop = asyncio.get_running_loop()
        waiter: asyncio.Future[None] = loop.create_future()
        self._queue.append(waiter)

        def _on_abort() -> None:
            if not waiter.done():
                try:
                    self._queue.remove(waiter)
                except ValueError:
                    pass
                waiter.set_exception(RequestCancelledError())

        add_listener = (
            getattr(inbound_signal, "add_event_listener", None)
            or getattr(inbound_signal, "addEventListener", None)
            if inbound_signal is not None
            else None
        )
        remove_listener = (
            getattr(inbound_signal, "remove_event_listener", None)
            or getattr(inbound_signal, "removeEventListener", None)
            if inbound_signal is not None
            else None
        )

        if callable(add_listener):
            try:
                add_listener(_on_abort)
            except TypeError:
                add_listener("abort", _on_abort)

        try:
            timeout_sec = max(0.0, wait_timeout_ms / 1000.0)
            await asyncio.wait_for(waiter, timeout=timeout_sec)
        except TimeoutError as exc:
            try:
                self._queue.remove(waiter)
            except ValueError:
                pass
            if waiter.done() and not waiter.cancelled() and waiter.exception() is None:
                # Permit was handed off just as timeout fired; release it immediately!
                self.release()
            raise OverloadRejectedError("Password hashing wait timeout") from exc
        except Exception:
            try:
                self._queue.remove(waiter)
            except ValueError:
                pass
            raise
        finally:
            if callable(remove_listener):
                try:
                    remove_listener(_on_abort)
                except TypeError:
                    remove_listener("abort", _on_abort)

        released_queued = False

        def _release_queued_once() -> None:
            nonlocal released_queued
            if not released_queued:
                released_queued = True
                self.release()

        return _release_queued_once


def _resolve_options_dict(
    raw: PasswordHashBulkheadOptions | Mapping[str, Any] | None,
) -> dict[str, int]:
    if raw is None:
        return {}
    if isinstance(raw, PasswordHashBulkheadOptions):
        return {
            "max_concurrent": raw.max_concurrent,
            "max_queue": raw.max_queue,
            "max_wait_ms": raw.max_wait_ms,
        }
    if isinstance(raw, Mapping):
        out: dict[str, int] = {}
        mc = (
            raw.get("max_concurrent")
            if "max_concurrent" in raw
            else raw.get("maxConcurrent")
        )
        if mc is not None:
            out["max_concurrent"] = max(1, int(mc))
        mq = raw.get("max_queue") if "max_queue" in raw else raw.get("maxQueue")
        if mq is not None:
            out["max_queue"] = max(0, int(mq))
        mw = raw.get("max_wait_ms") if "max_wait_ms" in raw else raw.get("maxWaitMs")
        if mw is not None:
            out["max_wait_ms"] = max(0, int(mw))
        return out
    return {}


class PasswordHashBulkhead:
    """Bounded-concurrency FIFO wait queue bulkhead for Argon2id operations."""

    def __init__(
        self,
        config_service_or_options: Any = None,
        explicit_options: (
            PasswordHashBulkheadOptions | Mapping[str, Any] | None
        ) = None,
        *,
        max_concurrent: int | None = None,
        max_queue: int | None = None,
        max_wait_ms: int | None = None,
    ) -> None:
        config_service: Any = None
        opts: dict[str, int] = {}

        is_options_obj = isinstance(
            config_service_or_options, PasswordHashBulkheadOptions
        ) or (
            isinstance(config_service_or_options, Mapping)
            and any(
                k in config_service_or_options
                for k in (
                    "maxConcurrent",
                    "max_concurrent",
                    "maxQueue",
                    "max_queue",
                    "maxWaitMs",
                    "max_wait_ms",
                )
            )
        )

        if is_options_obj:
            opts = _resolve_options_dict(config_service_or_options)
        else:
            config_service = config_service_or_options
            opts = _resolve_options_dict(explicit_options)

        if max_concurrent is not None:
            opts["max_concurrent"] = max(1, max_concurrent)
        if max_queue is not None:
            opts["max_queue"] = max(0, max_queue)
        if max_wait_ms is not None:
            opts["max_wait_ms"] = max(0, max_wait_ms)

        self.max_concurrent: int = opts.get(
            "max_concurrent",
            _parse_positive_int(
                _read_config_val(
                    config_service,
                    "AUTH_PASSWORD_HASH_MAX_CONCURRENT",
                ),
                DEFAULT_AUTH_PASSWORD_HASH_MAX_CONCURRENT,
            ),
        )
        self.max_queue: int = opts.get(
            "max_queue",
            _parse_non_negative_int(
                _read_config_val(
                    config_service,
                    "AUTH_PASSWORD_HASH_MAX_QUEUE",
                ),
                DEFAULT_AUTH_PASSWORD_HASH_MAX_QUEUE,
            ),
        )
        self.max_wait_ms: int = opts.get(
            "max_wait_ms",
            _parse_non_negative_int(
                _read_config_val(
                    config_service,
                    "AUTH_PASSWORD_HASH_MAX_WAIT_MS",
                ),
                DEFAULT_AUTH_PASSWORD_HASH_MAX_WAIT_MS,
            ),
        )

        self._limiter = _FIFOConcurrencyLimiter(self.max_concurrent)
        self._hasher = PasswordHasher(
            time_cost=ARGON2_TIME_COST,
            memory_cost=ARGON2_MEMORY_COST,
            parallelism=ARGON2_PARALLELISM,
            type=Type.ID,
        )

    @property
    def maxConcurrent(self) -> int:
        return self.max_concurrent

    @property
    def maxQueue(self) -> int:
        return self.max_queue

    @property
    def maxWaitMs(self) -> int:
        return self.max_wait_ms

    @property
    def active_count(self) -> int:
        return self._limiter.active_count

    @property
    def activeCount(self) -> int:
        return self._limiter.active_count

    @property
    def queued_count(self) -> int:
        return self._limiter.queued_count

    @property
    def queuedCount(self) -> int:
        return self._limiter.queued_count

    def is_queue_full(self) -> bool:
        if self._limiter.active_count < self.max_concurrent:
            return False
        return self._limiter.queued_count >= self.max_queue

    @overload
    async def run(self, fn: Callable[[], Awaitable[T]]) -> T: ...

    @overload
    async def run(self, fn: Callable[[], T]) -> T: ...

    async def run(self, fn: Callable[[], Awaitable[T]] | Callable[[], T]) -> T:
        inbound_signal = get_request_signal()
        if inbound_signal is not None and inbound_signal.aborted:
            throw_if_aborted()
            raise RequestCancelledError("Inbound request already aborted")

        if self.is_queue_full():
            raise OverloadRejectedError("Password hashing capacity exceeded")

        budget_remaining = remaining_ms()
        wait_timeout_ms = max(
            0,
            self.max_wait_ms
            if budget_remaining is None
            else min(self.max_wait_ms, budget_remaining),
        )

        try:
            release = await self._limiter.acquire(
                wait_timeout_ms=wait_timeout_ms,
                inbound_signal=inbound_signal,
            )
        except Exception as exc:
            if inbound_signal is not None and inbound_signal.aborted:
                throw_if_aborted()
                raise exc
            if isinstance(exc, OverloadRejectedError):
                raise
            raise OverloadRejectedError("Password hashing wait timeout") from exc

        try:
            result = fn()
            if inspect.isawaitable(result):
                return await result
            return result
        finally:
            release()

    async def hash(self, password: str) -> str:
        """Hash a plaintext password using Argon2id inside the threadpool bulkhead."""
        return await self.run(lambda: asyncio.to_thread(self._hasher.hash, password))

    async def hash_password(self, password: str) -> str:
        return await self.hash(password)

    async def verify(self, password_hash: str, password: str) -> bool:
        """Verify a password against an Argon2id hash inside the threadpool bulkhead."""

        def _do_verify() -> bool:
            try:
                return bool(self._hasher.verify(password_hash, password))
            except (VerifyMismatchError, VerificationError, InvalidHashError):
                return False

        return await self.run(lambda: asyncio.to_thread(_do_verify))

    async def verify_password(self, password_hash: str, password: str) -> bool:
        return await self.verify(password_hash, password)


PasswordHashBulkheadService = PasswordHashBulkhead

__all__ = [
    "PasswordHashBulkhead",
    "PasswordHashBulkheadOptions",
    "PasswordHashBulkheadService",
]
