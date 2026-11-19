from __future__ import annotations

import re

import httpx
from fastapi import Response

from app.common.constants import (
    ACCESS_TOKEN_COOKIE,
    CSRF_TOKEN_COOKIE,
    SESSION_ID_COOKIE,
)
from app.common.decorators import public
from app.core.csrf import (
    CSRF_FORBIDDEN_BODY,
    DEFAULT_CSRF_EXEMPT_PATHS,
    CsrfMiddleware,
    CsrfTokenService,
    is_csrf_exempt_path,
    normalize_request_path,
    parse_csv_paths,
    set_csrf_cookie,
    should_enforce_csrf,
)
from app.main import create_app


def test_csrf_token_service_generate_and_match() -> None:
    service = CsrfTokenService()
    a = service.generate_token()
    b = service.generateToken()

    assert re.match(r"^[A-Za-z0-9_-]+$", a) is not None
    assert len(a) == 43
    assert a != b

    assert service.tokens_match(a, a) is True
    assert service.tokensMatch(a, a) is True
    assert service.tokens_match(None, a) is False
    assert service.tokens_match(a, None) is False
    assert service.tokens_match("", a) is False
    assert service.tokens_match(a, f"{a}x") is False
    assert service.tokens_match(a, b) is False


def test_set_csrf_cookie_attributes() -> None:
    res = Response()
    set_csrf_cookie(res, "token-val", is_production=False)
    header = res.headers.get("set-cookie", "")
    assert "csrf_token=token-val" in header
    assert "HttpOnly" not in header
    assert "SameSite=strict" in header
    assert "Path=/" in header
    assert "Secure" not in header

    prod_res = Response()
    set_csrf_cookie(prod_res, "token-prod", is_production=True)
    prod_header = prod_res.headers.get("set-cookie", "")
    assert "Secure" in prod_header
    assert "SameSite=strict" in prod_header


def test_csrf_helpers_exempt_paths_and_should_enforce() -> None:
    assert DEFAULT_CSRF_EXEMPT_PATHS == ("/health", "/health/ready")
    assert normalize_request_path("/health/ready/?foo=1") == "/health/ready"
    assert normalize_request_path("/") == "/"

    exempt = parse_csv_paths(
        "/webhooks/stripe, /public/hook", DEFAULT_CSRF_EXEMPT_PATHS
    )
    assert exempt == ["/health", "/health/ready", "/webhooks/stripe", "/public/hook"]
    assert is_csrf_exempt_path("/health/ready?check=1", exempt) is True
    assert is_csrf_exempt_path("/webhooks/stripe/", exempt) is True
    assert is_csrf_exempt_path("/auth/logout", exempt) is False

    # Safe methods skipped
    assert (
        should_enforce_csrf(
            method="GET",
            cookies={ACCESS_TOKEN_COOKIE: "jwt"},
        )
        is False
    )
    assert (
        should_enforce_csrf(
            method="HEAD",
            cookies={ACCESS_TOKEN_COOKIE: "jwt"},
        )
        is False
    )
    assert (
        should_enforce_csrf(
            method="OPTIONS",
            cookies={ACCESS_TOKEN_COOKIE: "jwt"},
        )
        is False
    )

    # Bearer auth skipped even if cookie present
    assert (
        should_enforce_csrf(
            method="POST",
            headers={"Authorization": "Bearer some-jwt"},
            cookies={ACCESS_TOKEN_COOKIE: "jwt"},
        )
        is False
    )

    # No session cookies -> skipped
    assert (
        should_enforce_csrf(
            method="POST",
            headers={},
            cookies={"other_cookie": "1"},
        )
        is False
    )

    # Unsafe method with access_token or session_id cookie -> enforced
    assert (
        should_enforce_csrf(
            method="POST",
            headers={},
            cookies={ACCESS_TOKEN_COOKIE: "jwt"},
        )
        is True
    )
    assert (
        should_enforce_csrf(
            method="DELETE",
            headers={},
            cookies={SESSION_ID_COOKIE: "sid"},
        )
        is True
    )


async def test_csrf_middleware_http_enforcement(
    valid_base_env: dict[str, str],
) -> None:
    app = create_app(env=valid_base_env)

    @app.post("/probe/mutate")
    @public
    async def _probe_mutate() -> dict[str, bool]:
        return {"ok": True}

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. POST with access_token cookie but no x-csrf-token header -> 403
        res_403 = await client.post(
            "/probe/mutate",
            headers={"cookie": f"{ACCESS_TOKEN_COOKIE}=fake-jwt"},
        )
        assert res_403.status_code == 403
        body_403 = res_403.json()
        assert body_403 == CSRF_FORBIDDEN_BODY
        # Must NOT have AllExceptionsFilter fields (timestamp/path/requestId)
        assert "timestamp" not in body_403
        assert "requestId" not in body_403

        # 2. POST with matching csrf_token cookie and x-csrf-token header -> 200
        token = CsrfTokenService().generate_token()
        cookie_hdr = f"{ACCESS_TOKEN_COOKIE}=fake-jwt; {CSRF_TOKEN_COOKIE}={token}"
        res_200 = await client.post(
            "/probe/mutate",
            headers={
                "x-csrf-token": token,
                "cookie": cookie_hdr,
            },
        )
        assert res_200.status_code == 200
        assert res_200.json() == {"ok": True}

        # 3. POST with Authorization: Bearer bypasses CSRF even with cookie present
        res_bearer = await client.post(
            "/probe/mutate",
            headers={
                "Authorization": "Bearer my-jwt-token",
                "cookie": f"{ACCESS_TOKEN_COOKIE}=fake-jwt",
            },
        )
        assert res_bearer.status_code == 200

        # 4. POST without session cookies bypasses CSRF
        res_no_cookie = await client.post("/probe/mutate")
        assert res_no_cookie.status_code == 200


def test_csrf_middleware_enforce_on_request_unit() -> None:
    token_service = CsrfTokenService()
    codes: list[int] = []
    sent: list[object] = []

    class _FakeReply:
        def code(self, status: int) -> _FakeReply:
            codes.append(status)
            return self

        def send(self, body: object) -> None:
            sent.append(body)

    reply = _FakeReply()
    mw = CsrfMiddleware(
        csrf_token_service=token_service,
        options={
            "enabled": True,
            "cookieName": CSRF_TOKEN_COOKIE,
            "headerName": "x-csrf-token",
            "exemptPaths": ["/health"],
        },
    )

    mw.enforceOnRequest({"method": "GET", "url": "/any"}, reply)
    assert len(codes) == 0

    mw.enforceOnRequest(
        {
            "method": "POST",
            "url": "/health",
            "cookies": {ACCESS_TOKEN_COOKIE: "x"},
        },
        reply,
    )
    assert len(codes) == 0

    mw.enforceOnRequest(
        {
            "method": "POST",
            "url": "/auth/logout",
            "cookies": {
                ACCESS_TOKEN_COOKIE: "jwt",
                CSRF_TOKEN_COOKIE: "abc",
            },
            "headers": {},
        },
        reply,
    )
    assert codes == [403]
    assert sent == [CSRF_FORBIDDEN_BODY]
