from __future__ import annotations

import httpx
from fastapi import Depends, FastAPI

from app.common.config.resolve_coordination_storage import RateLimitStorage
from app.common.decorators import skip_rate_limit
from app.common.filters.all_exceptions_filter import (
    AllExceptionsFilter,
    register_exception_handlers,
)
from app.core.rate_limiter.rate_limit_guard import (
    GLOBAL_IP_METHOD,
    RateLimiterOptions,
    RateLimitGuard,
    build_rate_limit_key,
    default_rate_limiter_options,
    hash_route_scope,
    rate_limit,
    rate_limit_cost,
)
from app.core.rate_limiter.stores import (
    MemoryRateLimitBackend,
    RateLimitAlgorithm,
    RateLimitIdentifier,
)


def _app(options: RateLimiterOptions | None = None) -> FastAPI:
    guard = RateLimitGuard(
        MemoryRateLimitBackend(),
        options or default_rate_limiter_options(),
        now_ms=lambda: 1_700_000_000_000,
    )
    app = FastAPI(dependencies=[Depends(guard)])
    register_exception_handlers(app, AllExceptionsFilter())

    @app.get("/default")
    async def default() -> dict[str, bool]:
        return {"ok": True}

    @app.get("/tight")
    @rate_limit(
        {"name": "tight-ip", "identifier": "ip", "duration": 60_000, "limit": 2}
    )
    async def tight() -> dict[str, bool]:
        return {"ok": True}

    @app.get("/costly")
    @rate_limit_cost(5)
    @rate_limit(
        {"name": "costly", "identifier": "ip", "duration": 60_000, "limit": 5},
        algorithm=RateLimitAlgorithm.TOKEN_BUCKET,
    )
    async def costly() -> dict[str, bool]:
        return {"ok": True}

    @app.get("/guest")
    @rate_limit(
        {"name": "guest", "identifier": "GuestId", "duration": 60_000, "limit": 1}
    )
    async def guest() -> dict[str, bool]:
        return {"ok": True}

    @app.get("/user")
    @rate_limit({"name": "user", "identifier": "UserId", "duration": 1, "limit": 1})
    async def user() -> dict[str, bool]:
        return {"ok": True}

    @app.get("/skipped")
    @skip_rate_limit
    async def skipped() -> dict[str, bool]:
        return {"ok": True}

    return app


def _client(app: FastAPI) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://t"
    )


def test_default_options_match_reference_global_ip_rule() -> None:
    options = default_rate_limiter_options()
    assert options.methods == (GLOBAL_IP_METHOD,)
    assert GLOBAL_IP_METHOD.name == "global-ip"
    assert GLOBAL_IP_METHOD.identifier == RateLimitIdentifier.IP
    assert GLOBAL_IP_METHOD.duration == 60_000
    assert GLOBAL_IP_METHOD.limit == 10
    assert GLOBAL_IP_METHOD.cost == 1
    assert GLOBAL_IP_METHOD.key_scope == "global"
    assert options.algorithm == RateLimitAlgorithm.FIXED_WINDOW_COUNTER


def test_key_format_and_route_scope_hashing() -> None:
    plain = build_rate_limit_key(
        policy="p",
        identifier=RateLimitIdentifier.IP,
        tracker="1.2.3.4",
        route_scope="GET:/x",
        storage=RateLimitStorage.MEMORY,
    )
    assert plain == "rl:p:Ip:1.2.3.4:GET:/x"
    hashed = build_rate_limit_key(
        policy=None,
        identifier=RateLimitIdentifier.IP,
        tracker="1.2.3.4",
        route_scope="GET:/x",
        storage=RateLimitStorage.REDIS,
    )
    assert hashed == f"rl:default:Ip:1.2.3.4:{hash_route_scope('GET:/x')}"
    assert len(hash_route_scope("GET:/x")) == 16


async def test_global_ip_rule_limits_to_ten_then_429() -> None:
    async with _client(_app()) as client:
        for i in range(10):
            res = await client.get("/default")
            assert res.status_code == 200
            assert res.headers["x-ratelimit-limit"] == "10"
            assert res.headers["x-ratelimit-remaining"] == str(9 - i)
            assert res.headers["x-ratelimit-reset"].isdigit()
        blocked = await client.get("/default")
    assert blocked.status_code == 429
    assert int(blocked.headers["retry-after"]) >= 1
    assert blocked.headers["x-ratelimit-remaining"] == "0"
    assert blocked.json()["message"] == "Too Many Requests"


async def test_global_scope_is_shared_across_routes() -> None:
    async with _client(_app()) as client:
        for _ in range(10):
            await client.get("/default")
        assert (await client.get("/default")).status_code == 429


async def test_route_policy_overrides_global_and_is_per_route() -> None:
    async with _client(_app()) as client:
        assert (await client.get("/tight")).status_code == 200
        assert (await client.get("/tight")).status_code == 200
        blocked = await client.get("/tight")
        assert blocked.status_code == 429
        assert (await client.get("/default")).status_code == 200


async def test_handler_cost_consumes_budget() -> None:
    async with _client(_app()) as client:
        assert (await client.get("/costly")).status_code == 200
        assert (await client.get("/costly")).status_code == 429


async def test_guest_identifier_requires_guest_cookie() -> None:
    async with _client(_app()) as client:
        missing = await client.get("/guest")
        assert missing.status_code == 400
        ok = await client.get("/guest", headers={"Cookie": "guest_id=abc"})
        assert ok.status_code == 200
        blocked = await client.get("/guest", headers={"Cookie": "guest_id=abc"})
        assert blocked.status_code == 429


async def test_user_identifier_skipped_when_unauthenticated() -> None:
    async with _client(_app()) as client:
        for _ in range(3):
            assert (await client.get("/user")).status_code == 200


async def test_skip_rate_limit_bypasses_guard() -> None:
    async with _client(_app()) as client:
        for _ in range(15):
            res = await client.get("/skipped")
            assert res.status_code == 200
        assert "x-ratelimit-limit" not in res.headers
