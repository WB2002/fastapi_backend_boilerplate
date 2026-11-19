"""GraphQL over HTTP POST helper for E2E tests."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import httpx

    from tests.e2e.helpers.auth import AuthSession


@dataclass(slots=True)
class GqlResponse:
    """Parsed GraphQL HTTP response with status, headers, data, and errors."""

    status_code: int
    headers: httpx.Headers
    raw: dict[str, Any]
    data: Any
    errors: list[dict[str, Any]]

    @property
    def ok(self) -> bool:
        """Return ``True`` if HTTP status is 200 and no GraphQL errors exist."""
        return self.status_code == 200 and not self.errors

    @property
    def first_error(self) -> dict[str, Any] | None:
        """Return the first GraphQL error dictionary if present."""
        return self.errors[0] if self.errors else None

    @property
    def first_error_code(self) -> str | None:
        """Return ``errors[0].extensions.code`` if present."""
        err = self.first_error
        if not err:
            return None
        ext = err.get("extensions")
        if isinstance(ext, dict):
            code = ext.get("code")
            return str(code) if code is not None else None
        return None

    @property
    def retry_after(self) -> str | None:
        """Return ``Retry-After`` response header if present."""
        val = self.headers.get("retry-after")
        return str(val) if val is not None else None

    @property
    def cache_control(self) -> str | None:
        """Return ``Cache-Control`` response header if present."""
        val = self.headers.get("cache-control")
        return str(val) if val is not None else None

    def assert_ok(self) -> Any:
        """Assert HTTP 200 with zero GraphQL errors and return ``data``."""
        assert self.status_code == 200, (
            f"Expected HTTP 200 from GraphQL, got {self.status_code}: {self.raw}"
        )
        assert not self.errors, f"Unexpected GraphQL errors: {self.errors}"
        return self.data


def _merge_cookie_header(
    headers: Mapping[str, str],
    cookies: Mapping[str, str],
) -> dict[str, str]:
    merged = dict(headers)
    if cookies:
        cookie_str = "; ".join(f"{k}={v}" for k, v in cookies.items())
        existing_key = next(
            (k for k in merged if k.lower() == "cookie"),
            None,
        )
        if existing_key is not None and merged[existing_key]:
            merged[existing_key] = f"{merged[existing_key]}; {cookie_str}"
        elif existing_key is not None:
            merged[existing_key] = cookie_str
        else:
            merged["Cookie"] = cookie_str
    return merged


async def gql_request(
    client: httpx.AsyncClient,
    query: str,
    variables: dict[str, Any] | None = None,
    headers: Mapping[str, str] | None = None,
    cookies: Mapping[str, str] | None = None,
    *,
    operation_name: str | None = None,
    session: AuthSession | None = None,
    endpoint: str = "/graphql",
) -> GqlResponse:
    """Execute a GraphQL POST request and return a :class:`GqlResponse`."""
    payload: dict[str, Any] = {"query": query}
    if variables is not None:
        payload["variables"] = variables
    if operation_name is not None:
        payload["operationName"] = operation_name

    merged_headers: dict[str, str] = {}
    merged_cookies: dict[str, str] = {}
    if session is not None:
        merged_headers.update(session.auth_headers())
        merged_cookies.update(session.auth_cookies())
    if headers is not None:
        merged_headers.update(headers)
    if cookies is not None:
        merged_cookies.update(cookies)

    final_headers = _merge_cookie_header(merged_headers, merged_cookies)
    resp = await client.post(
        endpoint,
        json=payload,
        headers=final_headers or None,
    )
    raw: dict[str, Any]
    try:
        parsed = resp.json()
        raw = parsed if isinstance(parsed, dict) else {"raw": parsed}
    except ValueError:
        raw = {}

    data = raw.get("data")
    raw_errors = raw.get("errors")
    errors = list(raw_errors) if isinstance(raw_errors, list) else []
    return GqlResponse(
        status_code=resp.status_code,
        headers=resp.headers,
        raw=raw,
        data=data,
        errors=errors,
    )


__all__ = [
    "GqlResponse",
    "gql_request",
]
