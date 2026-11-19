from __future__ import annotations

import json
import uuid
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from opentelemetry import trace as otel_trace
from opentelemetry.sdk.trace import TracerProvider

from app.core.logger import (
    LoggerContext,
    LoggerMiddleware,
    TracingInterceptor,
    create_redaction_format,
    get_logger,
    trace,
)
from app.core.telemetry import skip_tracing


class TestCreateRedactionFormat:
    def test_redacts_sensitive_metadata_fields_on_log_info(self) -> None:
        transform = create_redaction_format({"enabled": True})
        info = {
            "level": "info",
            "message": "GET /callback?access_token=sekrit&ok=1",
            "email": "a@b.com",
            "requestId": "rid-1",
        }
        transformed = transform(info)
        assert transformed["email"] == "[REDACTED]"
        assert transformed["requestId"] == "rid-1"
        assert "sekrit" not in transformed["message"]
        assert "ok=1" in transformed["message"]

    def test_passes_through_when_disabled(self) -> None:
        transform = create_redaction_format({"enabled": False})
        info = {
            "level": "info",
            "message": "hello",
            "email": "a@b.com",
        }
        transformed = transform(info)
        assert transformed["email"] == "a@b.com"


class TestLoggerContextAndTraceDecorator:
    def test_trace_decorator_sync_and_async(self) -> None:
        class SampleService:
            @trace
            def compute_sync(self) -> dict[str, Any]:
                return dict(LoggerContext.get())

            @trace
            async def compute_async(self) -> dict[str, Any]:
                return dict(LoggerContext.get())

        svc = SampleService()
        with LoggerContext.scope(request_id="req-123", user_id="u-1"):
            sync_ctx = svc.compute_sync()
            assert sync_ctx["requestId"] == "req-123"
            assert sync_ctx["userId"] == "u-1"
            assert sync_ctx["context"] == "SampleService"
            assert sync_ctx["method"] == "compute_sync"
            assert "context" not in LoggerContext.get()

    async def test_trace_decorator_async_execution(self) -> None:
        class AsyncService:
            @trace
            async def run_job(self) -> dict[str, Any]:
                return dict(LoggerContext.get())

        svc = AsyncService()
        with LoggerContext.scope(request_id="req-async"):
            ctx = await svc.run_job()
            assert ctx["requestId"] == "req-async"
            assert ctx["context"] == "AsyncService"
            assert ctx["method"] == "run_job"


class TestTracingInterceptor:
    def test_sets_code_attributes_on_active_span(self) -> None:
        provider = TracerProvider()
        tracer = provider.get_tracer("test")
        with tracer.start_as_current_span("HTTP GET") as span:
            result_ctx = TracingInterceptor.intercept_sync(
                class_name="ExampleController",
                method_name="list_items",
                url="/example",
                handler=lambda: dict(LoggerContext.get()),
            )
            assert result_ctx["context"] == "ExampleController"
            assert result_ctx["method"] == "list_items"
            assert "traceId" in result_ctx
            assert "spanId" in result_ctx
            attrs = dict(getattr(span, "attributes", {}) or {})
            assert attrs.get("code.namespace") == "ExampleController"
            assert attrs.get("code.function") == "list_items"

    def test_skips_span_attributes_when_skip_tracing_or_exempt_url(self) -> None:
        @skip_tracing
        def health_handler() -> str:
            return "ok"

        provider = TracerProvider()
        tracer = provider.get_tracer("test")
        with tracer.start_as_current_span("HTTP GET") as span:
            TracingInterceptor.intercept_sync(
                class_name="HealthController",
                method_name="check",
                endpoint=health_handler,
                url="/health",
                handler=lambda: "ok",
            )
            attrs = dict(getattr(span, "attributes", {}) or {})
            assert "code.namespace" not in attrs


class TestLoggerMiddleware:
    async def test_extracts_valid_request_id_or_generates_uuid(
        self,
        capsys: pytest.CaptureFixture[str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("NODE_ENV", "production")
        app = FastAPI()
        app.add_middleware(LoggerMiddleware)

        @app.get("/test")
        async def _endpoint() -> dict[str, Any]:
            get_logger("TestEndpoint").log(
                "handling request",
                password="super-secret-password",
            )
            return dict(LoggerContext.get())

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://testserver",
        ) as client:
            res1 = await client.get(
                "/test?access_token=query-secret&ok=1",
                headers={"x-request-id": "  custom-req-id-99  "},
            )
            assert res1.status_code == 200
            assert res1.headers["x-request-id"] == "custom-req-id-99"
            body1 = res1.json()
            assert body1["requestId"] == "custom-req-id-99"
            assert body1["userId"] == "anonymous"

            res2 = await client.get(
                "/test",
                headers={"x-request-id": "   "},
            )
            assert res2.status_code == 200
            generated_id = res2.headers["x-request-id"]
            uuid.UUID(generated_id)
            assert res2.json()["requestId"] == generated_id

        captured = capsys.readouterr().out
        assert "super-secret-password" not in captured
        assert "query-secret" not in captured
        lines = [
            json.loads(line)
            for line in captured.splitlines()
            if line.strip().startswith("{")
        ]
        assert any(
            entry.get("requestId") == "custom-req-id-99"
            and entry.get("password") == "[REDACTED]"
            for entry in lines
        )

    async def test_omits_trace_id_on_exempt_health_paths(self) -> None:
        provider = TracerProvider()
        otel_trace.set_tracer_provider(provider)
        tracer = provider.get_tracer("test")

        app = FastAPI()
        app.add_middleware(LoggerMiddleware, log_completion=False)

        @app.get("/health")
        async def _health() -> dict[str, Any]:
            return dict(LoggerContext.get())

        @app.get("/items")
        async def _items() -> dict[str, Any]:
            return dict(LoggerContext.get())

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://testserver",
        ) as client:
            with tracer.start_as_current_span("root"):
                health_res = await client.get("/health")
                assert "traceId" not in health_res.json()

                items_res = await client.get("/items")
                assert "traceId" in items_res.json()
                assert "spanId" in items_res.json()
