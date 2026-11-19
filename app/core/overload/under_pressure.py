from __future__ import annotations

import asyncio
import datetime
import json
import time
import uuid
from collections.abc import Callable, Mapping
from typing import Any, Final

import psutil
from starlette.types import ASGIApp, Receive, Scope, Send

from app.common.config.parsers import (
    DEFAULT_OVERLOAD_RETRY_AFTER_SEC,
    DEFAULT_UNDER_PRESSURE_MAX_EVENT_LOOP_DELAY_MS,
    UnderPressureConfig,
    parse_under_pressure_config,
)
from app.core.overload.overload_telemetry import record_under_pressure_503

PRESSURE_EXEMPT_PATHS: Final[tuple[str, ...]] = ("/health", "/health/ready")


def normalize_request_path(url: str) -> str:
    """Strip query string and trailing slash (except root '/')."""
    raw = url.split("?", 1)[0]
    if len(raw) > 1 and raw.endswith("/"):
        return raw[:-1]
    return raw


def is_pressure_exempt_path(url: str) -> bool:
    """Return True if the normalized URL is in PRESSURE_EXEMPT_PATHS."""
    path = normalize_request_path(url)
    return path in PRESSURE_EXEMPT_PATHS


def _extract_url_and_request_id(
    request: Any,
) -> tuple[str, str]:
    url = "/"
    headers: Any = None
    if isinstance(request, Mapping):
        if isinstance(request.get("url"), str):
            url = request["url"]
        elif isinstance(request.get("path"), str):
            qs = request["query_string"]
            if isinstance(qs, (bytes, bytearray)) and qs:
                url = f"{request['path']}?{qs.decode('latin-1')}"
            else:
                url = request["path"]
        headers = request.get("headers")
    else:
        raw_url = getattr(request, "url", None)
        if isinstance(raw_url, str):
            url = raw_url
        elif raw_url is not None and hasattr(raw_url, "path"):
            p = str(raw_url.path)
            q = str(getattr(raw_url, "query", "") or "")
            url = f"{p}?{q}" if q else p
        headers = getattr(request, "headers", None)

    request_id = ""
    if isinstance(headers, Mapping):
        for k, v in headers.items():
            if str(k).lower() == "x-request-id" and isinstance(v, str) and v:
                request_id = v
                break
    elif isinstance(headers, list):
        for item in headers:
            if (
                isinstance(item, tuple)
                and len(item) == 2
                and isinstance(item[0], (bytes, bytearray))
                and item[0].lower() == b"x-request-id"
            ):
                decoded = bytes(item[1]).decode("latin-1")
                if decoded:
                    request_id = decoded
                    break

    if not request_id:
        request_id = str(uuid.uuid4())
    return url, request_id


def build_overload_error_response(
    request: Any,
    message: str = "Service temporarily unavailable",
) -> dict[str, Any]:
    """Construct the standard 503 overload JSON body matching NestJS."""
    url, request_id = _extract_url_and_request_id(request)
    now_iso = (
        datetime.datetime.now(datetime.UTC)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )
    return {
        "statusCode": 503,
        "timestamp": now_iso,
        "path": url,
        "requestId": request_id,
        "message": message,
    }


def _resolve_under_pressure_config(
    config: UnderPressureConfig | Mapping[str, Any] | None,
) -> UnderPressureConfig:
    if isinstance(config, UnderPressureConfig):
        return config
    if isinstance(config, Mapping):
        enabled = bool(config.get("enabled", False))
        max_delay = int(
            config["max_event_loop_delay_ms"]
            if "max_event_loop_delay_ms" in config
            else config.get(
                "maxEventLoopDelayMs",
                DEFAULT_UNDER_PRESSURE_MAX_EVENT_LOOP_DELAY_MS,
            )
        )
        max_heap = int(
            config["max_heap_used_bytes"]
            if "max_heap_used_bytes" in config
            else config.get("maxHeapUsedBytes", 0)
        )
        max_rss = int(
            config["max_rss_bytes"]
            if "max_rss_bytes" in config
            else config.get("maxRssBytes", 0)
        )
        retry_after = max(
            1,
            int(
                config["retry_after_sec"]
                if "retry_after_sec" in config
                else config.get("retryAfterSec", DEFAULT_OVERLOAD_RETRY_AFTER_SEC)
            ),
        )
        expose_status = bool(
            config["expose_status_route"]
            if "expose_status_route" in config
            else config.get("exposeStatusRoute", False)
        )
        return UnderPressureConfig(
            enabled=enabled,
            max_event_loop_delay_ms=max_delay,
            max_heap_used_bytes=max_heap,
            max_rss_bytes=max_rss,
            retry_after_sec=retry_after,
            expose_status_route=expose_status,
        )
    return parse_under_pressure_config()


def create_pressure_handler(
    config: UnderPressureConfig | Mapping[str, Any],
) -> Callable[[Any, Any, str, int | float | None], dict[str, Any] | None]:
    """Create a Fastify/ASGI pressure handler matching `createPressureHandler`."""
    resolved = _resolve_under_pressure_config(config)

    def _handler(
        request: Any,
        reply: Any,
        pressure_type: str,
        _value: int | float | None = None,
    ) -> dict[str, Any] | None:
        url, _ = _extract_url_and_request_id(request)
        if is_pressure_exempt_path(url):
            return None

        record_under_pressure_503(
            path=url,
            pressure_type=pressure_type,
        )
        body = build_overload_error_response(request)
        if reply is not None:
            code_fn = getattr(reply, "code", None)
            if callable(code_fn):
                r1 = code_fn(503) or reply
                header_fn = getattr(r1, "header", None) or getattr(
                    reply, "header", None
                )
                r2 = (
                    header_fn("Retry-After", str(resolved.retry_after_sec))
                    if callable(header_fn)
                    else r1
                ) or r1
                send_fn = getattr(r2, "send", None) or getattr(reply, "send", None)
                if callable(send_fn):
                    send_fn(body)
        return body

    return _handler


class UnderPressureMonitor:
    """Monitors event-loop lag and RSS/heap memory usage."""

    def __init__(
        self,
        config: UnderPressureConfig | Mapping[str, Any] | None = None,
        *,
        event_loop_delay_sampler: Callable[[], float] | None = None,
        rss_bytes_sampler: Callable[[], int] | None = None,
        heap_used_bytes_sampler: Callable[[], int] | None = None,
    ) -> None:
        self.config = _resolve_under_pressure_config(config)
        self._event_loop_delay_ms: float = 0.0
        self._delay_sampler = event_loop_delay_sampler
        self._rss_sampler = rss_bytes_sampler
        self._heap_sampler = heap_used_bytes_sampler
        self._task: asyncio.Task[None] | None = None

    def set_event_loop_delay_ms(self, delay_ms: float) -> None:
        self._event_loop_delay_ms = max(0.0, float(delay_ms))

    def sample_event_loop_delay_ms(self) -> float:
        if self._delay_sampler is not None:
            return float(self._delay_sampler())
        return self._event_loop_delay_ms

    def sample_rss_bytes(self) -> int:
        if self._rss_sampler is not None:
            return int(self._rss_sampler())
        try:
            return int(psutil.Process().memory_info().rss)
        except Exception:
            return 0

    def sample_heap_used_bytes(self) -> int:
        if self._heap_sampler is not None:
            return int(self._heap_sampler())
        return self.sample_rss_bytes()

    def memory_usage(self) -> dict[str, int | float]:
        return {
            "eventLoopDelay": self.sample_event_loop_delay_ms(),
            "rssBytes": self.sample_rss_bytes(),
            "heapUsed": self.sample_heap_used_bytes(),
        }

    def check_pressure(self) -> tuple[str, int | float] | None:
        """Return `(pressure_type, value)` if any configured threshold is exceeded."""
        if not self.config.enabled:
            return None

        if self.config.max_event_loop_delay_ms > 0:
            delay = self.sample_event_loop_delay_ms()
            if delay > self.config.max_event_loop_delay_ms:
                return "eventLoopDelay", delay

        if self.config.max_heap_used_bytes > 0:
            heap = self.sample_heap_used_bytes()
            if heap > self.config.max_heap_used_bytes:
                return "heapUsedBytes", heap

        if self.config.max_rss_bytes > 0:
            rss = self.sample_rss_bytes()
            if rss > self.config.max_rss_bytes:
                return "rssBytes", rss

        return None

    def is_under_pressure(self) -> bool:
        return self.check_pressure() is not None

    async def start_background_sampler(
        self,
        interval_ms: int = 250,
    ) -> None:
        if not self.config.enabled or self._task is not None:
            return

        interval_sec = max(0.01, interval_ms / 1000.0)

        async def _loop() -> None:
            try:
                while True:
                    t0 = time.perf_counter()
                    await asyncio.sleep(interval_sec)
                    elapsed_ms = (time.perf_counter() - t0 - interval_sec) * 1000.0
                    self._event_loop_delay_ms = max(0.0, elapsed_ms)
            except asyncio.CancelledError:
                return

        self._task = asyncio.create_task(_loop())

    async def stop_background_sampler(self) -> None:
        task = self._task
        self._task = None
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):  # noqa: S110
                pass


class UnderPressureMiddleware:
    """Pure ASGI under-pressure monitor middleware with optional `GET /status`."""

    def __init__(
        self,
        app: ASGIApp,
        *,
        config: UnderPressureConfig | Mapping[str, Any] | None = None,
        monitor: UnderPressureMonitor | None = None,
    ) -> None:
        self.app = app
        self.config = _resolve_under_pressure_config(config)
        self.monitor = monitor or UnderPressureMonitor(self.config)
        self._pressure_handler = create_pressure_handler(self.config)

    async def __call__(
        self,
        scope: Scope,
        receive: Receive,
        send: Send,
    ) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        if not self.config.enabled:
            await self.app(scope, receive, send)
            return

        method = str(scope.get("method", "GET")).upper()
        path = str(scope.get("path", "/"))
        qs = scope.get("query_string")
        url = (
            f"{path}?{qs.decode('latin-1')}"
            if isinstance(qs, (bytes, bytearray)) and qs
            else path
        )

        pressure = self.monitor.check_pressure()
        if pressure is not None:
            pressure_type, pressure_val = pressure
            body = self._pressure_handler(
                scope,
                None,
                pressure_type,
                pressure_val,
            )
            if body is not None:
                payload_bytes = json.dumps(body).encode("utf-8")
                await send(
                    {
                        "type": "http.response.start",
                        "status": 503,
                        "headers": [
                            (b"content-type", b"application/json"),
                            (
                                b"content-length",
                                str(len(payload_bytes)).encode("latin-1"),
                            ),
                            (
                                b"retry-after",
                                str(self.config.retry_after_sec).encode("latin-1"),
                            ),
                        ],
                    }
                )
                await send(
                    {
                        "type": "http.response.body",
                        "body": payload_bytes,
                        "more_body": False,
                    }
                )
                return

        if (
            self.config.expose_status_route
            and method == "GET"
            and normalize_request_path(url) == "/status"
        ):
            payload_bytes = json.dumps({"status": "ok"}).encode("utf-8")
            await send(
                {
                    "type": "http.response.start",
                    "status": 200,
                    "headers": [
                        (b"content-type", b"application/json"),
                        (
                            b"content-length",
                            str(len(payload_bytes)).encode("latin-1"),
                        ),
                    ],
                }
            )
            await send(
                {
                    "type": "http.response.body",
                    "body": payload_bytes,
                    "more_body": False,
                }
            )
            return

        await self.app(scope, receive, send)


normalizeRequestPath = normalize_request_path
isPressureExemptPath = is_pressure_exempt_path
buildOverloadErrorResponse = build_overload_error_response
createPressureHandler = create_pressure_handler

__all__ = [
    "PRESSURE_EXEMPT_PATHS",
    "UnderPressureConfig",
    "UnderPressureMiddleware",
    "UnderPressureMonitor",
    "buildOverloadErrorResponse",
    "build_overload_error_response",
    "createPressureHandler",
    "create_pressure_handler",
    "isPressureExemptPath",
    "is_pressure_exempt_path",
    "normalizeRequestPath",
    "normalize_request_path",
    "parse_under_pressure_config",
]
