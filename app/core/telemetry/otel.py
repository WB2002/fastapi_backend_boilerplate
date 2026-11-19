from __future__ import annotations

import re
from collections.abc import Callable, Mapping, Sequence
from typing import Any, Final

from opentelemetry import context as otel_context
from opentelemetry import propagate, trace
from opentelemetry.context.context import Context
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.propagators.textmap import (
    CarrierT,
    Getter,
    Setter,
    TextMapPropagator,
    default_getter,
    default_setter,
)
from opentelemetry.sdk.extension.aws.trace import AwsXRayIdGenerator
from opentelemetry.sdk.resources import SERVICE_NAME, Resource
from opentelemetry.sdk.trace import Event, ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import (
    BatchSpanProcessor,
    SimpleSpanProcessor,
    SpanExporter,
    SpanExportResult,
)
from opentelemetry.trace import (
    NonRecordingSpan,
    SpanContext,
    TraceFlags,
)

from app.common.config.parsers import (
    TelemetryConfig,
    is_app_metrics_enabled,
    parse_log_redaction_config,
    parse_telemetry_config,
)
from app.core.redaction.redactor import redact_otel_attributes

SKIP_TRACING_KEY: Final[str] = "skipTracing"
TRACE_HEADER_KEY: Final[str] = "X-Amzn-Trace-Id"

PRESSURE_EXEMPT_PATHS: Final[tuple[str, ...]] = (
    "/health",
    "/health/ready",
)

TRACE_EXEMPT_PATHS: Final[tuple[str, ...]] = (
    "/health",
    "/health/ready",
    "/health/live",
    "/status",
)

_XRAY_ROOT_RE: Final[re.Pattern[str]] = re.compile(
    r"^1-([0-9a-fA-F]{8})-([0-9a-fA-F]{24})$"
)
_XRAY_PARENT_RE: Final[re.Pattern[str]] = re.compile(r"^[0-9a-fA-F]{16}$")


class AwsXRayPropagator(TextMapPropagator):
    """OpenTelemetry TextMapPropagator for AWS X-Ray (X-Amzn-Trace-Id)."""

    def extract(
        self,
        carrier: CarrierT,
        context: Context | None = None,
        getter: Getter[CarrierT] = default_getter,
    ) -> Context:
        if context is None:
            context = otel_context.get_current()

        header_list = getter.get(carrier, TRACE_HEADER_KEY) or getter.get(
            carrier, TRACE_HEADER_KEY.lower()
        )
        if not header_list:
            return context

        header_val = header_list[0]
        trace_id_int: int | None = None
        span_id_int: int | None = None
        sampled = False

        for kv in header_val.split(";"):
            part = kv.strip()
            if not part or "=" not in part:
                continue
            k, v = part.split("=", 1)
            k = k.strip()
            v = v.strip()
            if k == "Root":
                m = _XRAY_ROOT_RE.match(v)
                if m is not None:
                    trace_id_int = int(f"{m.group(1)}{m.group(2)}", 16)
            elif k == "Parent":
                if _XRAY_PARENT_RE.match(v) is not None:
                    span_id_int = int(v, 16)
            elif k == "Sampled":
                sampled = v == "1"

        if trace_id_int and span_id_int:
            span_context = SpanContext(
                trace_id=trace_id_int,
                span_id=span_id_int,
                is_remote=True,
                trace_flags=TraceFlags(
                    TraceFlags.SAMPLED if sampled else TraceFlags.DEFAULT
                ),
            )
            if span_context.is_valid:
                return trace.set_span_in_context(
                    NonRecordingSpan(span_context),
                    context,
                )
        return context

    def inject(
        self,
        carrier: CarrierT,
        context: Context | None = None,
        setter: Setter[CarrierT] = default_setter,
    ) -> None:
        span = trace.get_current_span(context)
        span_context = span.get_span_context()
        if not span_context.is_valid:
            return
        otel_trace_id = f"{span_context.trace_id:032x}"
        xray_root = f"1-{otel_trace_id[:8]}-{otel_trace_id[8:]}"
        parent_id = f"{span_context.span_id:016x}"
        sampled_flag = "1" if (span_context.trace_flags & TraceFlags.SAMPLED) else "0"
        header_val = f"Root={xray_root};Parent={parent_id};Sampled={sampled_flag}"
        setter.set(carrier, TRACE_HEADER_KEY, header_val)

    @property
    def fields(self) -> set[str]:
        return {TRACE_HEADER_KEY}


AWSXRayPropagator = AwsXRayPropagator
AWSXRayIdGenerator = AwsXRayIdGenerator

_propagator: AwsXRayPropagator | None = None


def normalize_request_path(url: str) -> str:
    """Strip query string and trailing slash (except root '/')."""
    raw = url.split("?", 1)[0]
    if len(raw) > 1 and raw.endswith("/"):
        return raw[:-1]
    return raw


def is_pressure_exempt_path(url: str) -> bool:
    """Return True if path is exempt from under-pressure synthetic 503 checks."""
    path = normalize_request_path(url)
    return path in PRESSURE_EXEMPT_PATHS


def is_trace_exempt_path(
    url: str,
    *,
    include_live_and_status: bool = True,
) -> bool:
    """Return True if path is an ALB/Terminus probe exempt from OTel spans."""
    path = normalize_request_path(url)
    if include_live_and_status:
        return path in TRACE_EXEMPT_PATHS
    return path in PRESSURE_EXEMPT_PATHS


def skip_tracing[F: Callable[..., Any] | type[Any]](target: F) -> F:
    """Decorator marking a route handler or controller class to skip OTel tracing."""
    setattr(target, SKIP_TRACING_KEY, True)
    return target


def should_skip_tracing(
    endpoint: object | None = None,
    url: str | None = None,
    controller_class: object | None = None,
) -> bool:
    """Determine if tracing should be skipped based on @skip_tracing or exempt path."""
    if endpoint is not None and bool(getattr(endpoint, SKIP_TRACING_KEY, False)):
        return True
    if controller_class is not None and bool(
        getattr(controller_class, SKIP_TRACING_KEY, False)
    ):
        return True
    if url is not None and is_trace_exempt_path(url):
        return True
    return False


def get_aws_xray_propagator() -> AwsXRayPropagator:
    """Return singleton AWSXRayPropagator for X-Amzn-Trace-Id header propagation."""
    global _propagator
    if _propagator is None:
        _propagator = AwsXRayPropagator()
    return _propagator


class _RedactedSpanProxy:
    """Thin proxy over ReadableSpan with redacted attributes and events."""

    def __init__(self, delegate: ReadableSpan) -> None:
        self._delegate = delegate
        raw_attrs = dict(delegate.attributes) if delegate.attributes else {}
        self._attributes: Mapping[str, Any] = redact_otel_attributes(raw_attrs)
        redacted_events: list[Event] = []
        for ev in delegate.events:
            if ev.attributes is None:
                redacted_events.append(ev)
            else:
                ev_attrs = redact_otel_attributes(dict(ev.attributes))
                redacted_events.append(
                    Event(
                        name=ev.name,
                        attributes=ev_attrs,
                        timestamp=ev.timestamp,
                    )
                )
        self._events: Sequence[Event] = tuple(redacted_events)

    @property
    def attributes(self) -> Mapping[str, Any]:
        return self._attributes

    @property
    def events(self) -> Sequence[Event]:
        return self._events

    def __getattr__(self, name: str) -> Any:
        return getattr(self._delegate, name)


class RedactingSpanExporter(SpanExporter):
    """Wraps a SpanExporter and redacts PII from span and event attributes."""

    def __init__(
        self,
        delegate: SpanExporter,
        *,
        enabled: bool = True,
    ) -> None:
        self._delegate = delegate
        self._enabled = enabled

    @property
    def delegate(self) -> SpanExporter:
        return self._delegate

    @property
    def enabled(self) -> bool:
        return self._enabled

    def export(
        self,
        spans: Sequence[ReadableSpan],
    ) -> SpanExportResult:
        if not self._enabled:
            return self._delegate.export(spans)
        redacted_spans: list[ReadableSpan] = [
            _RedactedSpanProxy(span)  # type: ignore[misc]
            for span in spans
        ]
        return self._delegate.export(redacted_spans)

    def shutdown(self) -> None:
        self._delegate.shutdown()

    def force_flush(self, timeout_millis: int = 30_000) -> bool:
        force_flush_fn = getattr(self._delegate, "force_flush", None)
        if callable(force_flush_fn):
            return bool(force_flush_fn(timeout_millis))
        return True


def create_redacting_span_exporter(
    delegate: SpanExporter,
    *,
    enabled: bool = True,
) -> RedactingSpanExporter:
    """Create a RedactingSpanExporter wrapping `delegate`."""
    return RedactingSpanExporter(delegate, enabled=enabled)


def build_tracer_provider(
    env: Mapping[str, str | None] | None = None,
    *,
    custom_exporter: SpanExporter | None = None,
    use_simple_processor: bool = False,
) -> TracerProvider | None:
    """Build and configure an OpenTelemetry TracerProvider with AWS X-Ray IDs."""
    config = parse_telemetry_config(env)
    if not config.otel_enabled:
        return None

    resource = Resource.create({SERVICE_NAME: config.service_name})
    provider = TracerProvider(
        resource=resource,
        id_generator=AwsXRayIdGenerator(),
    )

    redaction = parse_log_redaction_config(env or {})
    exporter: SpanExporter | None = None
    if custom_exporter is not None:
        exporter = create_redacting_span_exporter(
            custom_exporter,
            enabled=redaction.enabled,
        )
    elif config.otlp_endpoint:
        endpoint_url = f"{config.otlp_endpoint.rstrip('/')}/v1/traces"
        base_exporter = OTLPSpanExporter(endpoint=endpoint_url)
        exporter = create_redacting_span_exporter(
            base_exporter,
            enabled=redaction.enabled,
        )

    if exporter is not None:
        processor = (
            SimpleSpanProcessor(exporter)
            if use_simple_processor
            else BatchSpanProcessor(exporter)
        )
        provider.add_span_processor(processor)

    propagate.set_global_textmap(get_aws_xray_propagator())
    trace.set_tracer_provider(provider)
    return provider


__all__ = [
    "PRESSURE_EXEMPT_PATHS",
    "SKIP_TRACING_KEY",
    "TRACE_EXEMPT_PATHS",
    "TRACE_HEADER_KEY",
    "AWSXRayIdGenerator",
    "AWSXRayPropagator",
    "AwsXRayIdGenerator",
    "AwsXRayPropagator",
    "RedactingSpanExporter",
    "TelemetryConfig",
    "TextMapPropagator",
    "build_tracer_provider",
    "create_redacting_span_exporter",
    "get_aws_xray_propagator",
    "is_app_metrics_enabled",
    "is_pressure_exempt_path",
    "is_trace_exempt_path",
    "normalize_request_path",
    "parse_telemetry_config",
    "should_skip_tracing",
    "skip_tracing",
]
