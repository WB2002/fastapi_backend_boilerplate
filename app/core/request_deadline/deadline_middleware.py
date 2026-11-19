from __future__ import annotations

import asyncio
import time

from starlette.routing import Match
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.request_deadline.deadline_context import (
    DEFAULT_REQUEST_TIMEOUT_MAX_MS,
    DEFAULT_REQUEST_TIMEOUT_MS,
    REQUEST_DEADLINE_HEADER,
    REQUEST_TIMEOUT_KEY,
    SKIP_REQUEST_DEADLINE_KEY,
    DeadlineContext,
    RequestAbortSignal,
    RequestCancelledError,
    RequestDeadlineAbortReason,
    RequestDeadlineExceededError,
    RequestDeadlineInternals,
    RequestDeadlineStore,
    parse_timeout_header,
    resolve_timeout_ms,
)


def _headers_from_scope(scope: Scope) -> dict[str, str]:
    raw_headers: list[tuple[bytes, bytes]] = scope.get("headers") or []
    result: dict[str, str] = {}
    for k, v in raw_headers:
        key_str = k.decode("latin-1").lower()
        if key_str not in result:
            result[key_str] = v.decode("latin-1")
    return result


def _find_route_endpoint(scope: Scope) -> object | None:
    endpoint: object | None = scope.get("endpoint")
    if endpoint is not None:
        return endpoint
    app = scope.get("app")
    router = getattr(app, "router", None)
    routes = getattr(router, "routes", None)
    if not routes:
        return None
    for route in routes:
        try:
            match, child_scope = route.matches(scope)
            if match == Match.FULL:
                found: object | None = child_scope.get("endpoint") or getattr(
                    route, "endpoint", None
                )
                return found
        except Exception:  # noqa: S112
            continue
    return None


def _resolve_route_metadata(scope: Scope) -> tuple[bool, int | None]:
    """Inspect route endpoint for @skip_request_deadline and @request_timeout."""
    endpoint = _find_route_endpoint(scope)
    if endpoint is None:
        return False, None

    skipped = bool(getattr(endpoint, SKIP_REQUEST_DEADLINE_KEY, False))
    raw_timeout = getattr(endpoint, REQUEST_TIMEOUT_KEY, None)
    route_timeout_ms = (
        int(raw_timeout)
        if isinstance(raw_timeout, (int, float))
        and not isinstance(raw_timeout, bool)
        and raw_timeout > 0
        else None
    )
    return skipped, route_timeout_ms


class _DeadlineExecution:
    """Encapsulates per-request timer, signal, and ASGI send/receive wrapping."""

    def __init__(
        self,
        *,
        app: ASGIApp,
        scope: Scope,
        receive: Receive,
        send: Send,
        store: RequestDeadlineStore,
        max_timeout_ms: int,
        arm_initial_timer: bool,
    ) -> None:
        self.app = app
        self.scope = scope
        self.receive = receive
        self.send = send
        self.store = store
        self.max_timeout_ms = max_timeout_ms
        self.loop = asyncio.get_running_loop()
        self.timer_handle: asyncio.TimerHandle | None = None
        self.has_request_finished = False
        self.response_started = False
        self.app_task: asyncio.Task[None] | None = None
        self.receive_queue: asyncio.Queue[Message] = asyncio.Queue()

        if arm_initial_timer:
            self.arm_timer_for_deadline()

    def clear_timer(self) -> None:
        if self.timer_handle is not None:
            self.timer_handle.cancel()
            self.timer_handle = None

    def clear_timer_and_reset_deadline(self) -> None:
        self.clear_timer()
        self.store.deadline_at = self.store.started_at + self.max_timeout_ms

    def abort(self, reason: RequestDeadlineAbortReason) -> None:
        if self.store.signal.aborted:
            return
        self.store.reason = reason
        self.clear_timer()
        self.store.signal._trigger(reason)
        if (
            self.app_task is not None
            and not self.app_task.done()
            and not self.has_request_finished
        ):
            self.app_task.cancel()

    def arm_timer_for_deadline(self) -> None:
        self.clear_timer()
        now_ms = int(time.time() * 1000)
        remaining = max(1, self.store.deadline_at - now_ms)
        self.timer_handle = self.loop.call_later(
            remaining / 1000.0,
            lambda: self.abort("timeout"),
        )

    def reschedule_from_start(self, timeout_ms_from_start: int) -> None:
        if self.store.signal.aborted or self.has_request_finished:
            return
        clamped = max(1, min(timeout_ms_from_start, self.max_timeout_ms))
        candidate = self.store.started_at + clamped
        self.store.deadline_at = min(self.store.deadline_at, candidate)
        self.arm_timer_for_deadline()

    def set_reason(self, reason: RequestDeadlineAbortReason) -> None:
        self.store.reason = reason

    def build_internals(self) -> RequestDeadlineInternals:
        return RequestDeadlineInternals(
            clear_timer=self.clear_timer_and_reset_deadline,
            reschedule_from_start=self.reschedule_from_start,
            set_reason=self.set_reason,
            abort=self.abort,
        )

    async def monitor_receive(self) -> None:
        try:
            while not self.has_request_finished:
                msg = await self.receive()
                if msg.get("type") == "http.disconnect":
                    self.scope["state"]["client_disconnected"] = True
                    if not self.has_request_finished and not self.response_started:
                        self.abort("client_disconnected")
                    await self.receive_queue.put(msg)
                    return
                await self.receive_queue.put(msg)
        except asyncio.CancelledError:
            return

    async def wrapped_receive(self) -> Message:
        return await self.receive_queue.get()

    async def wrapped_send(self, message: Message) -> None:
        if message["type"] == "http.response.start":
            self.response_started = True
            raw_headers: list[tuple[bytes, bytes]] = list(message.get("headers") or [])
            header_bytes = REQUEST_DEADLINE_HEADER.encode("latin-1")
            filtered = [(k, v) for k, v in raw_headers if k.lower() != header_bytes]
            filtered.append(
                (header_bytes, str(self.store.deadline_at).encode("latin-1"))
            )
            message = {**message, "headers": filtered}
        elif message["type"] == "http.response.body" and not message.get(
            "more_body", False
        ):
            self.has_request_finished = True
            self.clear_timer()
        await self.send(message)

    async def _run_downstream(self) -> None:
        await self.app(self.scope, self.wrapped_receive, self.wrapped_send)

    async def execute(self) -> None:
        token = DeadlineContext.set(self.store, self.build_internals())
        monitor_task = asyncio.create_task(self.monitor_receive())
        try:
            self.app_task = asyncio.create_task(self._run_downstream())
            await self.app_task
        except asyncio.CancelledError:
            if self.store.signal.aborted:
                if self.store.reason == "client_disconnected":
                    raise RequestCancelledError() from None
                raise RequestDeadlineExceededError() from None
            raise
        finally:
            self.has_request_finished = True
            self.clear_timer()
            if not monitor_task.done():
                monitor_task.cancel()
                try:
                    await monitor_task
                except (asyncio.CancelledError, Exception):  # noqa: S110
                    pass
            DeadlineContext.reset(token)


class RequestDeadlineMiddleware:
    """Pure ASGI middleware enforcing request deadlines and x-request-deadline."""

    def __init__(
        self,
        app: ASGIApp,
        *,
        default_timeout_ms: int = DEFAULT_REQUEST_TIMEOUT_MS,
        max_timeout_ms: int = DEFAULT_REQUEST_TIMEOUT_MAX_MS,
    ) -> None:
        self.app = app
        self.default_timeout_ms = default_timeout_ms
        self.max_timeout_ms = max(default_timeout_ms, max_timeout_ms)

    async def __call__(
        self,
        scope: Scope,
        receive: Receive,
        send: Send,
    ) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers_map = _headers_from_scope(scope)
        header_ms = parse_timeout_header(headers_map)
        skipped, route_override_ms = _resolve_route_metadata(scope)

        resolved = resolve_timeout_ms(
            header_ms=header_ms,
            route_override_ms=route_override_ms,
            default_timeout_ms=self.default_timeout_ms,
            max_timeout_ms=self.max_timeout_ms,
            skipped=skipped,
        )

        started_at = int(time.time() * 1000)
        initial_timeout_ms = (
            resolved.timeout_ms
            if resolved.timeout_ms is not None
            else self.max_timeout_ms
        )
        store = RequestDeadlineStore(
            signal=RequestAbortSignal(),
            started_at=started_at,
            deadline_at=started_at + initial_timeout_ms,
        )

        if "state" not in scope or not isinstance(scope.get("state"), dict):
            scope["state"] = {}
        scope["state"]["client_disconnected"] = False

        execution = _DeadlineExecution(
            app=self.app,
            scope=scope,
            receive=receive,
            send=send,
            store=store,
            max_timeout_ms=self.max_timeout_ms,
            arm_initial_timer=(
                not resolved.skipped and resolved.timeout_ms is not None
            ),
        )
        await execution.execute()


__all__ = ["RequestDeadlineMiddleware"]
