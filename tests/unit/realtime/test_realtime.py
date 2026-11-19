from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from app.auth.types import AuthenticatedUser
from app.realtime.connection_limiter import WsConnectionLimiter
from app.realtime.constants import REALTIME_NAMESPACE
from app.realtime.extract_token import extract_access_token
from app.realtime.gateway import RealtimeGateway
from app.realtime.message_rate_limiter import WsMessageRateLimiter


def test_extract_access_token_from_bearer() -> None:
    handshake = {"headers": {"authorization": "Bearer my-jwt-token"}}
    assert extract_access_token(handshake) == "my-jwt-token"


def test_extract_access_token_from_cookie() -> None:
    handshake = {"headers": {"cookie": "other=123; access_token=cookie-jwt; foo=bar"}}
    assert extract_access_token(handshake) == "cookie-jwt"


def test_extract_access_token_from_auth() -> None:
    handshake = {"headers": {}, "auth": {"token": "auth-jwt"}}
    assert extract_access_token(handshake) == "auth-jwt"


def test_extract_access_token_from_query() -> None:
    handshake = {"headers": {}, "query": {"access_token": "query-jwt"}}
    assert extract_access_token(handshake, allow_query_token=False) is None
    assert extract_access_token(handshake, allow_query_token=True) == "query-jwt"


@pytest.mark.asyncio
async def test_connection_limiter_local_max_and_release() -> None:
    limiter = WsConnectionLimiter(max_connections_per_user=2)
    await limiter.register_connection("user-1", "s1")
    await limiter.register_connection("user-1", "s2")

    with pytest.raises(HTTPException) as exc_info:
        await limiter.register_connection("user-1", "s3")
    assert exc_info.value.status_code == 429

    await limiter.release_connection("user-1", "s1")
    await limiter.register_connection("user-1", "s3")


@pytest.mark.asyncio
async def test_message_rate_limiter_local() -> None:
    limiter = WsMessageRateLimiter(messages_per_minute=2)
    assert await limiter.is_allowed("user-1") is True
    assert await limiter.is_allowed("user-1") is True
    assert await limiter.is_allowed("user-1") is False


@pytest.mark.asyncio
async def test_realtime_gateway_connect_and_join() -> None:
    sio = MagicMock()
    sio.on = MagicMock(return_value=lambda f: f)
    sio.enter_room = MagicMock()
    sio.emit = AsyncMock()

    authenticator = MagicMock()
    test_user = AuthenticatedUser(
        id="user-123", email="user@test.com", jti="jti-1", role="user"
    )
    authenticator.verify_access_token = AsyncMock(return_value=test_user)

    connection_limiter = MagicMock()
    connection_limiter.register_connection = AsyncMock()
    connection_limiter.release_connection = AsyncMock()

    rate_limiter = MagicMock()
    rate_limiter.is_allowed = AsyncMock(return_value=True)

    gateway = RealtimeGateway(
        sio=sio,
        authenticator=authenticator,
        connection_limiter=connection_limiter,
        message_rate_limiter=rate_limiter,
    )

    # 1. Connect with valid token
    environ = {"HTTP_AUTHORIZATION": "Bearer valid-token"}
    connected = await gateway.handle_connect("sid-1", environ)
    assert connected is True
    connection_limiter.register_connection.assert_awaited_once_with("user-123", "sid-1")

    # 2. Join foreign room rejects
    res_foreign = await gateway.handle_join("sid-1", {"room": "user:other-user"})
    assert res_foreign["ok"] is False
    assert "Cannot join foreign room" in res_foreign["error"]

    # 3. Join own room succeeds
    res_own = await gateway.handle_join("sid-1", {"room": "user:user-123"})
    assert res_own["ok"] is True
    assert res_own["room"] == "user:user-123"
    sio.enter_room.assert_called_once_with(
        "sid-1", "user:user-123", namespace=REALTIME_NAMESPACE
    )

    # 4. Message emitted
    msg_res = await gateway.handle_message("sid-1", {"text": "hello realtime"})
    assert msg_res == {"ok": True}
    sio.emit.assert_awaited_once_with(
        "message",
        {"text": "hello realtime", "from": "user-123"},
        room="user:user-123",
        namespace=REALTIME_NAMESPACE,
    )

    # 5. Non-admin broadcast is rejected
    admin_res = await gateway.handle_admin_broadcast("sid-1", {"text": "hello world"})
    assert admin_res is not None
    assert admin_res["ok"] is False
    assert "admin role required" in admin_res["error"]

    # 6. Disconnect releases connection
    await gateway.handle_disconnect("sid-1")
    connection_limiter.release_connection.assert_awaited_once_with("user-123", "sid-1")


@pytest.mark.asyncio
async def test_realtime_gateway_admin_broadcast_succeeds_for_admin() -> None:
    sio = MagicMock()
    sio.on = MagicMock(return_value=lambda f: f)
    sio.emit = AsyncMock()

    authenticator = MagicMock()
    admin_user = AuthenticatedUser(
        id="admin-1", email="admin@test.com", jti="jti-admin", role="admin"
    )
    authenticator.verify_access_token = AsyncMock(return_value=admin_user)

    connection_limiter = MagicMock()
    connection_limiter.register_connection = AsyncMock()
    connection_limiter.release_connection = AsyncMock()

    rate_limiter = MagicMock()
    rate_limiter.is_allowed = AsyncMock(return_value=True)

    gateway = RealtimeGateway(
        sio=sio,
        authenticator=authenticator,
        connection_limiter=connection_limiter,
        message_rate_limiter=rate_limiter,
    )

    await gateway.handle_connect(
        "sid-admin", {"HTTP_AUTHORIZATION": "Bearer admin-token"}
    )
    res = await gateway.handle_admin_broadcast("sid-admin", {"text": "system alert"})
    assert res == {"ok": True}
    sio.emit.assert_awaited_once_with(
        "admin-broadcast",
        {"text": "system alert"},
        namespace=REALTIME_NAMESPACE,
    )
