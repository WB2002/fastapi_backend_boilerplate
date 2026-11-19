from __future__ import annotations

import os
from collections.abc import Mapping

from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SpanExporter

from app.core.telemetry.otel import (
    build_tracer_provider,
    is_trace_exempt_path,
)

_provider: TracerProvider | None = None


def should_ignore_incoming_trace_for_url(url: str) -> bool:
    """Used by HTTP instrumentation — no X-Ray segments for ALB/Terminus probes."""
    return is_trace_exempt_path(url)


def register_instrumentation(
    env: Mapping[str, str | None] | None = None,
    *,
    custom_exporter: SpanExporter | None = None,
    use_simple_processor: bool = False,
) -> TracerProvider | None:
    """Initialize OpenTelemetry AWS X-Ray tracing if OTEL_ENABLED=true."""
    global _provider
    target_env = os.environ if env is None else env
    if _provider is not None and env is None and custom_exporter is None:
        return _provider
    provider = build_tracer_provider(
        target_env,
        custom_exporter=custom_exporter,
        use_simple_processor=use_simple_processor,
    )
    if provider is not None:
        _provider = provider
    return provider


def reset_instrumentation_for_tests() -> None:
    """Reset cached TracerProvider singleton for unit tests."""
    global _provider
    if _provider is not None:
        _provider.shutdown()
        _provider = None


setup_instrumentation = register_instrumentation
init_instrumentation = register_instrumentation
configure_instrumentation = register_instrumentation

__all__ = [
    "configure_instrumentation",
    "init_instrumentation",
    "register_instrumentation",
    "reset_instrumentation_for_tests",
    "setup_instrumentation",
    "should_ignore_incoming_trace_for_url",
]
