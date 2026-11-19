from __future__ import annotations

import asyncio
import datetime
import ipaddress
import re
import socket
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, Final
from urllib.parse import SplitResult, urlsplit

import nh3
from fastapi import HTTPException, Request
from pydantic import BaseModel, model_validator

SKIP_SANITIZATION_KEY: Final[str] = "sanitization:skip"
SANITIZE_HTML_KEY: Final[str] = "sanitization:html"
SAFE_URL_KEY: Final[str] = "sanitization:safeUrl"
REJECT_SQL_INJECTION_KEY: Final[str] = "sanitization:rejectSqlInjection"
SKIP_SQL_INJECTION_KEY: Final[str] = "sanitization:skipSqlInjection"

DEFAULT_SKIP_KEYS: Final[frozenset[str]] = frozenset(
    {
        "password",
        "currentPassword",
        "newPassword",
        "confirmPassword",
        "token",
        "accessToken",
        "refreshToken",
        "authorization",
    }
)

METADATA_HOSTNAMES: Final[frozenset[str]] = frozenset(
    {
        "metadata.google.internal",
        "metadata.goog",
        "kubernetes.default",
        "kubernetes.default.svc",
    }
)

SQL_INJECTION_PATTERNS: Final[tuple[re.Pattern[str], ...]] = (
    re.compile(r"((\%3D)|(=))[^\n]*((\%27)|(\')|(\-\-)|(\%3B)|(;))", re.IGNORECASE),
    re.compile(
        r"\w*((\%27)|(\'))((\%6F)|o|(\%4F))((\%72)|r|(\%52))",
        re.IGNORECASE,
    ),
    re.compile(r"((\%27)|(\'))\s*or\s+", re.IGNORECASE),
    re.compile(r"((\%27)|(\'))union", re.IGNORECASE),
    re.compile(r"\bunion(\s+all)?\s+select\b", re.IGNORECASE),
    re.compile(
        r";\s*(drop|alter|create|truncate|insert|update|delete)\s+",
        re.IGNORECASE,
    ),
    re.compile(r"\/\*[\s\S]*?\*\/"),
    re.compile(r"\bor\b\s+\d+\s*=\s*\d+", re.IGNORECASE),
    re.compile(r"\band\b\s+\d+\s*=\s*\d+", re.IGNORECASE),
    re.compile(r"\bsleep\s*\(", re.IGNORECASE),
    re.compile(r"\bbenchmark\s*\(", re.IGNORECASE),
    re.compile(r"\binformation_schema\b", re.IGNORECASE),
    re.compile(r"\bpg_sleep\s*\(", re.IGNORECASE),
    re.compile(r"\bxp_cmdshell\b", re.IGNORECASE),
    re.compile(r"(\%27)|(\')\s*(\-\-|\#|\%23)", re.IGNORECASE),
    re.compile(r"\-\-\s*$"),
)


class BadRequestException(HTTPException):
    """NestJS-compatible 400 BadRequestException."""

    def __init__(self, message: str = "Bad Request") -> None:
        super().__init__(status_code=400, detail=message)
        self.message = message
        self.response = {"statusCode": 400, "message": message, "error": "Bad Request"}

    def get_status(self) -> int:
        return 400

    def getStatus(self) -> int:
        return 400

    def get_response(self) -> dict[str, Any]:
        return self.response

    def getResponse(self) -> dict[str, Any]:
        return self.response


class UnsafeUrlError(ValueError):
    """Raised when a URL fails SSRF / public HTTP(S) assertions."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message
        self.name = "UnsafeUrlError"


@dataclass(frozen=True, slots=True)
class ParsedUrl:
    """URL object mirroring JS URL properties (`hostname`, `protocol`, `href`)."""

    raw: str
    protocol: str
    hostname: str
    port: int | None
    pathname: str
    search: str
    username: str
    password: str

    @property
    def href(self) -> str:
        return self.raw


def _strip_trailing_dots(hostname: str) -> str:
    trimmed = hostname
    while trimmed.endswith("."):
        trimmed = trimmed[:-1]
    return trimmed


def _is_valid_ipv4(ip_str: str) -> bool:
    try:
        ipaddress.IPv4Address(ip_str)
        return True
    except ValueError:
        return False


def _is_valid_ipv6(ip_str: str) -> bool:
    try:
        ipaddress.IPv6Address(ip_str)
        return True
    except ValueError:
        return False


def _ip_version(ip_str: str) -> int:
    if _is_valid_ipv4(ip_str):
        return 4
    if _is_valid_ipv6(ip_str):
        return 6
    return 0


def _is_private_or_reserved_ipv4(ip_str: str) -> bool:
    parts = ip_str.split(".")
    if len(parts) != 4:
        return True
    nums: list[int] = []
    for p in parts:
        try:
            n = int(p, 10)
        except ValueError:
            return True
        if n < 0 or n > 255:
            return True
        nums.append(n)
    a, b = nums[0], nums[1]
    if a in (0, 10, 127):
        return True
    if a == 169 and b == 254:
        return True
    if a == 172 and 16 <= b <= 31:
        return True
    if a == 192 and b == 168:
        return True
    if a == 100 and 64 <= b <= 127:
        return True  # CGNAT
    return a >= 224  # multicast / reserved


def _extract_ipv4_mapped(ipv6: str) -> str | None:
    if not ipv6.startswith("::ffff:"):
        return None
    rest = ipv6[len("::ffff:") :]
    if _ip_version(rest) == 4:
        return rest
    hex_parts = rest.split(":")
    if len(hex_parts) != 2:
        return "0.0.0.0"  # noqa: S104
    try:
        hi = int(hex_parts[0], 16)
        lo = int(hex_parts[1], 16)
    except ValueError:
        return "0.0.0.0"  # noqa: S104
    return f"{(hi >> 8) & 0xFF}.{hi & 0xFF}.{(lo >> 8) & 0xFF}.{lo & 0xFF}"


def _extract_ipv4_compatible(ipv6: str) -> str | None:
    if not ipv6.startswith("::") or ipv6.startswith("::ffff:"):
        return None
    rest = ipv6[2:]
    if not rest or "::" in rest:
        return None
    if _ip_version(rest) == 4:
        return rest
    hex_parts = rest.split(":")
    if len(hex_parts) != 2:
        return None
    try:
        hi = int(hex_parts[0], 16)
        lo = int(hex_parts[1], 16)
    except ValueError:
        return None
    if hi == 0 and lo <= 1:
        return None
    return f"{(hi >> 8) & 0xFF}.{hi & 0xFF}.{(lo >> 8) & 0xFF}.{lo & 0xFF}"


def _extract_six_to_four_ipv4(ipv6: str) -> str | None:
    if not ipv6.startswith("2002:"):
        return None
    parts = ipv6.split(":")
    if len(parts) < 3:
        return None
    try:
        hi = int(parts[1], 16)
        lo = int(parts[2], 16)
    except ValueError:
        return None
    return f"{(hi >> 8) & 0xFF}.{hi & 0xFF}.{(lo >> 8) & 0xFF}.{lo & 0xFF}"


def _is_private_or_reserved_ipv6(ip_str: str) -> bool:
    normalized = ip_str.lower()
    if normalized in ("::1", "::"):
        return True

    if normalized.startswith("ff"):
        first_hextet = normalized.split(":", 1)[0]
        if re.match(r"^ff[0-9a-f]{0,2}$", first_hextet, re.IGNORECASE):
            return True

    if normalized.startswith(("fc", "fd", "fe8", "fe9", "fea", "feb")):
        return True

    mapped = _extract_ipv4_mapped(normalized)
    if mapped is not None:
        return _is_private_or_reserved_ipv4(mapped)

    compatible = _extract_ipv4_compatible(normalized)
    if compatible is not None:
        return _is_private_or_reserved_ipv4(compatible)

    six_to_four = _extract_six_to_four_ipv4(normalized)
    if six_to_four is not None:
        return _is_private_or_reserved_ipv4(six_to_four)

    return False


def is_private_or_reserved_address(hostname_or_ip: str) -> bool:
    """Return True if an IPv4 or IPv6 literal is private, loopback, or reserved."""
    normalized = _strip_trailing_dots(
        hostname_or_ip.lower().removeprefix("[").removesuffix("]")
    )
    version = _ip_version(normalized)
    if version == 4:
        return _is_private_or_reserved_ipv4(normalized)
    if version == 6:
        return _is_private_or_reserved_ipv6(normalized)
    return False


def _parse_and_assert_shape(raw: str) -> ParsedUrl:
    if not isinstance(raw, str) or not raw.strip() or any(c.isspace() for c in raw):
        raise UnsafeUrlError("Invalid URL")
    try:
        split: SplitResult = urlsplit(raw)
    except Exception as exc:
        raise UnsafeUrlError("Invalid URL") from exc

    if not split.scheme or not split.netloc:
        raise UnsafeUrlError("Invalid URL")

    protocol = f"{split.scheme.lower()}:"
    if protocol not in ("http:", "https:"):
        raise UnsafeUrlError("URL must use http or https")

    username = split.username or ""
    password = split.password or ""
    if username or password or "@" in split.netloc:
        raise UnsafeUrlError("URL must not contain credentials")

    try:
        port = split.port
    except ValueError as exc:
        raise UnsafeUrlError("Invalid URL") from exc

    raw_host = split.hostname
    if not raw_host:
        raise UnsafeUrlError("Invalid URL")

    # Preserve bracketed or raw IPv6 notation as WHATWG URL does
    if ":" in raw_host and not raw_host.startswith("["):
        netloc_host = split.netloc
        if netloc_host.startswith("[") and "]" in netloc_host:
            raw_host = netloc_host[1 : netloc_host.index("]")]

    return ParsedUrl(
        raw=raw,
        protocol=protocol,
        hostname=raw_host,
        port=port,
        pathname=split.path or "/",
        search=f"?{split.query}" if split.query else "",
        username=username,
        password=password,
    )


def _assert_hostname_not_private(raw_hostname: str) -> None:
    hostname = _strip_trailing_dots(
        raw_hostname.lower().removeprefix("[").removesuffix("]")
    )
    if (
        hostname == "localhost"
        or hostname.endswith(".localhost")
        or hostname in ("0.0.0.0", "::", "::1")  # noqa: S104
    ):
        raise UnsafeUrlError("URL hostname is not allowed")

    metadata_ip = ".".join(["169", "254", "169", "254"])
    if hostname in METADATA_HOSTNAMES or hostname == metadata_ip:
        raise UnsafeUrlError("URL hostname is not allowed")

    if is_private_or_reserved_address(hostname):
        raise UnsafeUrlError("URL resolves to a private or reserved address")


def assert_public_http_url(raw: str) -> ParsedUrl:
    """Synchronous SSRF check for user-controlled URLs (shape, scheme, IP literals)."""
    url = _parse_and_assert_shape(raw)
    _assert_hostname_not_private(url.hostname)
    return url


async def assert_public_http_url_async(
    raw: str,
    *,
    dns_lookup: Callable[[str], Any] | None = None,
) -> ParsedUrl:
    """Async SSRF check including DNS resolution to reject private A/AAAA records."""
    url = assert_public_http_url(raw)
    hostname = _strip_trailing_dots(
        url.hostname.lower().removeprefix("[").removesuffix("]")
    )
    if _ip_version(hostname) in (4, 6):
        return url

    records: list[str] = []
    try:
        if dns_lookup is not None:
            res = dns_lookup(hostname)
            if asyncio.iscoroutine(res):
                res = await res
            records = [str(r) for r in res]
        else:
            loop = asyncio.get_running_loop()
            addr_info = await loop.getaddrinfo(
                hostname,
                None,
                type=socket.SOCK_STREAM,
            )
            for item in addr_info:
                sockaddr = item[4]
                if sockaddr and isinstance(sockaddr[0], str):
                    records.append(sockaddr[0])
    except Exception as exc:
        raise UnsafeUrlError("URL hostname could not be resolved") from exc

    if not records:
        raise UnsafeUrlError("URL hostname could not be resolved")

    for address in records:
        if is_private_or_reserved_address(address):
            raise UnsafeUrlError("URL resolves to a private or reserved address")

    return url


def validate_safe_url(raw: str) -> str:
    """Validate that `raw` is a public HTTP(S) URL, raising BadRequestException(400)."""
    try:
        assert_public_http_url(raw)
    except UnsafeUrlError as exc:
        raise BadRequestException(exc.message) from exc
    return raw


def looks_like_sql_injection(value: str) -> bool:
    """Return True if `value` matches any of the 16 heuristic SQLi patterns."""
    if not value or len(value) < 2:
        return False
    return any(pattern.search(value) is not None for pattern in SQL_INJECTION_PATTERNS)


def sanitize_html_string(
    value: str,
    options: Mapping[str, Any] | None = None,
) -> str:
    """Strip HTML tags and script content using nh3 (matching sanitize-html)."""
    opts = options or {}
    raw_tags = opts.get("allowedTags") if "allowedTags" in opts else opts.get("tags")
    tags: set[str] = set(raw_tags) if raw_tags is not None else set()

    raw_attrs = (
        opts.get("allowedAttributes")
        if "allowedAttributes" in opts
        else opts.get("attributes")
    )
    attributes: dict[str, set[str]] = {}
    if isinstance(raw_attrs, Mapping):
        for k, v in raw_attrs.items():
            if isinstance(v, (list, set, tuple, frozenset)):
                attributes[str(k)] = {str(item) for item in v}

    clean_content_tags = {"script", "style"} - tags
    return nh3.clean(
        value,
        tags=tags,
        attributes=attributes,
        clean_content_tags=clean_content_tags,
        link_rel=None,
    )


@dataclass(slots=True)
class PropertyFlags:
    should_skip: bool = False
    html_options: dict[str, Any] | None = None
    is_safe_url: bool = False
    should_reject_sql_injection: bool | None = None
    should_skip_sql_injection: bool = False


@dataclass(slots=True)
class WalkSanitizationOptions:
    skip_keys: frozenset[str] | set[str] = field(
        default_factory=lambda: DEFAULT_SKIP_KEYS
    )
    html_options: dict[str, Any] | None = None
    reject_sql_injection: bool = True
    enforce_safe_url: bool = False
    metadata_target: Any = None
    dns_lookup: Callable[[str], Any] | None = None


def _get_target_class(target: Any) -> type[Any] | None:
    if target is None:
        return None
    if isinstance(target, type):
        return target
    cls = getattr(target, "__class__", None)
    if isinstance(cls, type) and cls not in (object, dict, list, str, int, bool, float):
        return cls
    return None


def _define_field_meta(
    target: Any,
    field_name: str,
    meta_key: str,
    meta_value: Any,
) -> None:
    cls = _get_target_class(target) or target
    existing: dict[tuple[str, str], Any] | None = getattr(
        cls,
        "__sanitization_meta__",
        None,
    )
    if existing is None or "__sanitization_meta__" not in getattr(cls, "__dict__", {}):
        copied = dict(existing) if existing is not None else {}
        cls.__sanitization_meta__ = copied
        existing = copied
    existing[(field_name, meta_key)] = meta_value


def _get_field_meta(
    target: Any,
    field_name: str,
    meta_key: str,
) -> Any:
    cls = _get_target_class(target)
    target_obj = cls if cls is not None else target
    if target_obj is None:
        return None

    meta_dict: dict[tuple[str, str], Any] | None = getattr(
        target_obj,
        "__sanitization_meta__",
        None,
    )
    if isinstance(meta_dict, Mapping) and (field_name, meta_key) in meta_dict:
        return meta_dict[(field_name, meta_key)]

    # Also support Pydantic v2 Field(json_schema_extra={...})
    model_fields = getattr(target_obj, "model_fields", None)
    if isinstance(model_fields, Mapping) and field_name in model_fields:
        field_info = model_fields[field_name]
        extra = getattr(field_info, "json_schema_extra", None)
        if isinstance(extra, Mapping) and meta_key in extra:
            return extra[meta_key]

    return None


def skip_sanitization(*field_names: str) -> Callable[[Any], Any]:
    """Decorator marking fields or handlers to skip sanitization."""

    def _decorator(target: Any) -> Any:
        if not field_names:
            setattr(target, SKIP_SANITIZATION_KEY, True)
            return target
        for name in field_names:
            _define_field_meta(target, name, SKIP_SANITIZATION_KEY, True)
        return target

    return _decorator


def sanitize_html(
    options: Mapping[str, Any] | None = None,
    *,
    fields: tuple[str, ...] | list[str] = (),
) -> Callable[[Any], Any]:
    """Override HTML allowlist for rich text fields (still runs SQLi checks)."""
    resolved_opts = dict(options) if options is not None else {}

    def _decorator(target: Any) -> Any:
        if not fields:
            setattr(target, SANITIZE_HTML_KEY, resolved_opts)
            return target
        for name in fields:
            _define_field_meta(target, name, SANITIZE_HTML_KEY, resolved_opts)
        return target

    return _decorator


def safe_url(*field_names: str) -> Callable[[Any], Any]:
    """Assert public http(s) URL including DNS resolution for decorated fields."""

    def _decorator(target: Any) -> Any:
        if not field_names:
            setattr(target, SAFE_URL_KEY, True)
            return target
        for name in field_names:
            _define_field_meta(target, name, SAFE_URL_KEY, True)
        return target

    return _decorator


def reject_sql_injection(*field_names: str) -> Callable[[Any], Any]:
    """Force SQL-injection rejection on for decorated fields."""

    def _decorator(target: Any) -> Any:
        if not field_names:
            setattr(target, REJECT_SQL_INJECTION_KEY, True)
            return target
        for name in field_names:
            _define_field_meta(target, name, REJECT_SQL_INJECTION_KEY, True)
        return target

    return _decorator


def skip_sql_injection_check(*field_names: str) -> Callable[[Any], Any]:
    """Skip only SQLi heuristics on decorated fields; XSS sanitization still applies."""

    def _decorator(target: Any) -> Any:
        if not field_names:
            setattr(target, SKIP_SQL_INJECTION_KEY, True)
            return target
        for name in field_names:
            _define_field_meta(target, name, SKIP_SQL_INJECTION_KEY, True)
        return target

    return _decorator


def _read_property_flags(
    metadata_target: Any,
    current_key: str | None,
) -> PropertyFlags:
    if metadata_target is None or current_key is None:
        return PropertyFlags()

    should_skip = bool(
        _get_field_meta(metadata_target, current_key, SKIP_SANITIZATION_KEY)
    )
    html_opts = _get_field_meta(metadata_target, current_key, SANITIZE_HTML_KEY)
    is_safe = bool(_get_field_meta(metadata_target, current_key, SAFE_URL_KEY))
    reject_sql = _get_field_meta(
        metadata_target,
        current_key,
        REJECT_SQL_INJECTION_KEY,
    )
    skip_sql = bool(
        _get_field_meta(metadata_target, current_key, SKIP_SQL_INJECTION_KEY)
    )

    return PropertyFlags(
        should_skip=should_skip,
        html_options=dict(html_opts) if isinstance(html_opts, Mapping) else None,
        is_safe_url=is_safe,
        should_reject_sql_injection=(
            bool(reject_sql) if reject_sql is not None else None
        ),
        should_skip_sql_injection=skip_sql,
    )


def _resolve_nested_metadata_target(
    value: Any,
    options: WalkSanitizationOptions,
    current_key: str | None,
) -> WalkSanitizationOptions:
    val_cls = _get_target_class(value)
    if val_cls is not None:
        return WalkSanitizationOptions(
            skip_keys=options.skip_keys,
            html_options=options.html_options,
            reject_sql_injection=options.reject_sql_injection,
            enforce_safe_url=options.enforce_safe_url,
            metadata_target=val_cls,
            dns_lookup=options.dns_lookup,
        )

    target_cls = _get_target_class(options.metadata_target)
    if target_cls is None or not current_key:
        return options

    # Inspect type annotations or Pydantic model_fields for nested DTO type
    nested_type: Any = None
    model_fields = getattr(target_cls, "model_fields", None)
    if isinstance(model_fields, Mapping) and current_key in model_fields:
        nested_type = getattr(model_fields[current_key], "annotation", None)
    if nested_type is None:
        annotations = getattr(target_cls, "__annotations__", None)
        if isinstance(annotations, Mapping):
            nested_type = annotations.get(current_key)

    if isinstance(nested_type, type) and nested_type not in (
        object,
        dict,
        list,
        str,
        int,
        float,
        bool,
    ):
        return WalkSanitizationOptions(
            skip_keys=options.skip_keys,
            html_options=options.html_options,
            reject_sql_injection=options.reject_sql_injection,
            enforce_safe_url=options.enforce_safe_url,
            metadata_target=nested_type,
            dns_lookup=options.dns_lookup,
        )
    return options


async def _sanitize_string_value_async(
    value: str,
    options: WalkSanitizationOptions,
    current_key: str | None = None,
) -> str:
    flags = _read_property_flags(options.metadata_target, current_key)
    skip_keys = options.skip_keys

    if flags.should_skip or (current_key is not None and current_key in skip_keys):
        return value

    should_reject_sql = (
        flags.should_reject_sql_injection
        if flags.should_reject_sql_injection is not None
        else options.reject_sql_injection
    )
    if flags.should_skip_sql_injection:
        should_reject_sql = False

    if should_reject_sql and looks_like_sql_injection(value):
        raise BadRequestException("Potentially malicious input rejected")

    should_enforce_safe_url = flags.is_safe_url or options.enforce_safe_url
    if should_enforce_safe_url:
        try:
            await assert_public_http_url_async(
                value,
                dns_lookup=options.dns_lookup,
            )
        except UnsafeUrlError as exc:
            raise BadRequestException(exc.message) from exc
        return value

    html_options = (
        flags.html_options if flags.html_options is not None else options.html_options
    )
    return sanitize_html_string(value, html_options)


def _sanitize_string_value_sync(
    value: str,
    options: WalkSanitizationOptions,
    current_key: str | None = None,
) -> str:
    flags = _read_property_flags(options.metadata_target, current_key)
    skip_keys = options.skip_keys

    if flags.should_skip or (current_key is not None and current_key in skip_keys):
        return value

    should_reject_sql = (
        flags.should_reject_sql_injection
        if flags.should_reject_sql_injection is not None
        else options.reject_sql_injection
    )
    if flags.should_skip_sql_injection:
        should_reject_sql = False

    if should_reject_sql and looks_like_sql_injection(value):
        raise BadRequestException("Potentially malicious input rejected")

    should_enforce_safe_url = flags.is_safe_url or options.enforce_safe_url
    if should_enforce_safe_url:
        try:
            assert_public_http_url(value)
        except UnsafeUrlError as exc:
            raise BadRequestException(exc.message) from exc
        return value

    html_options = (
        flags.html_options if flags.html_options is not None else options.html_options
    )
    return sanitize_html_string(value, html_options)


def _coerce_options(
    options: WalkSanitizationOptions | Mapping[str, Any] | None,
) -> WalkSanitizationOptions:
    if isinstance(options, WalkSanitizationOptions):
        return options
    if isinstance(options, Mapping):
        raw_skip = options.get("skip_keys") or options.get("skipKeys")
        skip_keys = frozenset(raw_skip) if raw_skip is not None else DEFAULT_SKIP_KEYS
        raw_html = options.get("html_options") or options.get("htmlOptions")
        html_opts = dict(raw_html) if isinstance(raw_html, Mapping) else None
        raw_reject = (
            options["reject_sql_injection"]
            if "reject_sql_injection" in options
            else options.get("rejectSqlInjection", True)
        )
        raw_safe = (
            options["enforce_safe_url"]
            if "enforce_safe_url" in options
            else options.get("enforceSafeUrl", False)
        )
        raw_target = (
            options.get("metadata_target")
            if "metadata_target" in options
            else options.get("metadataTarget")
        )
        return WalkSanitizationOptions(
            skip_keys=skip_keys,
            html_options=html_opts,
            reject_sql_injection=bool(raw_reject),
            enforce_safe_url=bool(raw_safe),
            metadata_target=raw_target,
            dns_lookup=options.get("dns_lookup"),
        )
    return WalkSanitizationOptions()


async def walk_and_sanitize(
    value: Any,
    options: WalkSanitizationOptions | Mapping[str, Any] | None = None,
    current_key: str | None = None,
    seen: set[int] | None = None,
) -> Any:
    """Recursively sanitize/reject string values in plain objects, dicts, and lists."""
    if value is None:
        return None

    opts = _coerce_options(options)

    if isinstance(value, str):
        return await _sanitize_string_value_async(value, opts, current_key)

    if isinstance(
        value, (int, float, bool, bytes, bytearray, datetime.date, datetime.datetime)
    ):
        return value

    visited = seen if seen is not None else set()
    obj_id = id(value)

    if isinstance(value, list):
        if obj_id in visited:
            return value
        visited.add(obj_id)
        return list(
            await asyncio.gather(
                *(walk_and_sanitize(elem, opts, current_key, visited) for elem in value)
            )
        )

    if isinstance(value, tuple):
        if obj_id in visited:
            return value
        visited.add(obj_id)
        items = await asyncio.gather(
            *(walk_and_sanitize(elem, opts, current_key, visited) for elem in value)
        )
        return tuple(items)

    if isinstance(value, dict):
        if obj_id in visited:
            return value
        visited.add(obj_id)
        effective_opts = _resolve_nested_metadata_target(value, opts, current_key)
        keys = list(value.keys())
        sanitized_vals = await asyncio.gather(
            *(
                walk_and_sanitize(
                    value[k],
                    effective_opts,
                    str(k),
                    visited,
                )
                for k in keys
            )
        )
        return dict(zip(keys, sanitized_vals, strict=True))

    if hasattr(value, "__dict__"):
        if obj_id in visited:
            return value
        visited.add(obj_id)
        effective_opts = _resolve_nested_metadata_target(value, opts, current_key)
        attrs = dict(vars(value))
        keys = list(attrs.keys())
        sanitized_vals = await asyncio.gather(
            *(
                walk_and_sanitize(
                    attrs[k],
                    effective_opts,
                    str(k),
                    visited,
                )
                for k in keys
            )
        )
        for k, v in zip(keys, sanitized_vals, strict=True):
            setattr(value, k, v)
        return value

    return value


def sanitize_value(
    value: Any,
    options: WalkSanitizationOptions | Mapping[str, Any] | None = None,
    current_key: str | None = None,
    seen: set[int] | None = None,
) -> Any:
    """Synchronous recursive sanitization (for Pydantic pre-validation)."""
    if value is None:
        return None

    opts = _coerce_options(options)

    if isinstance(value, str):
        return _sanitize_string_value_sync(value, opts, current_key)

    if isinstance(
        value, (int, float, bool, bytes, bytearray, datetime.date, datetime.datetime)
    ):
        return value

    visited = seen if seen is not None else set()
    obj_id = id(value)

    if isinstance(value, list):
        if obj_id in visited:
            return value
        visited.add(obj_id)
        return [sanitize_value(elem, opts, current_key, visited) for elem in value]

    if isinstance(value, tuple):
        if obj_id in visited:
            return value
        visited.add(obj_id)
        return tuple(sanitize_value(elem, opts, current_key, visited) for elem in value)

    if isinstance(value, dict):
        if obj_id in visited:
            return value
        visited.add(obj_id)
        effective_opts = _resolve_nested_metadata_target(value, opts, current_key)
        return {
            k: sanitize_value(v, effective_opts, str(k), visited)
            for k, v in value.items()
        }

    if hasattr(value, "__dict__"):
        if obj_id in visited:
            return value
        visited.add(obj_id)
        effective_opts = _resolve_nested_metadata_target(value, opts, current_key)
        for k, v in list(vars(value).items()):
            setattr(
                value,
                k,
                sanitize_value(v, effective_opts, str(k), visited),
            )
        return value

    return value


@dataclass(frozen=True, slots=True)
class ArgumentMetadata:
    """NestJS ArgumentMetadata descriptor (`body` | `query` | `param` | `custom`)."""

    type: str = "body"
    metatype: Any = None
    data: str | None = None


class SanitizationPipe:
    """NestJS-compatible SanitizationPipe transforming `body`, `query`, `param`."""

    async def transform(
        self,
        value: Any,
        metadata: ArgumentMetadata | Mapping[str, Any] | None = None,
    ) -> Any:
        if value is None:
            return None

        meta_type = "body"
        metatype: Any = None
        if isinstance(metadata, ArgumentMetadata):
            meta_type = metadata.type
            metatype = metadata.metatype
        elif isinstance(metadata, Mapping):
            meta_type = str(metadata.get("type") or "body")
            metatype = metadata.get("metatype")

        if meta_type not in ("body", "query", "param"):
            return value

        metadata_target: Any = None
        if metatype is not None and metatype not in (
            object,
            dict,
            list,
            str,
            int,
            float,
            bool,
        ):
            metadata_target = metatype

        return await walk_and_sanitize(
            value,
            WalkSanitizationOptions(metadata_target=metadata_target),
        )

    async def __call__(self, request: Request) -> Request:
        """FastAPI dependency validating query and path params for SQLi / XSS."""
        if request.query_params:
            await self.transform(dict(request.query_params), {"type": "query"})
        if request.path_params:
            await self.transform(dict(request.path_params), {"type": "param"})
        return request


class SanitizedBaseModel(BaseModel):
    """Pydantic BaseModel running HTML/SQLi/SafeUrl sanitization before validation."""

    @model_validator(mode="before")
    @classmethod
    def _pre_validate_sanitize(cls, data: Any) -> Any:
        if bool(getattr(cls, SKIP_SANITIZATION_KEY, False)):
            return data
        if isinstance(data, Mapping):
            return sanitize_value(
                dict(data),
                WalkSanitizationOptions(metadata_target=cls),
            )
        return data


SkipSanitization = skip_sanitization
SanitizeHtml = sanitize_html
SafeUrl = safe_url
RejectSqlInjection = reject_sql_injection
SkipSqlInjectionCheck = skip_sql_injection_check
walkAndSanitize = walk_and_sanitize
sanitizeHtmlString = sanitize_html_string
looksLikeSqlInjection = looks_like_sql_injection
assertPublicHttpUrl = assert_public_http_url
assertPublicHttpUrlAsync = assert_public_http_url_async
isPrivateOrReservedAddress = is_private_or_reserved_address

__all__ = [
    "DEFAULT_SKIP_KEYS",
    "METADATA_HOSTNAMES",
    "REJECT_SQL_INJECTION_KEY",
    "SAFE_URL_KEY",
    "SANITIZE_HTML_KEY",
    "SKIP_SANITIZATION_KEY",
    "SKIP_SQL_INJECTION_KEY",
    "SQL_INJECTION_PATTERNS",
    "ArgumentMetadata",
    "BadRequestException",
    "ParsedUrl",
    "PropertyFlags",
    "RejectSqlInjection",
    "SafeUrl",
    "SanitizationPipe",
    "SanitizeHtml",
    "SanitizedBaseModel",
    "SkipSanitization",
    "SkipSqlInjectionCheck",
    "UnsafeUrlError",
    "WalkSanitizationOptions",
    "assertPublicHttpUrl",
    "assertPublicHttpUrlAsync",
    "assert_public_http_url",
    "assert_public_http_url_async",
    "isPrivateOrReservedAddress",
    "is_private_or_reserved_address",
    "looksLikeSqlInjection",
    "looks_like_sql_injection",
    "reject_sql_injection",
    "safe_url",
    "sanitizeHtmlString",
    "sanitize_html",
    "sanitize_html_string",
    "sanitize_value",
    "skip_sanitization",
    "skip_sql_injection_check",
    "validate_safe_url",
    "walkAndSanitize",
    "walk_and_sanitize",
]
