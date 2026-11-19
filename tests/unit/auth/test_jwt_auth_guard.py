from __future__ import annotations

import time

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import Depends, FastAPI, Request

from app.auth.access_token_authenticator import AccessTokenAuthenticator
from app.auth.guards.jwt_auth_guard import JwtAuthGuard, extract_jwt_from_request
from app.common.decorators import public
from app.common.filters.all_exceptions_filter import (
    AllExceptionsFilter,
    register_exception_handlers,
)


class FakeRedis:
    def __init__(self, revoked: set[str]) -> None:
        self.revoked = revoked

    async def get(self, key: str) -> str | None:
        return "true" if key.removeprefix("revoked_jti:") in self.revoked else None


@pytest.fixture(scope="module")
def keys() -> tuple[str, str]:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    public_pem = (
        key.public_key()
        .public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        .decode()
    )
    return private, public_pem


def _token(private: str, jti: str = "jti-1", *, ttl: int = 600) -> str:
    now = int(time.time())
    return jwt.encode(
        {
            "sub": "u1",
            "email": "a@b.co",
            "jti": jti,
            "role": "user",
            "iat": now,
            "exp": now + ttl,
        },
        private,
        algorithm="RS256",
    )


def _app(public_key: str, revoked: set[str]) -> FastAPI:
    guard = JwtAuthGuard(
        AccessTokenAuthenticator(
            redis_service=FakeRedis(revoked),
            config_service={"JWT_PUBLIC_KEY": public_key},
        )
    )
    app = FastAPI(dependencies=[Depends(guard)])
    register_exception_handlers(app, AllExceptionsFilter())

    @app.get("/private")
    async def private_route(request: Request) -> dict[str, str]:
        return {"user": str(request.state.user.id)}

    @app.get("/open")
    @public
    async def open_route() -> dict[str, bool]:
        return {"ok": True}

    return app


async def _get(
    app: FastAPI, path: str, headers: dict[str, str] | None = None
) -> httpx.Response:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        return await client.get(path, headers=headers)


async def test_default_deny_without_token(keys: tuple[str, str]) -> None:
    res = await _get(_app(keys[1], set()), "/private")
    assert res.status_code == 401


async def test_public_route_skips_auth(keys: tuple[str, str]) -> None:
    res = await _get(_app(keys[1], set()), "/open")
    assert res.status_code == 200


async def test_bearer_token_accepted(keys: tuple[str, str]) -> None:
    res = await _get(
        _app(keys[1], set()),
        "/private",
        {"Authorization": f"Bearer {_token(keys[0])}"},
    )
    assert res.status_code == 200
    assert res.json() == {"user": "u1"}


async def test_cookie_takes_precedence_over_bearer(keys: tuple[str, str]) -> None:
    res = await _get(
        _app(keys[1], set()),
        "/private",
        {
            "Cookie": f"access_token={_token(keys[0])}",
            "Authorization": "Bearer garbage",
        },
    )
    assert res.status_code == 200


async def test_revoked_jti_rejected(keys: tuple[str, str]) -> None:
    res = await _get(
        _app(keys[1], {"jti-1"}),
        "/private",
        {"Authorization": f"Bearer {_token(keys[0])}"},
    )
    assert res.status_code == 401


async def test_expired_and_invalid_tokens_rejected(keys: tuple[str, str]) -> None:
    app = _app(keys[1], set())
    expired = await _get(
        app, "/private", {"Authorization": f"Bearer {_token(keys[0], ttl=-10)}"}
    )
    assert expired.status_code == 401
    bad = await _get(app, "/private", {"Authorization": "Bearer nope"})
    assert bad.status_code == 401


def test_extract_jwt_prefers_cookie() -> None:
    req = {"cookies": {"access_token": "c"}, "headers": {"authorization": "Bearer b"}}
    assert extract_jwt_from_request(req) == "c"
    assert extract_jwt_from_request({"headers": {"authorization": "Bearer b"}}) == "b"
    assert extract_jwt_from_request({"headers": {"authorization": "Basic x"}}) is None
