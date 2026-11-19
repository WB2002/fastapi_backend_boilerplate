from __future__ import annotations

import urllib.parse
from typing import Final

from fastapi import HTTPException, status

ALLOWED_LOOPBACK_HOSTNAMES: Final[frozenset[str]] = frozenset(
    {"127.0.0.1", "localhost"}
)


def assert_loopback_target_url(raw_url: str) -> urllib.parse.ParseResult:
    try:
        parsed = urllib.parse.urlparse(raw_url)
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="targetUrl must be http://127.0.0.1 or http://localhost",
        ) from None

    is_http = parsed.scheme == "http"
    hostname = (parsed.hostname or "").lower()
    is_allowed_host = hostname in ALLOWED_LOOPBACK_HOSTNAMES
    has_no_credentials = parsed.username is None and parsed.password is None

    if not is_http or not is_allowed_host or not has_no_credentials:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="targetUrl must be http://127.0.0.1 or http://localhost",
        )

    return parsed
