from __future__ import annotations

from typing import Any, Final

from fastapi import APIRouter
from fastapi.responses import PlainTextResponse

from app.app_service import AppService
from app.common.decorators import public
from app.core.logger.tracing_interceptor import trace

RATE_LIMIT_METADATA_KEY: Final[str] = "rateLimitOptions"

HELLO_RATE_LIMIT_OPTIONS: Final[dict[str, Any]] = {
    "algorithm": "fixed-window-counter",
    "methods": [
        {
            "name": "hello-ip",
            "identifier": "ip",
            "duration": 60_000,
            "limit": 3,
        }
    ],
}

app_router = APIRouter()
_app_service = AppService()


@app_router.get("/", response_class=PlainTextResponse)
@public
@trace(context="AppController", method="getHello")
async def get_hello() -> str:
    return _app_service.get_hello()


setattr(get_hello, RATE_LIMIT_METADATA_KEY, HELLO_RATE_LIMIT_OPTIONS)


class AppController:
    """Controller class matching NestJS AppController."""

    def __init__(self, app_service: AppService | None = None) -> None:
        self._app_service = app_service or _app_service

    @trace(context="AppController", method="getHello")
    def get_hello(self) -> str:
        return self._app_service.get_hello()

    def getHello(self) -> str:
        return self.get_hello()


setattr(
    AppController.get_hello,
    RATE_LIMIT_METADATA_KEY,
    HELLO_RATE_LIMIT_OPTIONS,
)
setattr(
    AppController.getHello,
    RATE_LIMIT_METADATA_KEY,
    HELLO_RATE_LIMIT_OPTIONS,
)

__all__ = [
    "HELLO_RATE_LIMIT_OPTIONS",
    "RATE_LIMIT_METADATA_KEY",
    "AppController",
    "app_router",
    "get_hello",
]
