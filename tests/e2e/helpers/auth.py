"""Opaque-box authentication and CSRF helpers for E2E tests."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    import httpx

AuthMode = Literal["cookie", "bearer"]


@dataclass(slots=True)
class AuthSession:
    """Captured authentication tokens and cookies for an E2E user session."""

    email: str
    access_token: str
    session_id: str
    csrf_token: str
    cookies: dict[str, str]
    mode: AuthMode = "cookie"

    @property
    def bearer_headers(self) -> dict[str, str]:
        """Return ``Authorization: Bearer ...`` header."""
        return {"Authorization": f"Bearer {self.access_token}"}

    @property
    def csrf_headers(self) -> dict[str, str]:
        """Return ``x-csrf-token`` double-submit header."""
        return {"x-csrf-token": self.csrf_token}

    def auth_headers(
        self,
        mode: AuthMode | None = None,
        *,
        include_csrf: bool = True,
    ) -> dict[str, str]:
        """Return auth/CSRF headers for the selected authentication mode."""
        effective = mode or self.mode
        if effective == "bearer":
            return self.bearer_headers
        return self.csrf_headers if include_csrf else {}

    def auth_cookies(
        self,
        mode: AuthMode | None = None,
        *,
        include_session_id: bool = False,
    ) -> dict[str, str]:
        """Return cookies needed for cookie-auth (or empty dict for bearer)."""
        effective = mode or self.mode
        if effective == "bearer":
            return {}
        jar = {
            "access_token": self.access_token,
            "csrf_token": self.csrf_token,
        }
        if include_session_id:
            jar["session_id"] = self.session_id
        return jar


def _extract_cookies(
    resp: httpx.Response,
    fallback: dict[str, str] | None = None,
) -> dict[str, str]:
    jar = dict(fallback) if fallback else {}
    for key, val in resp.cookies.items():
        jar[str(key)] = str(val)
    return jar


def _clear_auth_cookies_on_client(client: httpx.AsyncClient) -> None:
    for name in ("access_token", "session_id", "csrf_token"):
        try:
            client.cookies.delete(name)
        except KeyError:
            pass


def _build_request_headers(
    headers: Mapping[str, str] | None = None,
    cookies: Mapping[str, str] | None = None,
) -> dict[str, str] | None:
    merged: dict[str, str] = dict(headers) if headers is not None else {}
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
    return merged or None


async def get_csrf_token(client: httpx.AsyncClient) -> str:
    """Call ``GET /auth/csrf`` and assert matching ``csrf_token`` cookie & JSON."""
    resp = await client.get("/auth/csrf")
    assert resp.status_code == 200, (
        f"Expected 200 from GET /auth/csrf, got {resp.status_code}: {resp.text}"
    )
    data: dict[str, Any] = resp.json()
    token = str(data["csrfToken"])
    cookie_val = resp.cookies.get("csrf_token") or client.cookies.get("csrf_token")
    assert cookie_val == token, (
        f"csrf_token cookie {cookie_val!r} does not match JSON csrfToken {token!r}"
    )
    return token


async def register_user(
    client: httpx.AsyncClient,
    email: str,
    password: str,
    *,
    headers: Mapping[str, str] | None = None,
    cookies: Mapping[str, str] | None = None,
) -> httpx.Response:
    """Call ``POST /auth/register`` and return the raw ``httpx.Response``."""
    return await client.post(
        "/auth/register",
        json={"email": email, "password": password},
        headers=_build_request_headers(headers, cookies),
    )


async def login_user(
    client: httpx.AsyncClient,
    email: str,
    password: str,
    *,
    mode: AuthMode = "cookie",
    headers: Mapping[str, str] | None = None,
    cookies: Mapping[str, str] | None = None,
    clear_client_cookies: bool | None = None,
) -> AuthSession:
    """Call ``POST /auth/login`` and return an :class:`AuthSession`."""
    resp = await client.post(
        "/auth/login",
        json={"email": email, "password": password},
        headers=_build_request_headers(headers, cookies),
    )
    assert resp.status_code == 200, (
        f"Expected 200 from POST /auth/login, got {resp.status_code}: {resp.text}"
    )
    data: dict[str, Any] = resp.json()
    access_token = str(data["accessToken"])
    session_id = str(data["sessionId"])
    csrf_token = str(data["csrfToken"])
    jar = _extract_cookies(
        resp,
        {
            "access_token": access_token,
            "session_id": session_id,
            "csrf_token": csrf_token,
        },
    )

    should_clear = (
        (mode == "bearer") if clear_client_cookies is None else clear_client_cookies
    )
    if should_clear:
        _clear_auth_cookies_on_client(client)

    return AuthSession(
        email=email,
        access_token=access_token,
        session_id=session_id,
        csrf_token=csrf_token,
        cookies=jar,
        mode=mode,
    )


async def register_and_login(
    client: httpx.AsyncClient,
    email: str,
    password: str,
    *,
    mode: AuthMode = "cookie",
    ignore_conflict: bool = False,
    clear_client_cookies: bool | None = None,
) -> AuthSession:
    """Execute ``/auth/csrf`` -> ``/auth/register`` -> ``/auth/login`` workflow."""
    csrf = await get_csrf_token(client)
    hdrs: dict[str, str] | None = None
    if client.cookies.get("access_token") or client.cookies.get("session_id"):
        hdrs = {"x-csrf-token": csrf}

    reg_resp = await register_user(client, email, password, headers=hdrs)
    expected_codes = (201, 409) if ignore_conflict else (201,)
    assert reg_resp.status_code in expected_codes, (
        f"Expected {expected_codes} from POST /auth/register, "
        f"got {reg_resp.status_code}: {reg_resp.text}"
    )

    return await login_user(
        client,
        email,
        password,
        mode=mode,
        headers=hdrs,
        clear_client_cookies=clear_client_cookies,
    )


async def refresh_session(
    client: httpx.AsyncClient,
    session: AuthSession,
    *,
    mode: AuthMode | None = None,
) -> AuthSession:
    """Rotate tokens via ``POST /auth/refresh`` in cookie or bearer mode."""
    effective = mode or session.mode
    if effective == "cookie":
        req_headers = _build_request_headers(
            {"x-csrf-token": session.csrf_token},
            {
                "session_id": session.session_id,
                "csrf_token": session.csrf_token,
            },
        )
        resp = await client.post(
            "/auth/refresh",
            headers=req_headers,
        )
    else:
        _clear_auth_cookies_on_client(client)
        resp = await client.post(
            "/auth/refresh",
            json={"sessionId": session.session_id},
        )

    assert resp.status_code == 200, (
        f"Expected 200 from POST /auth/refresh, got {resp.status_code}: {resp.text}"
    )
    data: dict[str, Any] = resp.json()
    new_access = str(data["accessToken"])
    new_session = str(data["sessionId"])
    new_csrf = str(data["csrfToken"])
    jar = _extract_cookies(
        resp,
        {
            "access_token": new_access,
            "session_id": new_session,
            "csrf_token": new_csrf,
        },
    )
    if effective == "bearer":
        _clear_auth_cookies_on_client(client)
    return AuthSession(
        email=session.email,
        access_token=new_access,
        session_id=new_session,
        csrf_token=new_csrf,
        cookies=jar,
        mode=effective,
    )


async def logout_session(
    client: httpx.AsyncClient,
    session: AuthSession,
    *,
    mode: AuthMode | None = None,
    send_session_in_body: bool = True,
) -> httpx.Response:
    """Revoke the active session via ``POST /auth/logout``."""
    effective = mode or session.mode
    body = {"sessionId": session.session_id} if send_session_in_body else None
    if effective == "cookie":
        req_headers = _build_request_headers(
            session.auth_headers("cookie", include_csrf=True),
            session.auth_cookies("cookie", include_session_id=True),
        )
        return await client.post(
            "/auth/logout",
            headers=req_headers,
            json=body,
        )
    _clear_auth_cookies_on_client(client)
    return await client.post(
        "/auth/logout",
        headers=session.bearer_headers,
        json=body,
    )


__all__ = [
    "AuthMode",
    "AuthSession",
    "get_csrf_token",
    "login_user",
    "logout_session",
    "refresh_session",
    "register_and_login",
    "register_user",
]
