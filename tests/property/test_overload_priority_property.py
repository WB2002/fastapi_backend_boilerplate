from __future__ import annotations

import itertools

from hypothesis import given
from hypothesis import strategies as st

from app.common.config.parsers import ApiPriorityLevel
from app.core.overload.priority_bulkhead_guard import (
    compute_inbound_utilization,
    should_shed_priority_request,
)

_priorities = st.sampled_from(list(ApiPriorityLevel))


@given(
    max_in_flight=st.integers(min_value=1, max_value=500),
    active=st.integers(min_value=0, max_value=500),
)
def test_utilization_is_bounded_ratio(max_in_flight: int, active: int) -> None:
    value = compute_inbound_utilization(active, max_in_flight)
    assert value >= 0.0
    assert value == active / max_in_flight


@given(
    max_in_flight=st.integers(min_value=1, max_value=500),
    active=st.integers(min_value=0, max_value=500),
)
def test_lower_priority_sheds_whenever_higher_priority_sheds(
    max_in_flight: int, active: int
) -> None:
    outcomes = [
        should_shed_priority_request(active, max_in_flight, level)
        for level in sorted(ApiPriorityLevel)
    ]
    for higher, lower in itertools.pairwise(outcomes):
        assert not higher or lower


@given(max_in_flight=st.integers(min_value=1, max_value=500), level=_priorities)
def test_idle_never_sheds_and_saturated_never_sheds(
    max_in_flight: int, level: ApiPriorityLevel
) -> None:
    assert should_shed_priority_request(0, max_in_flight, level) is False
    assert should_shed_priority_request(max_in_flight, max_in_flight, level) is False
