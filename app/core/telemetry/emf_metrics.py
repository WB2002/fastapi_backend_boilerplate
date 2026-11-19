from __future__ import annotations

import json
import sys
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Final, Literal

from app.common.config.parsers import parse_telemetry_config

type ServiceUnavailableReason = Literal[
    "under_pressure",
    "inbound_bulkhead",
    "api_priority_shed",
    "graphql_concurrency",
    "health_check",
    "dependency_unavailable",
    "inbound_load_level_queue_full",
    "inbound_load_level_wait_timeout",
]

type FeatureFlagMetricName = Literal[
    "feature_flag_evaluation_total",
    "feature_flag_evaluation_errors",
    "feature_flag_update_total",
    "feature_flag_update_latency",
    "feature_flag_stale_age",
    "feature_flag_redis_errors",
    "feature_flag_pubsub_disconnects",
    "feature_flag_reconciliation_total",
]

VALID_SERVICE_UNAVAILABLE_REASONS: Final[frozenset[str]] = frozenset(
    {
        "under_pressure",
        "inbound_bulkhead",
        "api_priority_shed",
        "graphql_concurrency",
        "health_check",
        "dependency_unavailable",
        "inbound_load_level_queue_full",
        "inbound_load_level_wait_timeout",
    }
)


class EmfUnit(StrEnum):
    SECONDS = "Seconds"
    MICROSECONDS = "Microseconds"
    MILLISECONDS = "Milliseconds"
    BYTES = "Bytes"
    KILOBYTES = "Kilobytes"
    MEGABYTES = "Megabytes"
    GIGABYTES = "Gigabytes"
    TERABYTES = "Terabytes"
    BITS = "Bits"
    KILOBITS = "Kilobits"
    MEGABITS = "Megabits"
    GIGABITS = "Gigabits"
    TERABITS = "Terabits"
    PERCENT = "Percent"
    COUNT = "Count"
    NONE = "None"


@dataclass(frozen=True, slots=True)
class EmfMetricDefinition:
    name: str
    value: int | float
    unit: EmfUnit | str = EmfUnit.COUNT


@dataclass(frozen=True, slots=True)
class EmitServiceUnavailableOptions:
    enabled: bool
    reason: ServiceUnavailableReason
    path: str | None = None
    pressure_type: str | None = None


@dataclass(frozen=True, slots=True)
class ClientIdentity:
    service_name: str
    kind: Literal["internal", "external"] = "external"


def build_emf_payload(
    *,
    namespace: str,
    dimensions: Mapping[str, str],
    metrics: Sequence[EmfMetricDefinition | Mapping[str, Any]],
    properties: Mapping[str, str | int | float | bool] | None = None,
    timestamp_ms: int | None = None,
) -> dict[str, Any]:
    """Construct a CloudWatch Embedded Metric Format (_aws) dictionary."""
    ts = int(time.time() * 1000) if timestamp_ms is None else timestamp_ms
    dim_keys = list(dimensions.keys())
    metric_specs: list[dict[str, str]] = []
    metric_values: dict[str, int | float] = {}

    for m in metrics:
        if isinstance(m, EmfMetricDefinition):
            name = m.name
            value = m.value
            unit = str(m.unit.value if isinstance(m.unit, EmfUnit) else m.unit)
        else:
            name = str(m["name"])
            value = m["value"]
            raw_unit = m.get("unit", EmfUnit.COUNT)
            unit = str(raw_unit.value if isinstance(raw_unit, EmfUnit) else raw_unit)
        metric_specs.append({"Name": name, "Unit": unit})
        metric_values[name] = value

    payload: dict[str, Any] = {
        "_aws": {
            "Timestamp": ts,
            "CloudWatchMetrics": [
                {
                    "Namespace": namespace,
                    "Dimensions": [dim_keys] if dim_keys else [[]],
                    "Metrics": metric_specs,
                }
            ],
        }
    }
    if properties:
        for prop_key, prop_val in properties.items():
            payload[prop_key] = prop_val
    for dim_key, dim_val in dimensions.items():
        payload[dim_key] = dim_val
    for met_key, met_val in metric_values.items():
        payload[met_key] = met_val
    return payload


def flush_embedded_metrics_sync(
    *,
    namespace: str,
    dimensions: Mapping[str, str],
    metrics: Sequence[EmfMetricDefinition | Mapping[str, Any]],
    properties: Mapping[str, str | int | float | bool] | None = None,
    timestamp_ms: int | None = None,
) -> dict[str, Any]:
    """Build and write a single-line CloudWatch EMF JSON payload to stdout."""
    payload = build_emf_payload(
        namespace=namespace,
        dimensions=dimensions,
        metrics=metrics,
        properties=properties,
        timestamp_ms=timestamp_ms,
    )
    line = json.dumps(payload, separators=(",", ":"))
    sys.stdout.write(f"{line}\n")
    sys.stdout.flush()
    return payload


async def flush_embedded_metrics(
    *,
    namespace: str,
    dimensions: Mapping[str, str],
    metrics: Sequence[EmfMetricDefinition | Mapping[str, Any]],
    properties: Mapping[str, str | int | float | bool] | None = None,
    timestamp_ms: int | None = None,
) -> dict[str, Any]:
    """Async wrapper for flush_embedded_metrics_sync."""
    return flush_embedded_metrics_sync(
        namespace=namespace,
        dimensions=dimensions,
        metrics=metrics,
        properties=properties,
        timestamp_ms=timestamp_ms,
    )


def emit_service_unavailable_metric_sync(
    options: EmitServiceUnavailableOptions | Mapping[str, Any],
) -> dict[str, Any] | None:
    """Emit Application/ServiceUnavailable EMF metric synchronously."""
    if isinstance(options, EmitServiceUnavailableOptions):
        enabled = options.enabled
        reason = options.reason
        path = options.path
        pressure_type = options.pressure_type
    else:
        enabled = bool(options.get("enabled", True))
        reason = options["reason"]
        path = options.get("path")
        pressure_type = options.get("pressure_type") or options.get("pressureType")

    if not enabled:
        return None

    try:
        props: dict[str, str | int | float | bool] = {}
        if path:
            props["Path"] = path
        if pressure_type:
            props["PressureType"] = pressure_type
        return flush_embedded_metrics_sync(
            namespace="Application",
            dimensions={"Reason": reason},
            metrics=[
                EmfMetricDefinition(
                    name="ServiceUnavailable",
                    value=1,
                    unit=EmfUnit.COUNT,
                )
            ],
            properties=props,
        )
    except Exception:
        return None


async def emit_service_unavailable_metric(
    options: EmitServiceUnavailableOptions | Mapping[str, Any],
) -> dict[str, Any] | None:
    """Emit Application/ServiceUnavailable EMF metric asynchronously."""
    return emit_service_unavailable_metric_sync(options)


def _is_app_metrics_enabled() -> bool:
    return parse_telemetry_config().app_metrics_enabled


def record_under_pressure_503(*, path: str, pressure_type: str) -> None:
    emit_service_unavailable_metric_sync(
        EmitServiceUnavailableOptions(
            enabled=_is_app_metrics_enabled(),
            reason="under_pressure",
            path=path,
            pressure_type=pressure_type,
        )
    )


def record_inbound_bulkhead_503(path: str) -> None:
    emit_service_unavailable_metric_sync(
        EmitServiceUnavailableOptions(
            enabled=_is_app_metrics_enabled(),
            reason="inbound_bulkhead",
            path=path,
        )
    )


def record_api_priority_shed_503(path: str) -> None:
    emit_service_unavailable_metric_sync(
        EmitServiceUnavailableOptions(
            enabled=_is_app_metrics_enabled(),
            reason="api_priority_shed",
            path=path,
        )
    )


def record_graphql_concurrency_503(path: str = "/graphql") -> None:
    emit_service_unavailable_metric_sync(
        EmitServiceUnavailableOptions(
            enabled=_is_app_metrics_enabled(),
            reason="graphql_concurrency",
            path=path,
        )
    )


def record_health_check_503(path: str) -> None:
    emit_service_unavailable_metric_sync(
        EmitServiceUnavailableOptions(
            enabled=_is_app_metrics_enabled(),
            reason="health_check",
            path=path,
        )
    )


def record_dependency_unavailable_503(path: str) -> None:
    emit_service_unavailable_metric_sync(
        EmitServiceUnavailableOptions(
            enabled=_is_app_metrics_enabled(),
            reason="dependency_unavailable",
            path=path,
        )
    )


def record_inbound_load_level_queue_full_503(path: str) -> None:
    emit_service_unavailable_metric_sync(
        EmitServiceUnavailableOptions(
            enabled=_is_app_metrics_enabled(),
            reason="inbound_load_level_queue_full",
            path=path,
        )
    )


def record_inbound_load_level_wait_timeout_503(path: str) -> None:
    emit_service_unavailable_metric_sync(
        EmitServiceUnavailableOptions(
            enabled=_is_app_metrics_enabled(),
            reason="inbound_load_level_wait_timeout",
            path=path,
        )
    )


def emit_feature_flag_metrics_sync(
    *,
    enabled: bool,
    name: FeatureFlagMetricName,
    value: int | float = 1,
    unit: EmfUnit | str = EmfUnit.COUNT,
    dimensions: Mapping[str, str] | None = None,
) -> dict[str, Any] | None:
    """Emit FeatureFlags namespace EMF metrics (swallowing errors)."""
    if not enabled:
        return None
    try:
        return flush_embedded_metrics_sync(
            namespace="FeatureFlags",
            dimensions=dict(dimensions) if dimensions else {},
            metrics=[EmfMetricDefinition(name=name, value=value, unit=unit)],
        )
    except Exception:
        return None


async def emit_feature_flag_metrics(
    *,
    enabled: bool,
    name: FeatureFlagMetricName,
    value: int | float = 1,
    unit: EmfUnit | str = EmfUnit.COUNT,
    dimensions: Mapping[str, str] | None = None,
) -> dict[str, Any] | None:
    """Async wrapper for emit_feature_flag_metrics_sync."""
    return emit_feature_flag_metrics_sync(
        enabled=enabled,
        name=name,
        value=value,
        unit=unit,
        dimensions=dimensions,
    )


def emit_http_metrics_sync(
    *,
    enabled: bool,
    identity: ClientIdentity | Mapping[str, str],
    method: str,
    latency_ms: int | float,
    attempts: int,
    success: bool,
    status: int | None = None,
    error_code: str | None = None,
) -> dict[str, Any] | None:
    """Emit HttpClient namespace EMF metrics (swallowing errors)."""
    if not enabled:
        return None
    try:
        if isinstance(identity, ClientIdentity):
            service_name = identity.service_name
            kind_str: str = identity.kind
        else:
            service_name = str(
                identity.get("service_name") or identity.get("serviceName") or ""
            )
            kind_str = str(identity.get("kind", "external"))

        metrics: list[EmfMetricDefinition] = [
            EmfMetricDefinition("Latency", latency_ms, EmfUnit.MILLISECONDS),
            EmfMetricDefinition("Attempts", attempts, EmfUnit.COUNT),
            EmfMetricDefinition(
                "Success" if success else "Failure",
                1,
                EmfUnit.COUNT,
            ),
        ]
        if status is not None:
            metrics.append(EmfMetricDefinition("HttpStatus", status, EmfUnit.NONE))
        props: dict[str, str | int | float | bool] = {}
        if error_code:
            props["ErrorCode"] = error_code

        return flush_embedded_metrics_sync(
            namespace="HttpClient",
            dimensions={
                "Service": service_name,
                "Kind": kind_str,
                "Method": method,
            },
            metrics=metrics,
            properties=props,
        )
    except Exception:
        return None


async def emit_http_metrics(
    *,
    enabled: bool,
    identity: ClientIdentity | Mapping[str, str],
    method: str,
    latency_ms: int | float,
    attempts: int,
    success: bool,
    status: int | None = None,
    error_code: str | None = None,
) -> dict[str, Any] | None:
    """Async wrapper for emit_http_metrics_sync."""
    return emit_http_metrics_sync(
        enabled=enabled,
        identity=identity,
        method=method,
        latency_ms=latency_ms,
        attempts=attempts,
        success=success,
        status=status,
        error_code=error_code,
    )


def emit_country_access_metric_sync(
    event: str,
    properties: Mapping[str, str | int | float | bool] | None = None,
) -> dict[str, Any] | None:
    """Emit CountryAccess namespace EMF metrics (swallowing errors)."""
    try:
        return flush_embedded_metrics_sync(
            namespace="CountryAccess",
            dimensions={"Event": event},
            metrics=[EmfMetricDefinition(name=event, value=1, unit=EmfUnit.COUNT)],
            properties=dict(properties) if properties else {},
        )
    except Exception:
        return None


async def emit_country_access_metric(
    event: str,
    properties: Mapping[str, str | int | float | bool] | None = None,
) -> dict[str, Any] | None:
    """Async wrapper for emit_country_access_metric_sync."""
    return emit_country_access_metric_sync(event, properties)


class CountryAccessTelemetry:
    """CountryAccess EMF telemetry emitter matching NestJS CountryAccessTelemetry."""

    async def emit(
        self,
        event: str,
        properties: Mapping[str, str | int | float | bool] | None = None,
    ) -> dict[str, Any] | None:
        return emit_country_access_metric_sync(event, properties)

    async def access_denied(
        self,
        country_code: str | None,
        reason: str,
    ) -> dict[str, Any] | None:
        return await self.emit(
            "country_access_denied",
            {
                "countryCode": country_code if country_code is not None else "unknown",
                "reason": reason,
            },
        )

    async def invalid_ip(self) -> dict[str, Any] | None:
        return await self.emit("country_access_invalid_ip", {})

    async def unknown_country(self, reason: str) -> dict[str, Any] | None:
        return await self.emit("country_access_unknown_country", {"reason": reason})

    async def geo_ip_failure(self, reason: str) -> dict[str, Any] | None:
        return await self.emit("country_access_geoip_failure", {"reason": reason})

    async def inactive_bypass(self) -> dict[str, Any] | None:
        return await self.emit("country_access_inactive_bypass", {})

    async def refresh_success(
        self,
        *,
        provider: str,
        snapshot_version: int,
        allowed_count: int,
        last_successful_refresh_at: str,
    ) -> dict[str, Any] | None:
        return await self.emit(
            "country_access_refresh_success",
            {
                "provider": provider,
                "snapshotVersion": snapshot_version,
                "allowedCount": allowed_count,
                "lastSuccessfulRefreshAt": last_successful_refresh_at,
            },
        )

    async def refresh_failure(
        self,
        provider: str,
        reason: str,
    ) -> dict[str, Any] | None:
        return await self.emit(
            "country_access_refresh_failure",
            {
                "provider": provider,
                "reason": reason,
            },
        )

    # Sync and camelCase helpers
    def access_denied_sync(
        self, country_code: str | None, reason: str
    ) -> dict[str, Any] | None:
        return emit_country_access_metric_sync(
            "country_access_denied",
            {
                "countryCode": country_code if country_code is not None else "unknown",
                "reason": reason,
            },
        )

    def invalid_ip_sync(self) -> dict[str, Any] | None:
        return emit_country_access_metric_sync("country_access_invalid_ip", {})

    def unknown_country_sync(self, reason: str) -> dict[str, Any] | None:
        return emit_country_access_metric_sync(
            "country_access_unknown_country", {"reason": reason}
        )

    def geo_ip_failure_sync(self, reason: str) -> dict[str, Any] | None:
        return emit_country_access_metric_sync(
            "country_access_geoip_failure", {"reason": reason}
        )

    def inactive_bypass_sync(self) -> dict[str, Any] | None:
        return emit_country_access_metric_sync("country_access_inactive_bypass", {})

    def refresh_success_sync(
        self,
        *,
        provider: str,
        snapshot_version: int,
        allowed_count: int,
        last_successful_refresh_at: str,
    ) -> dict[str, Any] | None:
        return emit_country_access_metric_sync(
            "country_access_refresh_success",
            {
                "provider": provider,
                "snapshotVersion": snapshot_version,
                "allowedCount": allowed_count,
                "lastSuccessfulRefreshAt": last_successful_refresh_at,
            },
        )

    def refresh_failure_sync(self, provider: str, reason: str) -> dict[str, Any] | None:
        return emit_country_access_metric_sync(
            "country_access_refresh_failure",
            {
                "provider": provider,
                "reason": reason,
            },
        )

    accessDenied = access_denied
    invalidIp = invalid_ip
    unknownCountry = unknown_country
    geoIpFailure = geo_ip_failure
    inactiveBypass = inactive_bypass
    refreshSuccess = refresh_success
    refreshFailure = refresh_failure


__all__ = [
    "VALID_SERVICE_UNAVAILABLE_REASONS",
    "ClientIdentity",
    "CountryAccessTelemetry",
    "EmfMetricDefinition",
    "EmfUnit",
    "EmitServiceUnavailableOptions",
    "FeatureFlagMetricName",
    "ServiceUnavailableReason",
    "build_emf_payload",
    "emit_country_access_metric",
    "emit_country_access_metric_sync",
    "emit_feature_flag_metrics",
    "emit_feature_flag_metrics_sync",
    "emit_http_metrics",
    "emit_http_metrics_sync",
    "emit_service_unavailable_metric",
    "emit_service_unavailable_metric_sync",
    "flush_embedded_metrics",
    "flush_embedded_metrics_sync",
    "record_api_priority_shed_503",
    "record_dependency_unavailable_503",
    "record_graphql_concurrency_503",
    "record_health_check_503",
    "record_inbound_bulkhead_503",
    "record_inbound_load_level_queue_full_503",
    "record_inbound_load_level_wait_timeout_503",
    "record_under_pressure_503",
]
