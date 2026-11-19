import base64
import hashlib
import hmac
import os
from typing import Literal, TypedDict

from app.common.constants import DEFAULT_GUEST_COOKIE_MAX_AGE


class CookieOptions(TypedDict, total=False):
    httponly: bool
    secure: bool
    samesite: Literal["lax", "strict", "none"]
    path: str
    max_age: int


def _is_production(node_env: str | None) -> bool:
    resolved = node_env if node_env is not None else os.environ.get("NODE_ENV", "")
    return resolved == "production"


def get_access_token_cookie_options(
    ttl: int,
    *,
    node_env: str | None = None,
) -> CookieOptions:
    return {
        "httponly": True,
        "secure": _is_production(node_env),
        "samesite": "strict",
        "path": "/",
        "max_age": ttl,
    }


def get_session_id_cookie_options(
    ttl: int,
    *,
    node_env: str | None = None,
) -> CookieOptions:
    return {
        "httponly": True,
        "secure": _is_production(node_env),
        "samesite": "strict",
        "path": "/auth/refresh",
        "max_age": ttl,
    }


def get_csrf_token_cookie_options(
    *,
    node_env: str | None = None,
) -> CookieOptions:
    return {
        "httponly": False,
        "secure": _is_production(node_env),
        "samesite": "strict",
        "path": "/",
    }


def get_guest_id_cookie_options(
    max_age: int = DEFAULT_GUEST_COOKIE_MAX_AGE,
    *,
    node_env: str | None = None,
) -> CookieOptions:
    return {
        "httponly": True,
        "secure": _is_production(node_env),
        "samesite": "lax",
        "path": "/",
        "max_age": max_age,
    }


def sign_cookie(value: str, secret: str) -> str:
    """Sign a cookie value matching cookie-signature@1.2.2 (<value>.<base64_no_pad>)."""
    if not secret:
        raise ValueError("Cookie secret must be non-empty")
    digest = hmac.new(
        secret.encode("utf-8"),
        value.encode("utf-8"),
        hashlib.sha256,
    ).digest()
    signature = base64.b64encode(digest).decode("ascii").rstrip("=")
    return f"{value}.{signature}"


def unsign_cookie(signed_value: str, secret: str) -> str | None:
    """Verify a cookie-signature@1.2.2 signed cookie using constant-time comparison."""
    if not signed_value or not secret or "." not in signed_value:
        return None
    value, _sep, _sig = signed_value.rpartition(".")
    expected = sign_cookie(value, secret)
    if hmac.compare_digest(expected.encode("utf-8"), signed_value.encode("utf-8")):
        return value
    return None
