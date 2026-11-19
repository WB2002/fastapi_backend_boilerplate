from collections.abc import Mapping
from typing import Any


def without_none[T](mapping: Mapping[str, T | None]) -> dict[str, T]:
    """Return a new dict omitting keys whose values are None."""
    return {key: value for key, value in mapping.items() if value is not None}


def read_string_record_field(
    record: Mapping[str, Any] | None,
    field_name: str,
) -> str | None:
    """Safely read a string field from a mapping by exact key match."""
    if not record:
        return None
    for key, val in record.items():
        if key == field_name:
            return val if isinstance(val, str) else None
    return None


def read_cookie_field(
    cookies: Mapping[str, str | None] | None,
    cookie_name: str,
) -> str | None:
    """Safely read a cookie value from a cookies mapping by exact key match."""
    if not cookies:
        return None
    for key, val in cookies.items():
        if key == cookie_name:
            return val
    return None
