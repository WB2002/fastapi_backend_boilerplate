from __future__ import annotations

import os
import time
import uuid
from typing import Any

from opentelemetry import trace as otel_trace
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.common.config.parsers import parse_log_redaction_config
from app.core.logger.logger_config import get_logger
from app.core.logger.logger_context import LoggerContext
from app.core.redaction.redactor import redact_url
from app.core.telemetry.otel import is_trace_exempt_path


def _extract_header(scope: Scope, name_lower: bytes) -> str | None:
    headers: list[tuple[bytes, bytes]] = scope.get("headers") or []
    for key, val in headers:
        if key.lower() == name_lower:
            try:
                decoded = val.decode("latin-1").strip()
                if decoded:
                    return decoded
            except Exception:
                return None
    return None


def _extract_user_id(scope: Scope) -> str:
    state = scope.get("state")
    user: Any = None
    if isinstance(state, dict):
        user = state.get("user")
    elif state is not None:
        user = getattr(state, "user", None)
    if user is None:
        user = scope.get("user")
    if isinstance(user, dict):
        uid = user.get("id") or user.get("sub")
        if isinstance(uid, str) and uid.strip():
            return uid.strip()
    elif user is not None:
        uid = getattr(user, "id", None) or getattr(user, "sub", None)
        if isinstance(uid, str) and uid.strip():
            return uid.strip()
    return "anonymous"


def _build_raw_url(scope: Scope) -> str:
    path = str(scope.get("path", "/"))
    query_bytes: bytes = scope.get("query_string") or b""
    if query_bytes:
        qs = query_bytes.decode("latin-1", errors="replace")
        if qs:
            return f"{path}?{qs}"
    return path


def _resolve_active_span_ids(url: str) -> tuple[str | None, str | None]:
    if is_trace_exempt_path(url):
        return None, None
    span = otel_trace.get_current_span()
    span_ctx = span.get_span_context()
    if span_ctx is not None and span_ctx.is_valid:
        trace_id = f"{span_ctx.trace_id:032x}"
        span_id = f"{span_ctx.span_id:016x}"
        return trace_id, span_id
    return None, None


class LoggingInterceptor:
    """Logs HTTP access line `${method} ${safeUrl} ${statusCode} - ${delay}ms`."""

    def __init__(self) -> None:
        self._logger = get_logger("LoggingInterceptor")

    def log_request(
        self,
        *,
        method: str,
        url: str,
        status_code: int,
        duration_ms: int,
    ) -> dict[str, Any]:
        is_redaction_enabled = parse_log_redaction_config(os.environ).enabled
        safe_url = redact_url(url) if is_redaction_enabled else url
        return self._logger.log(f"{method} {safe_url} {status_code} - {duration_ms}ms")


class LoggerMiddleware:
    """Pure ASGI middleware binding LoggerContext, x-request-id, and access logging."""

    def __init__(
        self,
        app: ASGIApp,
        *,
        log_completion: bool = True,
    ) -> None:
        self.app = app
        self._log_completion = log_completion
        self._logging_interceptor = LoggingInterceptor()

    async def __call__(
        self,
        scope: Scope,
        receive: Receive,
        send: Send,
    ) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        inbound_req_id = _extract_header(scope, b"x-request-id")
        request_id = inbound_req_id if inbound_req_id else str(uuid.uuid4())
        user_id = _extract_user_id(scope)
        url = _build_raw_url(scope)
        method = str(scope.get("method", "GET"))
        trace_id, span_id = _resolve_active_span_ids(url)

        if "state" not in scope or not isinstance(scope.get("state"), dict):
            scope["state"] = {}
        scope["state"]["request_id"] = request_id

        token = LoggerContext.set(
            request_id=request_id,
            user_id=user_id,
            trace_id=trace_id,
            span_id=span_id,
        )
        started_ns = time.monotonic_ns()
        status_code = 500

        async def send_wrapper(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = int(message.get("status", 200))
                raw_headers: list[tuple[bytes, bytes]] = list(
                    message.get("headers") or []
                )
                filtered = [
                    (k, v) for k, v in raw_headers if k.lower() != b"x-request-id"
                ]
                filtered.append((b"x-request-id", request_id.encode("latin-1")))
                message = {**message, "headers": filtered}
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            if self._log_completion:
                elapsed_ms = max(
                    0,
                    int((time.monotonic_ns() - started_ns) // 1_000_000),
                )
                self._logging_interceptor.log_request(
                    method=method,
                    url=url,
                    status_code=status_code,
                    duration_ms=elapsed_ms,
                )
            LoggerContext.reset(token)


__all__ = ["LoggerMiddleware", "LoggingInterceptor"]
