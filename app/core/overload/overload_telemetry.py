from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from app.core.telemetry.emf_metrics import (
    record_api_priority_shed_503 as _record_api_priority_shed_503,
)
from app.core.telemetry.emf_metrics import (
    record_dependency_unavailable_503 as _record_dependency_unavailable_503,
)
from app.core.telemetry.emf_metrics import (
    record_health_check_503 as _record_health_check_503,
)
from app.core.telemetry.emf_metrics import (
    record_inbound_bulkhead_503 as _record_inbound_bulkhead_503,
)
from app.core.telemetry.emf_metrics import (
    record_inbound_load_level_queue_full_503 as _record_inbound_load_level_queue_full_503,  # noqa: E501
)
from app.core.telemetry.emf_metrics import (
    record_inbound_load_level_wait_timeout_503 as _record_inbound_load_level_wait_timeout_503,  # noqa: E501
)
from app.core.telemetry.emf_metrics import (
    record_under_pressure_503 as _record_under_pressure_503,
)


def record_under_pressure_503(
    path_or_options: str | Mapping[str, Any] | None = None,
    pressure_type: str | None = None,
    *,
    path: str | None = None,
) -> None:
    """Emit `Application/ServiceUnavailable` EMF metric with Reason=`under_pressure`."""
    if isinstance(path_or_options, Mapping):
        resolved_path = str(path_or_options.get("path", "/"))
        resolved_type = str(
            path_or_options.get("pressure_type")
            or path_or_options.get("pressureType")
            or "eventLoopDelay"
        )
    else:
        resolved_path = str(path if path is not None else (path_or_options or "/"))
        resolved_type = str(pressure_type or "eventLoopDelay")
    _record_under_pressure_503(path=resolved_path, pressure_type=resolved_type)


def record_inbound_bulkhead_503(path: str) -> None:
    _record_inbound_bulkhead_503(path)


def record_api_priority_shed_503(path: str) -> None:
    _record_api_priority_shed_503(path)


def record_health_check_503(path: str) -> None:
    _record_health_check_503(path)


def record_dependency_unavailable_503(path: str) -> None:
    _record_dependency_unavailable_503(path)


def record_inbound_load_level_queue_full_503(path: str) -> None:
    _record_inbound_load_level_queue_full_503(path)


def record_inbound_load_level_wait_timeout_503(path: str) -> None:
    _record_inbound_load_level_wait_timeout_503(path)


recordUnderPressure503 = record_under_pressure_503
recordInboundBulkhead503 = record_inbound_bulkhead_503
recordApiPriorityShed503 = record_api_priority_shed_503
recordHealthCheck503 = record_health_check_503
recordDependencyUnavailable503 = record_dependency_unavailable_503
recordInboundLoadLevelQueueFull503 = record_inbound_load_level_queue_full_503
recordInboundLoadLevelWaitTimeout503 = record_inbound_load_level_wait_timeout_503

__all__ = [
    "recordApiPriorityShed503",
    "recordDependencyUnavailable503",
    "recordHealthCheck503",
    "recordInboundBulkhead503",
    "recordInboundLoadLevelQueueFull503",
    "recordInboundLoadLevelWaitTimeout503",
    "recordUnderPressure503",
    "record_api_priority_shed_503",
    "record_dependency_unavailable_503",
    "record_health_check_503",
    "record_inbound_bulkhead_503",
    "record_inbound_load_level_queue_full_503",
    "record_inbound_load_level_wait_timeout_503",
    "record_under_pressure_503",
]
