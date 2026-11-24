from __future__ import annotations

import asyncio
import math
import time
from collections.abc import Callable, Mapping
from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from typing import Any, Final, Literal
from weakref import WeakKeyDictionary

from app.common.config.parsers import (
    ResolvedTimeout,
    parse_js_number,
    parse_positive_timeout_ms,
    resolve_timeout_ms,
)

REQUEST_DEADLINE_OPTIONS: Final[str] = "REQUEST_DEADLINE_OPTIONS"
SKIP_REQUEST_DEADLINE_KEY: Final[str] = "skipRequestDeadline"
REQUEST_TIMEOUT_KEY: Final[str] = "requestTimeoutMs"

REQUEST_TIMEOUT_HEADER: Final[str] = "x-request-timeout-ms"
REQUEST_DEADLINE_HEADER: Final[str] = "x-request-deadline"

DEFAULT_REQUEST_TIMEOUT_MS: Final[int] = 30_000
DEFAULT_REQUEST_TIMEOUT_MAX_MS: Final[int] = 120_000

HTTP_STATUS_CLIENT_CLOSED_REQUEST: Final[int] = 499

type RequestDeadlineAbortReason = Literal["timeout", "client_disconnected"]


class RequestCancelledError(Exception):
    """Raised when a request is cancelled by client disconnect (HTTP 499)."""

    code: Final[str] = "REQUEST_CANCELLED"

    def __init__(self, message: str = "Request cancelled by client") -> None:
        super().__init__(message)
        self.message = message
        self.name = "RequestCancelledError"


class RequestDeadlineExceededError(Exception):
    """Raised when a request exceeds its resolved deadline budget (HTTP 504)."""

    code: Final[str] = "REQUEST_DEADLINE_EXCEEDED"

    def __init__(self, message: str = "Request deadline exceeded") -> None:
        super().__init__(message)
        self.message = message
        self.name = "RequestDeadlineExceededError"


class RequestAbortSignal:
    """Async/callback-compatible cancellation signal mirroring JS AbortSignal."""

    def __init__(self) -> None:
        self._aborted = False
        self._reason: RequestDeadlineAbortReason | None = None
        self._event: asyncio.Event | None = None
        self._listeners: list[Callable[[], None]] = []

    @property
    def aborted(self) -> bool:
        return self._aborted

    @property
    def reason(self) -> RequestDeadlineAbortReason | None:
        return self._reason

    def is_set(self) -> bool:
        return self._aborted

    async def wait(self) -> None:
        if self._aborted:
            return
        if self._event is None:
            self._event = asyncio.Event()
        await self._event.wait()

    def add_event_listener(self, listener: Callable[[], None]) -> None:
        if self._aborted:
            listener()
            return
        if listener not in self._listeners:
            self._listeners.append(listener)

    def remove_event_listener(self, listener: Callable[[], None]) -> None:
        if listener in self._listeners:
            self._listeners.remove(listener)

    def _trigger(self, reason: RequestDeadlineAbortReason) -> None:
        if self._aborted:
            return
        self._aborted = True
        self._reason = reason
        if self._event is not None:
            self._event.set()
        for listener in list(self._listeners):
            try:
                listener()
            except Exception:  # noqa: S110
                pass


@dataclass(slots=True)
class RequestDeadlineModuleOptions:
    default_timeout_ms: int = DEFAULT_REQUEST_TIMEOUT_MS
    max_timeout_ms: int = DEFAULT_REQUEST_TIMEOUT_MAX_MS


DEFAULT_REQUEST_DEADLINE_OPTIONS: Final[RequestDeadlineModuleOptions] = (
    RequestDeadlineModuleOptions(
        default_timeout_ms=DEFAULT_REQUEST_TIMEOUT_MS,
        max_timeout_ms=DEFAULT_REQUEST_TIMEOUT_MAX_MS,
    )
)


@dataclass(slots=True)
class RequestDeadlineStore:
    signal: RequestAbortSignal
    started_at: int
    deadline_at: int
    reason: RequestDeadlineAbortReason | None = None

    @property
    def startedAt(self) -> int:
        return self.started_at

    @property
    def deadlineAt(self) -> int:
        return self.deadline_at

    @deadlineAt.setter
    def deadlineAt(self, value: int) -> None:
        self.deadline_at = value


@dataclass(slots=True)
class RequestDeadlineInternals:
    clear_timer: Callable[[], None]
    reschedule_from_start: Callable[[int], None]
    set_reason: Callable[[RequestDeadlineAbortReason], None]
    abort: Callable[[RequestDeadlineAbortReason], None] = field(default=lambda _r: None)


_deadline_context_var: ContextVar[RequestDeadlineStore | None] = ContextVar(
    "request_deadline_context",
    default=None,
)

_internals_by_signal: WeakKeyDictionary[
    RequestAbortSignal, RequestDeadlineInternals
] = WeakKeyDictionary()


class DeadlineContext:
    """Per-request deadline ContextVar storage matching NestJS DeadlineContext."""

    @staticmethod
    def set(
        store: RequestDeadlineStore,
        internals: RequestDeadlineInternals | None = None,
    ) -> Token[RequestDeadlineStore | None]:
        if internals is not None:
            _internals_by_signal[store.signal] = internals
        return _deadline_context_var.set(store)

    @staticmethod
    def reset(token: Token[RequestDeadlineStore | None]) -> None:
        current = _deadline_context_var.get()
        _deadline_context_var.reset(token)
        if current is not None and current is not token.old_value:
            _internals_by_signal.pop(current.signal, None)

    @staticmethod
    def run[T](
        store: RequestDeadlineStore,
        internals: RequestDeadlineInternals,
        callback: Callable[[], T],
    ) -> T:
        token = DeadlineContext.set(store, internals)
        try:
            return callback()
        finally:
            DeadlineContext.reset(token)

    @staticmethod
    def get() -> RequestDeadlineStore | None:
        return _deadline_context_var.get()

    @staticmethod
    def get_internals(
        signal: RequestAbortSignal | None = None,
    ) -> RequestDeadlineInternals | None:
        current = _deadline_context_var.get()
        key = (
            signal
            if signal is not None
            else (current.signal if current is not None else None)
        )
        if key is None:
            return None
        return _internals_by_signal.get(key)

    @staticmethod
    def set_reason(reason: RequestDeadlineAbortReason) -> None:
        store = _deadline_context_var.get()
        if store is not None:
            store.reason = reason


def request_timeout(ms: int) -> Callable[[Any], Any]:
    """Route/class decorator setting custom server request timeout in milliseconds."""

    def _decorator(target: Any) -> Any:
        setattr(target, REQUEST_TIMEOUT_KEY, ms)
        return target

    return _decorator


def skip_request_deadline[F: Callable[..., Any] | type[Any]](
    target: F | None = None,
) -> Any:
    """Route/class decorator disabling the request deadline timer."""

    def _decorator(fn: F) -> F:
        setattr(fn, SKIP_REQUEST_DEADLINE_KEY, True)
        return fn

    if target is not None:
        return _decorator(target)
    return _decorator


def parse_timeout_header(
    headers: Mapping[str, Any] | None,
) -> int | None:
    """Parse a positive integer timeout from the x-request-timeout-ms request header."""
    if not headers:
        return None
    raw: Any = None
    for k, v in headers.items():
        if k.lower() == REQUEST_TIMEOUT_HEADER:
            raw = v
            break
    value = (raw[0] if raw else None) if isinstance(raw, (list, tuple)) else raw
    if value is None or value == "" or isinstance(value, bool):
        return None
    n = (
        float(value)
        if isinstance(value, (int, float))
        else parse_js_number(str(value).strip())
    )
    if not math.isfinite(n) or n <= 0:
        return None
    return math.floor(n)


def get_request_signal() -> RequestAbortSignal | None:
    store = DeadlineContext.get()
    return store.signal if store is not None else None


def get_deadline_at() -> int | None:
    store = DeadlineContext.get()
    return store.deadline_at if store is not None else None


def get_deadline_reason() -> RequestDeadlineAbortReason | None:
    store = DeadlineContext.get()
    return store.reason if store is not None else None


def remaining_ms(now_ms: int | None = None) -> int | None:
    deadline_at = get_deadline_at()
    if deadline_at is None:
        return None
    current = int(time.time() * 1000) if now_ms is None else now_ms
    return max(0, deadline_at - current)


get_remaining_budget_ms = remaining_ms


def throw_if_aborted() -> None:
    """Raise RequestCancelledError or RequestDeadlineExceededError if aborted."""
    store = DeadlineContext.get()
    if store is None or not store.signal.aborted:
        return
    reason = store.reason or store.signal.reason
    if reason == "client_disconnected":
        raise RequestCancelledError()
    raise RequestDeadlineExceededError()


def resolve_outbound_deadline_headers(
    *,
    propagate: bool,
    inbound_deadline_at: int | None = None,
    existing_headers: Mapping[str, str | None] | None = None,
) -> dict[str, str]:
    """Propagate x-request-deadline header to outbound calls unless already set."""
    if not propagate or inbound_deadline_at is None:
        return {}
    if existing_headers:
        for key, val in existing_headers.items():
            if key.lower() == REQUEST_DEADLINE_HEADER and val is not None:
                return {}
    return {REQUEST_DEADLINE_HEADER: str(inbound_deadline_at)}


RequestTimeout = request_timeout
SkipRequestDeadline = skip_request_deadline

__all__ = [
    "DEFAULT_REQUEST_DEADLINE_OPTIONS",
    "DEFAULT_REQUEST_TIMEOUT_MAX_MS",
    "DEFAULT_REQUEST_TIMEOUT_MS",
    "HTTP_STATUS_CLIENT_CLOSED_REQUEST",
    "REQUEST_DEADLINE_HEADER",
    "REQUEST_DEADLINE_OPTIONS",
    "REQUEST_TIMEOUT_HEADER",
    "REQUEST_TIMEOUT_KEY",
    "SKIP_REQUEST_DEADLINE_KEY",
    "DeadlineContext",
    "RequestAbortSignal",
    "RequestCancelledError",
    "RequestDeadlineAbortReason",
    "RequestDeadlineExceededError",
    "RequestDeadlineInternals",
    "RequestDeadlineModuleOptions",
    "RequestDeadlineStore",
    "RequestTimeout",
    "ResolvedTimeout",
    "SkipRequestDeadline",
    "get_deadline_at",
    "get_deadline_reason",
    "get_remaining_budget_ms",
    "get_request_signal",
    "parse_positive_timeout_ms",
    "parse_timeout_header",
    "remaining_ms",
    "request_timeout",
    "resolve_outbound_deadline_headers",
    "resolve_timeout_ms",
    "skip_request_deadline",
    "throw_if_aborted",
]
