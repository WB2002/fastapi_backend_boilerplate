from __future__ import annotations

import httpx
from fastapi import FastAPI, Request

from app.common.utils.cookie_utils import sign_cookie
from app.core.rate_limiter.guest_id_middleware import (
    GUEST_ID_UUID_V4_RE,
    GuestIdMiddleware,
)

SECRET = "test-cookie-secret"
VALID_ID = "3f2b8c1e-9d4a-4b6f-8a1c-2e7d5f0a9b13"


def _app() -> FastAPI:
    app = FastAPI()
    app.add_middleware(GuestIdMiddleware, cookie_secret=SECRET, node_env="production")

    @app.get("/who")
    async def who(request: Request) -> dict[str, str]:
        return {
            "state": str(request.state.guest_id),
            "cookie": request.cookies.get("guest_id", ""),
        }

    return app


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=_app()), base_url="http://t"
    )


async def test_mints_signed_uuid_v4_cookie_when_missing() -> None:
    async with _client() as client:
        res = await client.get("/who")
    body = res.json()
    assert GUEST_ID_UUID_V4_RE.match(body["state"])
    assert body["cookie"] == body["state"]
    set_cookie = res.headers["set-cookie"]
    assert set_cookie.startswith("guest_id=")
    assert f"guest_id={body['state']}." in set_cookie
    for attr in ("HttpOnly", "Secure", "SameSite=Lax", "Path=/", "Max-Age=31536000"):
        assert attr in set_cookie


async def test_valid_signed_cookie_is_reused_without_set_cookie() -> None:
    signed = sign_cookie(VALID_ID, SECRET)
    async with _client() as client:
        res = await client.get("/who", headers={"Cookie": f"guest_id={signed}"})
    assert res.json()["state"] == VALID_ID
    assert "set-cookie" not in res.headers


async def test_tampered_signature_is_replaced() -> None:
    signed = sign_cookie(VALID_ID, SECRET)
    async with _client() as client:
        res = await client.get("/who", headers={"Cookie": f"guest_id={signed}x"})
    assert res.json()["state"] != VALID_ID
    assert "set-cookie" in res.headers


async def test_signed_non_uuid_value_is_replaced() -> None:
    signed = sign_cookie("not-a-uuid", SECRET)
    async with _client() as client:
        res = await client.get("/who", headers={"Cookie": f"guest_id={signed}"})
    assert GUEST_ID_UUID_V4_RE.match(res.json()["state"])
    assert "set-cookie" in res.headers
