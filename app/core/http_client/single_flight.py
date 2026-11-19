from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Awaitable, Callable

from app.core.http_client.types import HttpRequest, HttpResponse

SENSITIVE_HEADERS = {
    "authorization",
    "cookie",
    "set-cookie",
    "x-api-key",
    "proxy-authorization",
}


def default_coalescing_key(request: HttpRequest) -> str:
    sorted_params = json.dumps(dict(sorted(request.params.items())), sort_keys=True)
    clean_headers: dict[str, str] = {}
    for k, v in request.headers.items():
        lk = k.lower()
        if lk in SENSITIVE_HEADERS:
            clean_headers[lk] = hashlib.sha256(v.encode()).hexdigest()
        else:
            clean_headers[lk] = v
    sorted_headers = json.dumps(dict(sorted(clean_headers.items())), sort_keys=True)
    req_path = request.path or ""
    return (
        f"{request.method.upper()}|{request.url}|{req_path}|"
        f"{sorted_params}|{sorted_headers}"
    )


class SingleFlightCoalescer:
    def __init__(self) -> None:
        self._in_flight: dict[str, asyncio.Future[HttpResponse]] = {}
        self._consumers: dict[str, int] = {}

    async def run(
        self,
        key: str,
        factory: Callable[[], Awaitable[HttpResponse]],
    ) -> HttpResponse:
        if key in self._in_flight:
            self._consumers[key] = self._consumers.get(key, 1) + 1
            return await self._in_flight[key]

        loop = asyncio.get_running_loop()
        future: asyncio.Future[HttpResponse] = loop.create_future()
        self._in_flight[key] = future
        self._consumers[key] = 1

        try:
            res = await factory()
            future.set_result(res)
            return res
        except Exception as exc:
            future.set_exception(exc)
            raise exc
        finally:
            self._consumers[key] = max(0, self._consumers.get(key, 1) - 1)
            if self._consumers[key] == 0:
                self._in_flight.pop(key, None)
                self._consumers.pop(key, None)
