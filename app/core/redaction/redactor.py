from __future__ import annotations

import datetime
import json
import re
from collections.abc import Mapping, Sequence
from typing import Any, Final
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from app.common.config.parsers import (
    LogRedactionConfig,
    parse_log_redaction_config,
)

REDACTED: Final[str] = "[REDACTED]"
REDACTED_DEPTH: Final[str] = "[REDACTED_DEPTH]"
MAX_DEPTH: Final[int] = 8

SENSITIVE_HEADER_NAMES: Final[frozenset[str]] = frozenset(
    {
        "authorization",
        "proxy-authorization",
        "x-api-key",
        "api-key",
        "cookie",
        "set-cookie",
    }
)

SENSITIVE_HEADER_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"(secret|token|password|api-?key|(?:^|[-_])key$|auth)",
    re.IGNORECASE,
)

SENSITIVE_QUERY_KEYS: Final[frozenset[str]] = frozenset(
    {
        "token",
        "access_token",
        "refresh_token",
        "password",
        "passwd",
        "secret",
        "client_secret",
        "api_key",
        "apikey",
        "key",
        "sig",
        "signature",
        "credential",
    }
)

SENSITIVE_QUERY_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"(secret|token|password|passwd|api-?key|(?:^|[-_])key$|auth|sig|signature|credential)",
    re.IGNORECASE,
)

SENSITIVE_LOG_KEYS: Final[frozenset[str]] = frozenset(
    {
        "password",
        "currentpassword",
        "newpassword",
        "confirmpassword",
        "token",
        "accesstoken",
        "refreshtoken",
        "sessionid",
        "csrftoken",
        "authorization",
        "cookie",
        "set-cookie",
        "setcookie",
        "email",
        "phone",
        "mobile",
        "phonenumber",
        "ssn",
        "socialsecuritynumber",
        "taxid",
        "creditcard",
        "cardnumber",
        "cvv",
        "secret",
        "clientsecret",
        "apikey",
        "xapikey",
        "privatekey",
    }
)

SENSITIVE_LOG_KEY_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"(secret|token|password|passwd|api-?key|(?:^|[-_])key$|auth|credential|ssn|cvv|tax[-_]?id|session[-_]?id|card[-_]?number|credit[-_]?card|private[-_]?key)",
    re.IGNORECASE,
)

EMAIL_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b",
    re.IGNORECASE,
)

JWT_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"^eyJ[A-Za-z0-9_-]+\.eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]*$"
)

BEARER_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"^Bearer\s+\S+$",
    re.IGNORECASE,
)

ABSOLUTE_URL_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"https?://[^ \t\r\n'\"]+",
    re.IGNORECASE,
)

RELATIVE_WITH_QUERY_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"/[^ \t\r\n'\"]*\?[^ \t\r\n'\"]+",
    re.IGNORECASE,
)

HEADER_JSON_KEYS: Final[frozenset[str]] = frozenset(
    {
        "http.request.headers",
        "http.response.headers",
    }
)

KNOWN_URL_KEYS: Final[frozenset[str]] = frozenset(
    {
        "http.url",
        "url.full",
        "http.target",
    }
)

type RedactedOtelAttributeValue = (
    str | int | float | bool | list[str | int | float | bool]
)


def _normalize_alnum(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", name.lower())


def is_sensitive_header_name(name: str) -> bool:
    """Return True if a HTTP header name is sensitive."""
    lower = name.lower()
    normalized = _normalize_alnum(lower)
    return (
        lower in SENSITIVE_HEADER_NAMES
        or normalized in SENSITIVE_HEADER_NAMES
        or SENSITIVE_HEADER_PATTERN.search(lower) is not None
    )


def is_sensitive_query_key(name: str) -> bool:
    """Return True if a URL query parameter key is sensitive."""
    lower = name.lower()
    normalized = _normalize_alnum(lower)
    return (
        lower in SENSITIVE_QUERY_KEYS
        or normalized in SENSITIVE_QUERY_KEYS
        or SENSITIVE_QUERY_PATTERN.search(lower) is not None
    )


def is_sensitive_log_key(name: str) -> bool:
    """Return True if a structured log field key is sensitive."""
    lower = name.lower()
    normalized = _normalize_alnum(lower)
    return (
        lower in SENSITIVE_LOG_KEYS
        or normalized in SENSITIVE_LOG_KEYS
        or SENSITIVE_LOG_KEY_PATTERN.search(lower) is not None
    )


def looks_like_sensitive_value(value: str) -> bool:
    """Return True if a string looks like an email address, JWT, or Bearer token."""
    trimmed = value.strip()
    if not trimmed:
        return False
    if EMAIL_PATTERN.search(trimmed) is not None:
        return True
    if JWT_PATTERN.match(trimmed) is not None:
        return True
    if BEARER_PATTERN.match(trimmed) is not None:
        return True
    return False


def redact_headers(
    headers: Mapping[str, str | Sequence[str] | Any] | None = None,
) -> dict[str, str]:
    """Redact sensitive HTTP headers while normalizing sequence values with ', '."""
    if not headers:
        return {}
    result: dict[str, str] = {}
    for key, value in headers.items():
        if value is None:
            continue
        if isinstance(value, Sequence) and not isinstance(
            value, (str, bytes, bytearray)
        ):
            normalized = ", ".join(str(item) for item in value)
        else:
            normalized = str(value)
        result[key] = REDACTED if is_sensitive_header_name(key) else normalized
    return result


def redact_query(
    query: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Redact sensitive query dictionary keys."""
    if not query:
        return {}
    return {
        key: REDACTED if is_sensitive_query_key(key) else value
        for key, value in query.items()
    }


def _strip_userinfo(netloc: str) -> str:
    if "@" not in netloc:
        return netloc
    return netloc.rsplit("@", 1)[1]


def redact_url(url: str) -> str:
    """Redact credentials (username:password) and sensitive query params from a URL."""
    if not url:
        return url
    try:
        is_abs = bool(re.match(r"^https?://", url, re.IGNORECASE))
        target = (
            url
            if is_abs
            else f"https://redact.invalid{url if url.startswith('/') else '/' + url}"
        )
        if (
            not is_abs
            and not url.startswith("/")
            and "://" not in url
            and "?" not in url
        ):
            return url
        parsed = urlsplit(target)
        if is_abs and not parsed.hostname:
            return url

        clean_netloc = _strip_userinfo(parsed.netloc)
        path = parsed.path or ("/" if is_abs else "")

        query_str = parsed.query
        if query_str:
            pairs = parse_qsl(query_str, keep_blank_values=True)
            redacted_pairs = [
                (k, REDACTED if is_sensitive_query_key(k) else v) for k, v in pairs
            ]
            new_query = urlencode(redacted_pairs, doseq=True)
        else:
            new_query = ""

        if is_abs:
            return urlunsplit(
                (parsed.scheme.lower(), clean_netloc, path, new_query, parsed.fragment)
            )
        rebuilt_path = parsed.path if url.startswith("/") else parsed.path.lstrip("/")
        suffix_query = (
            f"?{new_query}" if new_query or "?" in url.split("#", 1)[0] else ""
        )
        if not new_query and "?" not in url:
            suffix_query = ""
        suffix_frag = f"#{parsed.fragment}" if parsed.fragment or "#" in url else ""
        return f"{rebuilt_path}{suffix_query}{suffix_frag}"
    except Exception:
        return url


def redact_log_message(message: str) -> str:
    """Redact absolute URLs and relative URLs with query strings inside log messages."""
    out = ABSOLUTE_URL_PATTERN.sub(lambda m: redact_url(m.group(0)), message)
    out = RELATIVE_WITH_QUERY_PATTERN.sub(lambda m: redact_url(m.group(0)), out)
    return out


def _redact_string(value: str) -> str:
    if looks_like_sensitive_value(value):
        return REDACTED
    return value


def redact_for_log(
    value: Any,
    depth: int = 0,
    seen: set[int] | None = None,
    *,
    enabled: bool = True,
) -> Any:
    """Recursively redact sensitive keys/values with depth cap and cycle guard."""
    if not enabled:
        return value
    if depth > MAX_DEPTH:
        return REDACTED_DEPTH
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return _redact_string(value)
    if isinstance(
        value,
        (
            datetime.date,
            datetime.datetime,
            datetime.time,
            bytes,
            bytearray,
            memoryview,
        ),
    ):
        return value

    visited = set() if seen is None else seen

    if isinstance(value, (list, tuple, set, frozenset)):
        obj_id = id(value)
        if obj_id in visited:
            return REDACTED
        branch_seen = visited | {obj_id}
        return [
            redact_for_log(item, depth + 1, branch_seen, enabled=enabled)
            for item in value
        ]

    if isinstance(value, Mapping):
        obj_id = id(value)
        if obj_id in visited:
            return REDACTED
        branch_seen = visited | {obj_id}
        out: dict[str, Any] = {}
        for k, child in value.items():
            key_str = str(k)
            if is_sensitive_log_key(key_str):
                out[key_str] = REDACTED
            else:
                out[key_str] = redact_for_log(
                    child,
                    depth + 1,
                    branch_seen,
                    enabled=enabled,
                )
        return out

    return value


def _redact_otel_string_attribute(
    key: str,
    value: str,
) -> RedactedOtelAttributeValue:
    if key in HEADER_JSON_KEYS:
        try:
            parsed = json.loads(value)
            if isinstance(parsed, Mapping):
                return json.dumps(redact_headers(parsed))
            deep_val = redact_for_log(value)
            return deep_val if isinstance(deep_val, str) else str(deep_val)
        except Exception:
            deep_val = redact_for_log(value)
            return deep_val if isinstance(deep_val, str) else str(deep_val)

    if key in KNOWN_URL_KEYS:
        url_redacted = redact_url(value)
        if url_redacted != value:
            return url_redacted

    last_segment = key.rsplit(".", 1)[-1] if "." in key else key
    if is_sensitive_log_key(key) or is_sensitive_log_key(last_segment):
        return REDACTED

    deep = redact_for_log(value)
    return deep if isinstance(deep, str) else str(deep)


def _redact_otel_array_attribute(
    value: Sequence[Any],
) -> list[str | int | float | bool]:
    out: list[str | int | float | bool] = []
    for item in value:
        if isinstance(item, str):
            out.append(str(redact_for_log(item)))
        elif isinstance(item, (bool, int, float)):
            out.append(item)
        else:
            out.append(str(redact_for_log(item)))
    return out


def redact_otel_attribute_value(
    key: str,
    value: Any,
) -> RedactedOtelAttributeValue | None:
    """Redact a single OpenTelemetry span attribute value."""
    if value is None:
        return None
    if isinstance(value, str):
        return _redact_otel_string_attribute(key, value)
    if isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, (list, tuple)):
        return _redact_otel_array_attribute(value)
    return json.dumps(redact_for_log(value))


def redact_otel_attributes(
    attributes: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Redact a dictionary of OpenTelemetry span attributes."""
    if not attributes:
        return {}
    return {
        key: redact_otel_attribute_value(key, val) for key, val in attributes.items()
    }


__all__ = [
    "MAX_DEPTH",
    "REDACTED",
    "REDACTED_DEPTH",
    "LogRedactionConfig",
    "RedactedOtelAttributeValue",
    "is_sensitive_header_name",
    "is_sensitive_log_key",
    "is_sensitive_query_key",
    "looks_like_sensitive_value",
    "parse_log_redaction_config",
    "redact_for_log",
    "redact_headers",
    "redact_log_message",
    "redact_otel_attribute_value",
    "redact_otel_attributes",
    "redact_query",
    "redact_url",
]
