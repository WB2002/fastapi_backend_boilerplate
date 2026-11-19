from __future__ import annotations

import pytest

from app.common.config.parsers import ApiPriorityLevel
from app.core.overload.priority_bulkhead_guard import (
    compute_inbound_utilization,
    should_shed_priority_request,
)


def test_utilization_ratio() -> None:
    assert compute_inbound_utilization(5, 10) == 0.5
    assert compute_inbound_utilization(0, 10) == 0.0


@pytest.mark.parametrize(
    ("priority", "active", "expected"),
    [
        (ApiPriorityLevel.LOW, 6, False),
        (ApiPriorityLevel.LOW, 7, True),
        (ApiPriorityLevel.NORMAL, 8, False),
        (ApiPriorityLevel.NORMAL, 9, True),
        (ApiPriorityLevel.HIGH, 9, False),
        (ApiPriorityLevel.CRITICAL, 9, False),
    ],
)
def test_default_thresholds_at_max_ten(
    priority: ApiPriorityLevel, active: int, expected: bool
) -> None:
    assert should_shed_priority_request(active, 10, priority) is expected


def test_never_sheds_when_saturated_so_queue_or_reject_handles_it() -> None:
    assert should_shed_priority_request(10, 10, ApiPriorityLevel.LOW) is False


def test_mapping_form() -> None:
    assert (
        should_shed_priority_request(
            {"active": 7, "max_in_flight": 10, "priority": ApiPriorityLevel.LOW}
        )
        is True
    )
