from __future__ import annotations

import hmac
import secrets
from typing import Any, Final

from app.common.constants import CSRF_TOKEN_COOKIE
from app.common.utils.cookie_utils import get_csrf_token_cookie_options

TOKEN_BYTE_LENGTH: Final[int] = 32
CSRF_TOKEN_HEADER: Final[str] = "x-csrf-token"  # noqa: S105


def generate_csrf_token() -> str:
    """Generate a 32-byte random base64url unpadded token (43 chars)."""
    return secrets.token_urlsafe(TOKEN_BYTE_LENGTH)


def csrf_tokens_match(
    cookie_value: str | None,
    header_value: str | None,
) -> bool:
    """Compare cookie and header CSRF tokens in constant time."""
    if (
        not isinstance(cookie_value, str)
        or not isinstance(header_value, str)
        or not cookie_value
        or not header_value
    ):
        return False
    a = cookie_value.encode("utf-8")
    b = header_value.encode("utf-8")
    if len(a) != len(b):
        return False
    return hmac.compare_digest(a, b)


def set_csrf_cookie(
    response: Any,
    token: str,
    cookie_name: str = CSRF_TOKEN_COOKIE,
    *,
    is_production: bool | None = None,
    node_env: str | None = None,
) -> None:
    """Set the `csrf_token` cookie on a FastAPI/Starlette Response or reply object."""
    effective_env = (
        ("production" if is_production else "development")
        if is_production is not None
        else node_env
    )
    opts = get_csrf_token_cookie_options(node_env=effective_env)

    set_cookie_fn = getattr(response, "set_cookie", None)
    if callable(set_cookie_fn):
        set_cookie_fn(
            key=cookie_name,
            value=token,
            httponly=opts.get("httponly", False),
            secure=opts.get("secure", False),
            samesite=opts.get("samesite", "strict"),
            path=opts.get("path", "/"),
        )
        return

    fastify_set_cookie = getattr(response, "setCookie", None)
    if callable(fastify_set_cookie):
        fastify_set_cookie(
            cookie_name,
            token,
            {
                "httpOnly": opts.get("httponly", False),
                "secure": opts.get("secure", False),
                "sameSite": opts.get("samesite", "strict"),
                "path": opts.get("path", "/"),
            },
        )


class CsrfTokenService:
    """Service for minting and comparing double-submit CSRF tokens."""

    def generate_token(self) -> str:
        return generate_csrf_token()

    def generateToken(self) -> str:
        return self.generate_token()

    def tokens_match(
        self,
        cookie_value: str | None,
        header_value: str | None,
    ) -> bool:
        return csrf_tokens_match(cookie_value, header_value)

    def tokensMatch(
        self,
        cookie_value: str | None,
        header_value: str | None,
    ) -> bool:
        return self.tokens_match(cookie_value, header_value)


generateCsrfToken = generate_csrf_token
csrfTokensMatch = csrf_tokens_match
setCsrfCookie = set_csrf_cookie

__all__ = [
    "CSRF_TOKEN_COOKIE",
    "CSRF_TOKEN_HEADER",
    "TOKEN_BYTE_LENGTH",
    "CsrfTokenService",
    "csrfTokensMatch",
    "csrf_tokens_match",
    "generateCsrfToken",
    "generate_csrf_token",
    "setCsrfCookie",
    "set_csrf_cookie",
]
