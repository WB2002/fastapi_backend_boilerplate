from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from http.cookies import SimpleCookie
from typing import Any, Final

from fastapi import HTTPException
from starlette.types import ASGIApp, Receive, Scope, Send

from app.common.config.parsers import parse_csv_paths
from app.common.constants import (
    ACCESS_TOKEN_COOKIE,
    CSRF_TOKEN_COOKIE,
    SESSION_ID_COOKIE,
)
from app.common.utils.object_utils import read_cookie_field, read_string_record_field
from app.core.csrf.csrf_token_service import CSRF_TOKEN_HEADER, CsrfTokenService

CSRF_OPTIONS: Final[str] = "CSRF_OPTIONS"
DEFAULT_CSRF_EXEMPT_PATHS: Final[tuple[str, ...]] = ("/health", "/health/ready")
UNSAFE_METHODS: Final[frozenset[str]] = frozenset({"POST", "PUT", "PATCH", "DELETE"})

CSRF_FORBIDDEN_BODY: Final[dict[str, Any]] = {
    "statusCode": 403,
    "message": "CSRF token invalid or missing",
    "error": "Forbidden",
    "code": "CSRF_VALIDATION_FAILED",
}


class CsrfForbiddenError(HTTPException):
    """NestJS-compatible CsrfForbiddenError (403 CSRF_VALIDATION_FAILED)."""

    def __init__(self) -> None:
        body = dict(CSRF_FORBIDDEN_BODY)
        super().__init__(status_code=403, detail=body)
        self.response = body

    def get_status(self) -> int:
        return 403

    def getStatus(self) -> int:
        return 403

    def get_response(self) -> dict[str, Any]:
        return dict(self.response)

    def getResponse(self) -> dict[str, Any]:
        return self.get_response()


@dataclass(frozen=True, slots=True)
class CsrfModuleOptions:
    enabled: bool = True
    cookie_name: str = CSRF_TOKEN_COOKIE
    header_name: str = CSRF_TOKEN_HEADER
    exempt_paths: tuple[str, ...] = DEFAULT_CSRF_EXEMPT_PATHS


DEFAULT_CSRF_OPTIONS: Final[CsrfModuleOptions] = CsrfModuleOptions()


def normalize_request_path(url: str) -> str:
    """Strip query string and trailing slash (except root '/')."""
    raw = url.split("?", 1)[0]
    if len(raw) > 1 and raw.endswith("/"):
        return raw[:-1]
    return raw


def is_csrf_exempt_path(
    url: str,
    exempt_paths: Sequence[str],
) -> bool:
    """Return True if the normalized URL path is in `exempt_paths`."""
    path = normalize_request_path(url)
    return path in exempt_paths


def _header_value(
    headers: Mapping[str, Any] | None,
    name: str,
) -> str | None:
    if not headers:
        return None
    direct = read_string_record_field(headers, name)
    if direct is not None:
        return direct
    lower = name.lower()
    direct_lower = read_string_record_field(headers, lower)
    if direct_lower is not None:
        return direct_lower
    for k, v in headers.items():
        if str(k).lower() == lower and isinstance(v, str):
            return v
    return None


def has_bearer_auth(headers: Mapping[str, Any] | None) -> bool:
    auth = _header_value(headers, "authorization")
    return isinstance(auth, str) and auth.lower().startswith("bearer ")


def has_session_cookies(cookies: Mapping[str, str] | None) -> bool:
    if not cookies:
        return False
    return bool(
        read_cookie_field(cookies, ACCESS_TOKEN_COOKIE)
        or read_cookie_field(cookies, SESSION_ID_COOKIE)
    )


def should_enforce_csrf(
    *,
    method: str | None = None,
    headers: Mapping[str, Any] | None = None,
    cookies: Mapping[str, str] | None = None,
) -> bool:
    """Determine if a request requires double-submit CSRF validation."""
    resolved_method = (method or "GET").upper()
    if resolved_method not in UNSAFE_METHODS:
        return False
    if has_bearer_auth(headers):
        return False
    return has_session_cookies(cookies)


def _parse_cookies_from_header(cookie_header: str | None) -> dict[str, str]:
    if not cookie_header:
        return {}
    result: dict[str, str] = {}
    simple = SimpleCookie()
    try:
        simple.load(cookie_header)
        for k, morsel in simple.items():
            result[k] = morsel.value
    except Exception:  # noqa: S110
        pass
    # Fallback manual split for tokens/values SimpleCookie may skip
    for part in cookie_header.split(";"):
        segment = part.strip()
        if "=" in segment:
            k, v = segment.split("=", 1)
            k_clean = k.strip()
            v_clean = v.strip()
            if k_clean and k_clean not in result:
                result[k_clean] = v_clean
    return result


def _extract_scope_headers_and_cookies(
    scope: Scope,
) -> tuple[dict[str, str], dict[str, str]]:
    raw_headers: list[tuple[bytes, bytes]] = scope.get("headers") or []
    headers: dict[str, str] = {}
    cookie_parts: list[str] = []
    for k_bytes, v_bytes in raw_headers:
        k_str = k_bytes.decode("latin-1").lower()
        v_str = v_bytes.decode("latin-1")
        if k_str == "cookie":
            cookie_parts.append(v_str)
        if k_str not in headers:
            headers[k_str] = v_str
    cookies = (
        _parse_cookies_from_header("; ".join(cookie_parts)) if cookie_parts else {}
    )
    return headers, cookies


def resolve_csrf_options(
    options: CsrfModuleOptions | Mapping[str, Any] | None = None,
    *,
    env: Mapping[str, str | None] | None = None,
) -> CsrfModuleOptions:
    if isinstance(options, CsrfModuleOptions):
        return options
    if isinstance(options, Mapping):
        enabled = bool(options.get("enabled", DEFAULT_CSRF_OPTIONS.enabled))
        cookie_name = str(
            options.get("cookie_name")
            or options.get("cookieName")
            or DEFAULT_CSRF_OPTIONS.cookie_name
        )
        header_name = str(
            options.get("header_name")
            or options.get("headerName")
            or DEFAULT_CSRF_OPTIONS.header_name
        )
        raw_exempt = (
            options.get("exempt_paths")
            if "exempt_paths" in options
            else options.get("exemptPaths")
        )
        exempt_paths = (
            tuple(str(p) for p in raw_exempt)
            if isinstance(raw_exempt, (list, tuple, set, frozenset))
            else DEFAULT_CSRF_EXEMPT_PATHS
        )
        return CsrfModuleOptions(
            enabled=enabled,
            cookie_name=cookie_name,
            header_name=header_name,
            exempt_paths=exempt_paths,
        )
    if env is not None:
        enabled = env.get("CSRF_ENABLED", "true") != "false"
        exempt_list = parse_csv_paths(
            env.get("CSRF_EXEMPT_PATHS"),
            DEFAULT_CSRF_EXEMPT_PATHS,
        )
        return CsrfModuleOptions(
            enabled=enabled,
            cookie_name=CSRF_TOKEN_COOKIE,
            header_name=CSRF_TOKEN_HEADER,
            exempt_paths=tuple(exempt_list),
        )
    return DEFAULT_CSRF_OPTIONS


class CsrfMiddleware:
    """Pure ASGI Double-Submit Cookie CSRF middleware matching NestJS CsrfMiddleware."""

    def __init__(
        self,
        app: ASGIApp | None = None,
        *,
        csrf_token_service: CsrfTokenService | None = None,
        options: CsrfModuleOptions | Mapping[str, Any] | None = None,
        enabled: bool | None = None,
        cookie_name: str | None = None,
        header_name: str | None = None,
        exempt_paths: Sequence[str] | None = None,
    ) -> None:
        self.app = app
        self.csrf_token_service = csrf_token_service or CsrfTokenService()
        base_opts = resolve_csrf_options(options)
        self.options = CsrfModuleOptions(
            enabled=base_opts.enabled if enabled is None else enabled,
            cookie_name=base_opts.cookie_name if cookie_name is None else cookie_name,
            header_name=base_opts.header_name if header_name is None else header_name,
            exempt_paths=(
                base_opts.exempt_paths if exempt_paths is None else tuple(exempt_paths)
            ),
        )

    def check_request(
        self,
        *,
        method: str | None,
        url: str | None,
        headers: Mapping[str, Any] | None = None,
        cookies: Mapping[str, str] | None = None,
    ) -> dict[str, Any] | None:
        """Return 403 payload dict if CSRF check fails, or None if allowed."""
        if not self.options.enabled:
            return None

        resolved_url = url if isinstance(url, str) else ""
        if is_csrf_exempt_path(resolved_url, self.options.exempt_paths):
            return None

        resolved_cookies = dict(cookies) if cookies is not None else {}
        if not resolved_cookies and headers:
            cookie_hdr = _header_value(headers, "cookie")
            if cookie_hdr:
                resolved_cookies = _parse_cookies_from_header(cookie_hdr)

        if not should_enforce_csrf(
            method=method,
            headers=headers,
            cookies=resolved_cookies,
        ):
            return None

        cookie_token = resolved_cookies.get(self.options.cookie_name)
        header_token = _header_value(headers, self.options.header_name)

        if not self.csrf_token_service.tokens_match(cookie_token, header_token):
            return CsrfForbiddenError().get_response()
        return None

    def enforce_on_request(self, req: Any, reply: Any = None) -> dict[str, Any] | None:
        """NestJS Fastify `enforceOnRequest(req, reply)` compatibility method."""
        if isinstance(req, Mapping):
            method = req.get("method")
            url = req.get("url")
            headers = req.get("headers")
            cookies = req.get("cookies")
        else:
            method = getattr(req, "method", None)
            url = getattr(req, "url", None)
            headers = getattr(req, "headers", None)
            cookies = getattr(req, "cookies", None)

        error_body = self.check_request(
            method=method,
            url=url,
            headers=headers,
            cookies=cookies,
        )
        if error_body is not None and reply is not None:
            code_fn = getattr(reply, "code", None)
            if callable(code_fn):
                chained = code_fn(403)
                send_fn = getattr(chained, "send", None) or getattr(reply, "send", None)
                if callable(send_fn):
                    send_fn(error_body)
        return error_body

    def enforceOnRequest(self, req: Any, reply: Any = None) -> dict[str, Any] | None:
        return self.enforce_on_request(req, reply)

    async def __call__(
        self,
        scope: Scope,
        receive: Receive,
        send: Send,
    ) -> None:
        if scope["type"] != "http":
            if self.app is not None:
                await self.app(scope, receive, send)
            return

        method = str(scope.get("method") or "GET")
        path = str(scope.get("path") or "/")
        qs = scope.get("query_string")
        url = (
            f"{path}?{qs.decode('latin-1')}"
            if isinstance(qs, (bytes, bytearray)) and qs
            else path
        )
        headers, cookies = _extract_scope_headers_and_cookies(scope)

        error_body = self.check_request(
            method=method,
            url=url,
            headers=headers,
            cookies=cookies,
        )
        if error_body is not None:
            payload_bytes = json.dumps(error_body).encode("utf-8")
            await send(
                {
                    "type": "http.response.start",
                    "status": 403,
                    "headers": [
                        (b"content-type", b"application/json"),
                        (b"content-length", str(len(payload_bytes)).encode("latin-1")),
                    ],
                }
            )
            await send(
                {
                    "type": "http.response.body",
                    "body": payload_bytes,
                    "more_body": False,
                }
            )
            return

        if self.app is not None:
            await self.app(scope, receive, send)


normalizeRequestPath = normalize_request_path
isCsrfExemptPath = is_csrf_exempt_path
shouldEnforceCsrf = should_enforce_csrf
parseCsvPaths = parse_csv_paths

__all__ = [
    "CSRF_FORBIDDEN_BODY",
    "CSRF_OPTIONS",
    "DEFAULT_CSRF_EXEMPT_PATHS",
    "DEFAULT_CSRF_OPTIONS",
    "UNSAFE_METHODS",
    "CsrfForbiddenError",
    "CsrfMiddleware",
    "CsrfModuleOptions",
    "has_bearer_auth",
    "has_session_cookies",
    "isCsrfExemptPath",
    "is_csrf_exempt_path",
    "normalizeRequestPath",
    "normalize_request_path",
    "parseCsvPaths",
    "parse_csv_paths",
    "resolve_csrf_options",
    "shouldEnforceCsrf",
    "should_enforce_csrf",
]
