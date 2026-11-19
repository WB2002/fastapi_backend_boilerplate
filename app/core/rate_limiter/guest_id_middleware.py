from __future__ import annotations

import os
import re
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from http.cookies import SimpleCookie
from typing import Any, Final, Literal

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.common.constants import (
    DEFAULT_GUEST_COOKIE_MAX_AGE,
    DEFAULT_GUEST_COOKIE_NAME,
)
from app.common.utils.cookie_utils import sign_cookie, unsign_cookie
from app.common.utils.object_utils import read_cookie_field

GUEST_ID_UUID_V4_RE: Final[re.Pattern[str]] = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\Z",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class GuestCookieOptions:
    name: str = DEFAULT_GUEST_COOKIE_NAME
    max_age: int = DEFAULT_GUEST_COOKIE_MAX_AGE
    same_site: Literal["lax", "strict", "none"] = "lax"
    secure: bool = True
    http_only: bool = True
    path: str = "/"


def _parse_cookie_header(cookie_header: str) -> dict[str, str]:
    result: dict[str, str] = {}
    simple = SimpleCookie()
    try:
        simple.load(cookie_header)
        for k, morsel in simple.items():
            result[k] = morsel.value
    except Exception:  # noqa: S110
        pass
    for part in cookie_header.split(";"):
        segment = part.strip()
        if "=" in segment:
            k, v = segment.split("=", 1)
            k_clean = k.strip()
            v_clean = v.strip()
            if k_clean and k_clean not in result:
                result[k_clean] = v_clean
    return result


def _format_set_cookie_header(
    name: str,
    signed_val: str,
    options: GuestCookieOptions,
) -> bytes:
    parts = [
        f"{name}={signed_val}",
        f"Max-Age={options.max_age}",
        f"Path={options.path}",
    ]
    if options.http_only:
        parts.append("HttpOnly")
    if options.secure:
        parts.append("Secure")
    same_site_cap = options.same_site.capitalize()
    parts.append(f"SameSite={same_site_cap}")
    return "; ".join(parts).encode("latin-1")


def _resolve_guest_cookie_options(
    options: GuestCookieOptions | Mapping[str, Any] | None = None,
    *,
    node_env: str | None = None,
) -> GuestCookieOptions:
    default_secure = True if node_env is None else (node_env == "production")
    if isinstance(options, GuestCookieOptions):
        return options
    if isinstance(options, Mapping):
        guest_sub = options.get("guestCookie") or options.get("guest_cookie")
        src = guest_sub if isinstance(guest_sub, Mapping) else options
        name = str(src.get("name", DEFAULT_GUEST_COOKIE_NAME))
        max_age = int(
            src["max_age"]
            if "max_age" in src
            else src.get("maxAge", DEFAULT_GUEST_COOKIE_MAX_AGE)
        )
        same_site_raw = str(
            src["same_site"] if "same_site" in src else src.get("sameSite", "lax")
        ).lower()
        same_site: Literal["lax", "strict", "none"] = (
            "strict"
            if same_site_raw == "strict"
            else ("none" if same_site_raw == "none" else "lax")
        )
        secure = bool(src["secure"]) if "secure" in src else default_secure
        http_only = bool(
            src["http_only"] if "http_only" in src else src.get("httpOnly", True)
        )
        path = str(src.get("path", "/"))
        return GuestCookieOptions(
            name=name,
            max_age=max_age,
            same_site=same_site,
            secure=secure,
            http_only=http_only,
            path=path,
        )
    return GuestCookieOptions(secure=default_secure)


class GuestIdMiddleware:
    """Pure ASGI middleware minting HMAC-signed UUIDv4 `guest_id` cookies."""

    def __init__(
        self,
        app: ASGIApp | None = None,
        *,
        cookie_secret: str | None = None,
        options: GuestCookieOptions | Mapping[str, Any] | None = None,
        node_env: str | None = None,
    ) -> None:
        self.app = app
        self.cookie_secret = (
            cookie_secret
            if cookie_secret is not None
            else os.environ.get("COOKIE_SECRET", "default-cookie-secret-key-32-chars")
        )
        self.guest_cookie = _resolve_guest_cookie_options(
            options,
            node_env=node_env,
        )

    def read_valid_guest_id(
        self,
        req_or_cookies: Any,
        cookie_name: str | None = None,
    ) -> str | None:
        target_name = cookie_name or self.guest_cookie.name
        cookies: Mapping[str, Any] | None = None
        unsign_fn: Any = None

        if isinstance(req_or_cookies, Mapping):
            if "cookies" in req_or_cookies and isinstance(
                req_or_cookies["cookies"], Mapping
            ):
                cookies = req_or_cookies["cookies"]
            else:
                cookies = req_or_cookies
        else:
            cookies = getattr(req_or_cookies, "cookies", None)
            unsign_fn = getattr(req_or_cookies, "unsignCookie", None) or getattr(
                req_or_cookies, "unsign_cookie", None
            )

        raw = read_cookie_field(cookies, target_name)
        if not raw or not isinstance(raw, str):
            return None

        if callable(unsign_fn):
            res = unsign_fn(raw)
            if isinstance(res, Mapping):
                if not res.get("valid") or not res.get("value"):
                    return None
                value = str(res["value"])
            else:
                if not getattr(res, "valid", False) or not getattr(res, "value", None):
                    return None
                value = str(res.value)
        elif self.cookie_secret and "." in raw:
            unsigned = unsign_cookie(raw, self.cookie_secret)
            if unsigned is None:
                return None
            value = unsigned
        elif self.cookie_secret and "." not in raw:
            return None
        else:
            value = raw

        if GUEST_ID_UUID_V4_RE.match(value) is None:
            return None
        return value

    def readValidGuestId(
        self,
        req_or_cookies: Any,
        cookie_name: str | None = None,
    ) -> str | None:
        return self.read_valid_guest_id(req_or_cookies, cookie_name)

    def ensure_guest_id(self, req: Any, reply: Any = None) -> str:
        """Verify or mint guest_id and normalize req.cookies."""
        cookie_name = self.guest_cookie.name
        existing = self.read_valid_guest_id(req, cookie_name)
        if existing is not None:
            self._normalize_req_cookie(req, cookie_name, existing)
            return existing

        guest_id = str(uuid.uuid4())
        if reply is not None:
            set_cookie_fn = getattr(reply, "setCookie", None) or getattr(
                reply, "set_cookie", None
            )
            if callable(set_cookie_fn):
                try:
                    set_cookie_fn(
                        cookie_name,
                        guest_id,
                        {
                            "httpOnly": self.guest_cookie.http_only,
                            "secure": self.guest_cookie.secure,
                            "sameSite": self.guest_cookie.same_site,
                            "path": self.guest_cookie.path,
                            "maxAge": self.guest_cookie.max_age,
                            "signed": True,
                        },
                    )
                except TypeError:
                    signed_val = (
                        sign_cookie(guest_id, self.cookie_secret)
                        if self.cookie_secret
                        else guest_id
                    )
                    set_cookie_fn(
                        key=cookie_name,
                        value=signed_val,
                        httponly=self.guest_cookie.http_only,
                        secure=self.guest_cookie.secure,
                        samesite=self.guest_cookie.same_site,
                        path=self.guest_cookie.path,
                        max_age=self.guest_cookie.max_age,
                    )

        self._normalize_req_cookie(req, cookie_name, guest_id)
        return guest_id

    def ensureGuestId(self, req: Any, reply: Any = None) -> str:
        return self.ensure_guest_id(req, reply)

    @staticmethod
    def _normalize_req_cookie(req: Any, cookie_name: str, guest_id: str) -> None:
        if isinstance(req, dict):
            existing = req.get("cookies")
            merged = dict(existing) if isinstance(existing, Mapping) else {}
            merged[cookie_name] = guest_id
            req["cookies"] = merged
            return
        existing_attr = getattr(req, "cookies", None)
        merged_attr = dict(existing_attr) if isinstance(existing_attr, Mapping) else {}
        merged_attr[cookie_name] = guest_id
        try:
            req.cookies = merged_attr
        except Exception:  # noqa: S110
            pass

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

        raw_headers: list[tuple[bytes, bytes]] = list(scope.get("headers") or [])
        cookie_headers: list[str] = []
        non_cookie_headers: list[tuple[bytes, bytes]] = []
        for k_bytes, v_bytes in raw_headers:
            if k_bytes.lower() == b"cookie":
                cookie_headers.append(v_bytes.decode("latin-1"))
            else:
                non_cookie_headers.append((k_bytes, v_bytes))

        cookies = (
            _parse_cookie_header("; ".join(cookie_headers)) if cookie_headers else {}
        )
        cookie_name = self.guest_cookie.name
        valid_guest_id = self.read_valid_guest_id(cookies, cookie_name)
        minted = False
        if valid_guest_id is None:
            valid_guest_id = str(uuid.uuid4())
            minted = True

        # Normalize the Cookie header in scope so Starlette Request.cookies sees
        # the unsigned UUIDv4 guest_id (matching reference GuestIdMiddleware).
        cookies[cookie_name] = valid_guest_id
        normalized_cookie_str = "; ".join(f"{k}={v}" for k, v in cookies.items())
        scope["headers"] = [
            *non_cookie_headers,
            (b"cookie", normalized_cookie_str.encode("latin-1")),
        ]

        if "state" not in scope or not isinstance(scope.get("state"), dict):
            scope["state"] = {}
        scope["state"]["guest_id"] = valid_guest_id

        signed_cookie_val = (
            sign_cookie(valid_guest_id, self.cookie_secret)
            if self.cookie_secret
            else valid_guest_id
        )
        set_cookie_bytes = _format_set_cookie_header(
            cookie_name,
            signed_cookie_val,
            self.guest_cookie,
        )

        async def send_wrapper(message: Message) -> None:
            if minted and message["type"] == "http.response.start":
                resp_headers: list[tuple[bytes, bytes]] = list(
                    message.get("headers") or []
                )
                resp_headers.append((b"set-cookie", set_cookie_bytes))
                message = {**message, "headers": resp_headers}
            await send(message)

        if self.app is not None:
            await self.app(scope, receive, send_wrapper)


__all__ = [
    "DEFAULT_GUEST_COOKIE_MAX_AGE",
    "DEFAULT_GUEST_COOKIE_NAME",
    "GUEST_ID_UUID_V4_RE",
    "GuestCookieOptions",
    "GuestIdMiddleware",
]
