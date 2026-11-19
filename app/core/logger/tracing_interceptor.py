from __future__ import annotations

import functools
import inspect
from collections.abc import Awaitable, Callable
from typing import Any, overload

from opentelemetry import trace as otel_trace

from app.core.logger.logger_context import LoggerContext
from app.core.telemetry.otel import should_skip_tracing
from app.types.logger_types import ILoggerContext


def _resolve_callable_names(
    func: Callable[..., Any],
    *,
    class_name: str | None = None,
    method_name: str | None = None,
) -> tuple[str, str]:
    raw_name = getattr(func, "__name__", None)
    resolved_method: str = (
        method_name
        if method_name is not None
        else (str(raw_name) if raw_name is not None else "unknown")
    )
    if class_name is not None:
        return class_name, resolved_method
    qualname = str(getattr(func, "__qualname__", "") or "")
    if "." in qualname:
        parts = qualname.split(".")
        if len(parts) >= 2:
            return str(parts[-2]), resolved_method
    mod_name = getattr(func, "__module__", None)
    return (str(mod_name) if mod_name is not None else "App"), resolved_method


@overload
def trace[F: Callable[..., Any]](
    func: F,
    *,
    context: str | None = None,
    method: str | None = None,
) -> F: ...


@overload
def trace[F: Callable[..., Any]](
    func: None = None,
    *,
    context: str | None = None,
    method: str | None = None,
) -> Callable[[F], F]: ...


def trace[F: Callable[..., Any]](
    func: F | None = None,
    *,
    context: str | None = None,
    method: str | None = None,
) -> F | Callable[[F], F]:
    """Decorator binding `context` and `method` into LoggerContext."""

    def _decorator(target: F) -> F:
        cls_name, fn_name = _resolve_callable_names(
            target,
            class_name=context,
            method_name=method,
        )

        if inspect.iscoroutinefunction(target):

            @functools.wraps(target)
            async def _async_wrapper(*args: Any, **kwargs: Any) -> Any:
                resolved_cls = cls_name
                if (
                    args
                    and not context
                    and hasattr(args[0], "__class__")
                    and not isinstance(args[0], type)
                ):
                    resolved_cls = args[0].__class__.__name__
                with LoggerContext.scope(
                    context=resolved_cls,
                    method=fn_name,
                ):
                    return await target(*args, **kwargs)

            return _async_wrapper  # type: ignore[return-value]

        @functools.wraps(target)
        def _sync_wrapper(*args: Any, **kwargs: Any) -> Any:
            resolved_cls = cls_name
            if (
                args
                and not context
                and hasattr(args[0], "__class__")
                and not isinstance(args[0], type)
            ):
                resolved_cls = args[0].__class__.__name__
            with LoggerContext.scope(
                context=resolved_cls,
                method=fn_name,
            ):
                return target(*args, **kwargs)

        return _sync_wrapper  # type: ignore[return-value]

    if func is not None:
        return _decorator(func)
    return _decorator


class TracingInterceptor:
    """Sets OTel span attributes and updates LoggerContext."""

    @staticmethod
    def enter_handler(
        *,
        class_name: str,
        method_name: str,
        endpoint: object | None = None,
        controller_class: object | None = None,
        url: str | None = None,
    ) -> ILoggerContext:
        skip = should_skip_tracing(
            endpoint=endpoint,
            url=url,
            controller_class=controller_class,
        )
        base = dict(LoggerContext.get())
        base["context"] = class_name
        base["method"] = method_name

        if skip:
            return base  # type: ignore[return-value]

        active_span = otel_trace.get_current_span()
        if active_span is not None and active_span.is_recording():
            active_span.set_attributes(
                {
                    "code.namespace": class_name,
                    "code.function": method_name,
                }
            )
            span_ctx = active_span.get_span_context()
            if span_ctx is not None and span_ctx.is_valid:
                base["traceId"] = f"{span_ctx.trace_id:032x}"
                base["spanId"] = f"{span_ctx.span_id:016x}"

        return base  # type: ignore[return-value]

    @classmethod
    def intercept_sync[T](
        cls,
        *,
        class_name: str,
        method_name: str,
        handler: Callable[[], T],
        endpoint: object | None = None,
        controller_class: object | None = None,
        url: str | None = None,
    ) -> T:
        new_ctx = cls.enter_handler(
            class_name=class_name,
            method_name=method_name,
            endpoint=endpoint,
            controller_class=controller_class,
            url=url,
        )
        return LoggerContext.run(new_ctx, handler)

    @classmethod
    async def intercept_async[T](
        cls,
        *,
        class_name: str,
        method_name: str,
        handler: Callable[[], Awaitable[T]],
        endpoint: object | None = None,
        controller_class: object | None = None,
        url: str | None = None,
    ) -> T:
        new_ctx = cls.enter_handler(
            class_name=class_name,
            method_name=method_name,
            endpoint=endpoint,
            controller_class=controller_class,
            url=url,
        )
        token = LoggerContext.set(new_ctx)
        try:
            return await handler()
        finally:
            LoggerContext.reset(token)


Trace = trace

__all__ = ["Trace", "TracingInterceptor", "trace"]
