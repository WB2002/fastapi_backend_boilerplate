import math
import os
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Final

from app.common.config.parsers import parse_js_number, trim_js

DEFAULT_HTTP_KEEP_ALIVE_TIMEOUT_MS: Final[int] = 65_000
HEADERS_TIMEOUT_OFFSET_MS: Final[int] = 1_000


@dataclass(frozen=True, slots=True)
class HttpServerTimeouts:
    keep_alive_timeout_ms: int
    headers_timeout_ms: int


def _parse_positive_int(value: str | None, fallback: int) -> int:
    if value is None or trim_js(value) == "":
        return fallback
    parsed = parse_js_number(value)
    if not math.isfinite(parsed) or not parsed.is_integer() or parsed <= 0:
        return fallback
    return int(parsed)


def parse_http_server_timeouts(
    env: Mapping[str, str | None] | None = None,
) -> HttpServerTimeouts:
    target = os.environ if env is None else env
    keep_alive_timeout_ms = _parse_positive_int(
        target.get("HTTP_KEEP_ALIVE_TIMEOUT_MS"),
        DEFAULT_HTTP_KEEP_ALIVE_TIMEOUT_MS,
    )
    headers_timeout_ms = keep_alive_timeout_ms + HEADERS_TIMEOUT_OFFSET_MS
    return HttpServerTimeouts(
        keep_alive_timeout_ms=keep_alive_timeout_ms,
        headers_timeout_ms=headers_timeout_ms,
    )
