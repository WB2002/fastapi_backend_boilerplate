from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar, Token
from typing import Any

from app.types.logger_types import ILoggerContext

_logger_context_var: ContextVar[ILoggerContext | None] = ContextVar(
    "logger_context",
    default=None,
)

_KEY_ALIASES: tuple[tuple[str, str], ...] = (
    ("request_id", "requestId"),
    ("user_id", "userId"),
    ("trace_id", "traceId"),
    ("span_id", "spanId"),
)

_ALLOWED_KEYS: tuple[str, ...] = (
    "requestId",
    "userId",
    "traceId",
    "spanId",
    "context",
    "method",
)


def _normalize_context(
    data: Mapping[str, Any] | ILoggerContext | None = None,
    *,
    request_id: str | None = None,
    user_id: str | None = None,
    trace_id: str | None = None,
    span_id: str | None = None,
    context: str | None = None,
    method: str | None = None,
) -> ILoggerContext:
    merged: dict[str, Any] = (
        {k: v for k, v in data.items() if v is not None} if data else {}
    )
    for snake_key, camel_key in _KEY_ALIASES:
        if snake_key in merged and camel_key not in merged:
            merged[camel_key] = merged.pop(snake_key)

    overrides: tuple[tuple[str, str | None], ...] = (
        ("requestId", request_id),
        ("userId", user_id),
        ("traceId", trace_id),
        ("spanId", span_id),
        ("context", context),
        ("method", method),
    )
    for key, val in overrides:
        if val is not None:
            merged[key] = val

    out: dict[str, str] = {
        k: merged[k]
        for k in _ALLOWED_KEYS
        if k in merged and isinstance(merged[k], str)
    }
    return out  # type: ignore[return-value]


class LoggerContext:
    """Async/request-scoped logger context backed by ContextVar."""

    @staticmethod
    def get() -> ILoggerContext:
        current = _logger_context_var.get()
        if current is None:
            return {}
        return dict(current)  # type: ignore[return-value]

    @staticmethod
    def set(
        ctx: Mapping[str, Any] | ILoggerContext | None = None,
        *,
        request_id: str | None = None,
        user_id: str | None = None,
        trace_id: str | None = None,
        span_id: str | None = None,
        context: str | None = None,
        method: str | None = None,
    ) -> Token[ILoggerContext | None]:
        normalized = _normalize_context(
            ctx,
            request_id=request_id,
            user_id=user_id,
            trace_id=trace_id,
            span_id=span_id,
            context=context,
            method=method,
        )
        return _logger_context_var.set(normalized)

    @staticmethod
    def update(
        ctx: Mapping[str, Any] | ILoggerContext | None = None,
        *,
        request_id: str | None = None,
        user_id: str | None = None,
        trace_id: str | None = None,
        span_id: str | None = None,
        context: str | None = None,
        method: str | None = None,
    ) -> Token[ILoggerContext | None]:
        base = dict(LoggerContext.get())
        if ctx:
            base.update(ctx)
        return LoggerContext.set(
            base,
            request_id=request_id,
            user_id=user_id,
            trace_id=trace_id,
            span_id=span_id,
            context=context,
            method=method,
        )

    @staticmethod
    def reset(token: Token[ILoggerContext | None]) -> None:
        _logger_context_var.reset(token)

    @staticmethod
    def clear() -> None:
        _logger_context_var.set(None)

    @staticmethod
    def run[T](
        ctx: Mapping[str, Any] | ILoggerContext,
        callback: Callable[[], T],
    ) -> T:
        token = LoggerContext.set(ctx)
        try:
            return callback()
        finally:
            LoggerContext.reset(token)

    @staticmethod
    @contextmanager
    def scope(
        ctx: Mapping[str, Any] | ILoggerContext | None = None,
        *,
        request_id: str | None = None,
        user_id: str | None = None,
        trace_id: str | None = None,
        span_id: str | None = None,
        context: str | None = None,
        method: str | None = None,
    ) -> Iterator[ILoggerContext]:
        token = LoggerContext.update(
            ctx,
            request_id=request_id,
            user_id=user_id,
            trace_id=trace_id,
            span_id=span_id,
            context=context,
            method=method,
        )
        try:
            yield LoggerContext.get()
        finally:
            LoggerContext.reset(token)


__all__ = ["LoggerContext"]
