from __future__ import annotations

import httpx
import pytest
from fastapi import Depends, FastAPI

from app.conformance.conformance_harness_auth_guard import (
    require_conformance_harness_token,
)


def _app(expected_token: str) -> FastAPI:
    app = FastAPI()
    app.state.env = {"CONFORMANCE_HARNESS_TOKEN": expected_token}

    @app.get("/protected", dependencies=[Depends(require_conformance_harness_token)])
    async def protected() -> dict[str, bool]:
        return {"ok": True}

    return app


@pytest.mark.asyncio
async def test_harness_guard_rejects_missing_bearer() -> None:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=_app("secret")),
        base_url="http://t",
    ) as client:
        res = await client.get("/protected")
    assert res.status_code == 401


@pytest.mark.asyncio
async def test_harness_guard_accepts_valid_bearer() -> None:
    token = "conformance-harness-secret-token"
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=_app(token)),
        base_url="http://t",
    ) as client:
        res = await client.get(
            "/protected",
            headers={"Authorization": f"Bearer {token}"},
        )
    assert res.status_code == 200
    assert res.json() == {"ok": True}


@pytest.mark.asyncio
async def test_harness_guard_rejects_invalid_bearer() -> None:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=_app("expected")),
        base_url="http://t",
    ) as client:
        res = await client.get("/protected", headers={"Authorization": "Bearer wrong"})
    assert res.status_code == 401
