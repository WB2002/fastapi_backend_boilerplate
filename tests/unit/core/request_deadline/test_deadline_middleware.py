from __future__ import annotations

import asyncio
import time
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from starlette.types import Message

from app.common.filters import (
    AllExceptionsFilter,
    AllExceptionsMiddleware,
    register_exception_handlers,
)
from app.core.request_deadline import (
    HTTP_STATUS_CLIENT_CLOSED_REQUEST,
    REQUEST_DEADLINE_HEADER,
    DeadlineContext,
    RequestAbortSignal,
    RequestCancelledError,
    RequestDeadlineExceededError,
    RequestDeadlineInternals,
    RequestDeadlineMiddleware,
    RequestDeadlineStore,
    get_deadline_at,
    get_remaining_budget_ms,
    get_request_signal,
    parse_positive_timeout_ms,
    parse_timeout_header,
    remaining_ms,
    request_timeout,
    resolve_outbound_deadline_headers,
    resolve_timeout_ms,
    skip_request_deadline,
    throw_if_aborted,
)


class TestParseAndResolveTimeouts:
    def test_parse_timeout_header(self) -> None:
        assert parse_timeout_header({"x-request-timeout-ms": "5000"}) == 5000
        assert parse_timeout_header({"X-Request-Timeout-Ms": ["1200"]}) == 1200
        assert parse_timeout_header(None) is None
        assert parse_timeout_header({}) is None
        assert parse_timeout_header({"x-request-timeout-ms": "abc"}) is None
        assert parse_timeout_header({"x-request-timeout-ms": "0"}) is None
        assert parse_timeout_header({"x-request-timeout-ms": "-1"}) is None

    def test_parse_positive_timeout_ms(self) -> None:
        assert parse_positive_timeout_ms("", 30_000) == 30_000
        assert parse_positive_timeout_ms("abc", 30_000) == 30_000
        assert parse_positive_timeout_ms(0, 30_000) == 30_000
        assert parse_positive_timeout_ms(-5, 30_000) == 30_000
        assert parse_positive_timeout_ms("5000", 30_000) == 5000
        assert parse_positive_timeout_ms(12_000, 30_000) == 12_000

    def test_resolve_timeout_ms_shortens_only_and_clamps_to_max(self) -> None:
        assert (
            resolve_timeout_ms(
                default_timeout_ms=30_000,
                max_timeout_ms=120_000,
            ).timeout_ms
            == 30_000
        )

        # Client header shortens route override
        assert (
            resolve_timeout_ms(
                header_ms=5_000,
                route_override_ms=10_000,
                default_timeout_ms=30_000,
                max_timeout_ms=120_000,
            ).timeout_ms
            == 5_000
        )

        # Client header cannot extend route override
        assert (
            resolve_timeout_ms(
                header_ms=120_000,
                route_override_ms=5_000,
                default_timeout_ms=30_000,
                max_timeout_ms=120_000,
            ).timeout_ms
            == 5_000
        )

        # Clamped to max_timeout_ms
        assert (
            resolve_timeout_ms(
                header_ms=999_999,
                default_timeout_ms=200_000,
                max_timeout_ms=120_000,
            ).timeout_ms
            == 120_000
        )

        # Skipped
        skipped_res = resolve_timeout_ms(
            header_ms=5_000,
            default_timeout_ms=30_000,
            max_timeout_ms=120_000,
            skipped=True,
        )
        assert skipped_res.skipped is True
        assert skipped_res.timeout_ms is None


class TestDeadlineContextHelpersAndOutboundHeaders:
    def test_helpers_outside_and_inside_context(self) -> None:
        assert get_request_signal() is None
        assert get_deadline_at() is None
        assert remaining_ms() is None
        throw_if_aborted()

        signal = RequestAbortSignal()
        started_at = 1_700_000_000_000
        deadline_at = started_at + 10_000
        store = RequestDeadlineStore(
            signal=signal,
            started_at=started_at,
            deadline_at=deadline_at,
            reason="timeout",
        )
        internals = RequestDeadlineInternals(
            clear_timer=lambda: None,
            reschedule_from_start=lambda _ms: None,
            set_reason=lambda _r: None,
        )

        def _inside() -> None:
            assert get_request_signal() is signal
            assert get_deadline_at() == deadline_at
            assert get_remaining_budget_ms(deadline_at - 4_000) == 4_000
            signal._trigger("timeout")
            with pytest.raises(RequestDeadlineExceededError):
                throw_if_aborted()
            store.reason = "client_disconnected"
            with pytest.raises(RequestCancelledError):
                throw_if_aborted()

        DeadlineContext.run(store, internals, _inside)

    def test_resolve_outbound_deadline_headers(self) -> None:
        assert resolve_outbound_deadline_headers(
            propagate=True,
            inbound_deadline_at=1_700_000_000_000,
        ) == {REQUEST_DEADLINE_HEADER: "1700000000000"}

        assert (
            resolve_outbound_deadline_headers(
                propagate=True,
                inbound_deadline_at=None,
            )
            == {}
        )
        assert (
            resolve_outbound_deadline_headers(
                propagate=True,
                inbound_deadline_at=100,
                existing_headers={"X-Request-Deadline": "200"},
            )
            == {}
        )
        assert (
            resolve_outbound_deadline_headers(
                propagate=False,
                inbound_deadline_at=100,
            )
            == {}
        )


class TestRequestDeadlineMiddleware:
    def _build_test_app(
        self,
        *,
        default_timeout_ms: int = 1_000,
        max_timeout_ms: int = 5_000,
    ) -> FastAPI:
        app = FastAPI()
        exc_filter = AllExceptionsFilter()
        register_exception_handlers(app, exc_filter)
        app.add_middleware(
            RequestDeadlineMiddleware,
            default_timeout_ms=default_timeout_ms,
            max_timeout_ms=max_timeout_ms,
        )
        app.add_middleware(
            AllExceptionsMiddleware,
            exception_filter=exc_filter,
        )

        @app.get("/fast")
        async def _fast() -> dict[str, Any]:
            return {
                "deadlineAt": get_deadline_at(),
                "remainingMs": remaining_ms(),
            }

        @app.get("/slow")
        @request_timeout(150)
        async def _slow() -> dict[str, str]:
            await asyncio.sleep(0.5)
            return {"status": "done"}

        @app.get("/skipped")
        @skip_request_deadline
        async def _skipped() -> dict[str, Any]:
            await asyncio.sleep(0.08)
            return {"deadlineAt": get_deadline_at()}

        return app

    async def test_sets_x_request_deadline_header_and_context(self) -> None:
        app = self._build_test_app(default_timeout_ms=2_000, max_timeout_ms=5_000)
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://testserver",
        ) as client:
            before_ms = int(time.time() * 1000)
            res = await client.get("/fast")
            after_ms = int(time.time() * 1000)

            assert res.status_code == 200
            header_deadline = int(res.headers[REQUEST_DEADLINE_HEADER])
            assert before_ms + 2_000 <= header_deadline <= after_ms + 2_000
            body = res.json()
            assert body["deadlineAt"] == header_deadline

    async def test_returns_504_when_route_or_header_deadline_exceeded(self) -> None:
        app = self._build_test_app(default_timeout_ms=2_000, max_timeout_ms=5_000)
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://testserver",
        ) as client:
            res = await client.get("/slow")
            assert res.status_code == 504
            body = res.json()
            assert body["statusCode"] == 504
            assert body["message"] == "Request deadline exceeded"
            assert body["path"] == "/slow"

            # Client header shortens /fast if endpoint sleeps
            res_short = await client.get(
                "/slow",
                headers={"x-request-timeout-ms": "20"},
            )
            assert res_short.status_code == 504

    async def test_skip_request_deadline_decorator_disables_timer(self) -> None:
        app = self._build_test_app(default_timeout_ms=20, max_timeout_ms=5_000)
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://testserver",
        ) as client:
            res = await client.get("/skipped")
            assert res.status_code == 200

    async def test_aborts_with_request_cancelled_error_499_on_client_disconnect(
        self,
    ) -> None:
        assert HTTP_STATUS_CLIENT_CLOSED_REQUEST == 499

        async def slow_asgi_app(scope: Any, receive: Any, send: Any) -> None:
            _ = (scope, receive, send)
            await asyncio.sleep(5.0)

        mw = RequestDeadlineMiddleware(
            slow_asgi_app,
            default_timeout_ms=5_000,
            max_timeout_ms=10_000,
        )

        disconnect_sent = False

        async def mock_receive() -> Message:
            nonlocal disconnect_sent
            if not disconnect_sent:
                disconnect_sent = True
                await asyncio.sleep(0.02)
                return {"type": "http.disconnect"}
            await asyncio.sleep(10)
            return {"type": "http.disconnect"}

        sent_messages: list[Message] = []

        async def mock_send(msg: Message) -> None:
            sent_messages.append(msg)

        scope = {
            "type": "http",
            "method": "GET",
            "path": "/disconnect-test",
            "headers": [],
        }

        with pytest.raises(RequestCancelledError):
            await mw(scope, mock_receive, mock_send)
