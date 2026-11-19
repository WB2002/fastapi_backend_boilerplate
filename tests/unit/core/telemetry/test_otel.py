from __future__ import annotations

import time
from collections.abc import Sequence

from opentelemetry.sdk.trace import ReadableSpan
from opentelemetry.sdk.trace.export import SpanExporter, SpanExportResult

from app.core.telemetry import (
    SKIP_TRACING_KEY,
    create_redacting_span_exporter,
    get_aws_xray_propagator,
    is_trace_exempt_path,
    should_skip_tracing,
    skip_tracing,
)
from app.instrumentation import (
    register_instrumentation,
    reset_instrumentation_for_tests,
    should_ignore_incoming_trace_for_url,
)


class _InMemoryExporter(SpanExporter):
    def __init__(self) -> None:
        self.exported: list[ReadableSpan] = []
        self.is_shutdown = False

    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        self.exported.extend(spans)
        return SpanExportResult.SUCCESS

    def shutdown(self) -> None:
        self.is_shutdown = True


class TestIsTraceExemptPathAndDecorators:
    def test_matches_alb_terminus_probe_paths(self) -> None:
        assert is_trace_exempt_path("/health") is True
        assert is_trace_exempt_path("/health/") is True
        assert is_trace_exempt_path("/health/ready") is True
        assert is_trace_exempt_path("/health/ready?verbose=1") is True
        assert is_trace_exempt_path("/health/live") is True
        assert is_trace_exempt_path("/status") is True
        assert should_ignore_incoming_trace_for_url("/health") is True
        assert should_ignore_incoming_trace_for_url("/health/ready") is True

    def test_does_not_exempt_api_routes(self) -> None:
        assert is_trace_exempt_path("/healthcare/plans") is False
        assert is_trace_exempt_path("/api/v1/users") is False
        assert should_ignore_incoming_trace_for_url("/healthcare") is False

    def test_skip_tracing_decorator_and_should_skip(self) -> None:
        @skip_tracing
        def handler() -> None:
            pass

        assert getattr(handler, SKIP_TRACING_KEY) is True
        assert should_skip_tracing(endpoint=handler) is True
        assert should_skip_tracing(url="/health/ready") is True
        assert should_skip_tracing(url="/api/items") is False


class TestOtelInstrumentationAndRedactingExporter:
    def teardown_method(self) -> None:
        reset_instrumentation_for_tests()

    def test_does_not_start_sdk_when_otel_enabled_is_false(self) -> None:
        provider = register_instrumentation({"OTEL_ENABLED": "false"})
        assert provider is None

    def test_aws_xray_id_generator_and_redacting_span_exporter(self) -> None:
        mem_exporter = _InMemoryExporter()
        provider = register_instrumentation(
            {
                "OTEL_ENABLED": "true",
                "OTEL_SERVICE_NAME": "fastapi-test-service",
                "LOG_REDACTION_ENABLED": "true",
            },
            custom_exporter=mem_exporter,
            use_simple_processor=True,
        )
        assert provider is not None

        now_epoch = int(time.time())
        tracer = provider.get_tracer("test")
        with tracer.start_as_current_span("test-operation") as span:
            span.set_attribute("password", "sekrit")
            span.set_attribute("http.method", "GET")
            span.set_attribute(
                "http.url",
                "https://api.example.com/v1?access_token=top-secret&ok=1",
            )
            span.add_event("auth.event", {"email": "user@example.com"})
            trace_id_hex = f"{span.get_span_context().trace_id:032x}"

            carrier: dict[str, str] = {}
            get_aws_xray_propagator().inject(carrier)
            assert "X-Amzn-Trace-Id" in carrier
            assert carrier["X-Amzn-Trace-Id"].startswith("Root=1-")

        # AWS X-Ray first 8 hex digits encode 32-bit epoch timestamp in seconds
        xray_epoch = int(trace_id_hex[:8], 16)
        assert abs(xray_epoch - now_epoch) <= 10

        assert len(mem_exporter.exported) == 1
        exported_span = mem_exporter.exported[0]
        attrs = exported_span.attributes
        assert attrs is not None
        assert attrs["password"] == "[REDACTED]"
        assert attrs["http.method"] == "GET"
        assert "top-secret" not in str(attrs["http.url"])
        ev_attrs = exported_span.events[0].attributes
        assert ev_attrs is not None
        assert ev_attrs["email"] == "[REDACTED]"

    def test_redacting_span_exporter_passes_through_when_disabled(self) -> None:
        mem_exporter = _InMemoryExporter()
        wrapper = create_redacting_span_exporter(mem_exporter, enabled=False)
        assert wrapper.enabled is False

        provider = register_instrumentation(
            {
                "OTEL_ENABLED": "true",
                "LOG_REDACTION_ENABLED": "false",
            },
            custom_exporter=mem_exporter,
            use_simple_processor=True,
        )
        assert provider is not None
        tracer = provider.get_tracer("test")
        with tracer.start_as_current_span("unredacted-span") as span:
            span.set_attribute("password", "sekrit")

        assert len(mem_exporter.exported) == 1
        attrs = mem_exporter.exported[0].attributes
        assert attrs is not None
        assert attrs["password"] == "sekrit"

    def test_aws_xray_propagator_singleton_and_extract(self) -> None:
        prop1 = get_aws_xray_propagator()
        prop2 = get_aws_xray_propagator()
        assert prop1 is prop2

        ctx = prop1.extract(
            {
                "X-Amzn-Trace-Id": (
                    "Root=1-5759e988-bd862e3fe1be46a994272793;"
                    "Parent=53995c3f42cd8ad8;Sampled=1"
                )
            }
        )
        assert ctx is not None
