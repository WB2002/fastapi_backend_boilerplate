from __future__ import annotations

import urllib.parse
from collections.abc import Mapping
from typing import Any

from app.common.constants import ACCESS_TOKEN_COOKIE


def _read_cookie(cookie_header: str | None, name: str) -> str | None:
    if not cookie_header:
        return None
    for part in cookie_header.split(";"):
        trimmed = part.strip()
        eq = trimmed.find("=")
        if eq <= 0:
            continue
        key = trimmed[:eq]
        if key != name:
            continue
        val = trimmed[eq + 1 :]
        try:
            return urllib.parse.unquote(val)
        except Exception:
            return val
    return None


def extract_access_token(
    handshake: Mapping[str, Any],
    allow_query_token: bool = False,
) -> str | None:
    # 1. Check Authorization Bearer header
    headers = handshake.get("headers")
    auth_header: str | None = None
    cookie_header: str | None = None

    if isinstance(headers, dict):
        auth_header = headers.get("authorization") or headers.get("Authorization")
        cookie_header = headers.get("cookie") or headers.get("Cookie")
    else:
        # Check standard ASGI environ keys
        auth_header = handshake.get("HTTP_AUTHORIZATION")
        cookie_header = handshake.get("HTTP_COOKIE")

    if isinstance(auth_header, str) and auth_header.startswith("Bearer "):
        bearer = auth_header[len("Bearer ") :].strip()
        if bearer:
            return bearer

    # 2. Check cookie
    cookie_token = _read_cookie(cookie_header, ACCESS_TOKEN_COOKIE)
    if cookie_token:
        return cookie_token

    # 3. Check auth dictionary
    auth = handshake.get("auth")
    if isinstance(auth, dict):
        auth_token = auth.get("token")
        if isinstance(auth_token, str) and auth_token.strip():
            return auth_token.strip()

    # 4. Check query parameters if allowed
    if allow_query_token:
        query = handshake.get("query")
        if isinstance(query, dict):
            query_token = query.get("access_token")
            if isinstance(query_token, str) and query_token.strip():
                return query_token.strip()
        query_string = handshake.get("QUERY_STRING")
        if isinstance(query_string, str) and query_string.strip():
            parsed_qs = urllib.parse.parse_qs(query_string)
            token_list = parsed_qs.get("access_token")
            if token_list and token_list[0].strip():
                return token_list[0].strip()

    return None
