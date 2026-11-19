from __future__ import annotations

import gzip
from collections.abc import MutableMapping
from typing import Any

import httpx
from fastapi import FastAPI
from fastapi.responses import PlainTextResponse

from app.core.compression import CompressionMiddleware
from app.main import HELMET_CSP_POLICY, create_app

COMPRESSION_THRESHOLD = 1024
LARGE_PAYLOAD = "x" * (COMPRESSION_THRESHOLD + 64)
SMALL_PAYLOAD = "ok"


def _build_compression_app() -> FastAPI:
    app = FastAPI()
    app.add_middleware(CompressionMiddleware, threshold=COMPRESSION_THRESHOLD)

    @app.get("/large", response_class=PlainTextResponse)
    async def _large() -> str:
        return LARGE_PAYLOAD

    @app.get("/small", response_class=PlainTextResponse)
    async def _small() -> str:
        return SMALL_PAYLOAD

    return app


class TestResponseCompressionHttp:
    async def test_compresses_large_payloads_with_gzip(self) -> None:
        app = _build_compression_app()
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://testserver",
        ) as client:
            res = await client.get(
                "/large",
                headers={"accept-encoding": "gzip"},
            )
            assert res.status_code == 200
            assert res.headers.get("content-encoding") == "gzip"
            assert res.text == LARGE_PAYLOAD

    async def test_compresses_large_payloads_with_brotli_preferred(
        self,
    ) -> None:
        app = _build_compression_app()
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://testserver",
        ) as client:
            res = await client.get(
                "/large",
                headers={"accept-encoding": "gzip, br"},
            )
            assert res.status_code == 200
            assert res.headers.get("content-encoding") == "br"
            assert res.text == LARGE_PAYLOAD

            raw_chunks: list[bytes] = []
            headers_received: dict[str, str] = {}

            async def receive() -> MutableMapping[str, Any]:
                return {"type": "http.request", "body": b"", "more_body": False}

            async def send(message: MutableMapping[str, Any]) -> None:
                if message["type"] == "http.response.start":
                    for k, v in message.get("headers", []):
                        headers_received[k.decode("latin-1").lower()] = v.decode(
                            "latin-1"
                        )
                elif message["type"] == "http.response.body":
                    raw_chunks.append(bytes(message.get("body", b"")))

            await app(
                {
                    "type": "http",
                    "asgi": {"version": "3.0"},
                    "http_version": "1.1",
                    "method": "GET",
                    "scheme": "http",
                    "path": "/large",
                    "raw_path": b"/large",
                    "query_string": b"",
                    "headers": [(b"accept-encoding", b"br")],
                },
                receive,
                send,
            )
            assert headers_received.get("content-encoding") == "br"
            assert len(b"".join(raw_chunks)) < len(LARGE_PAYLOAD)

            raw_chunks.clear()
            headers_received.clear()
            await app(
                {
                    "type": "http",
                    "asgi": {"version": "3.0"},
                    "http_version": "1.1",
                    "method": "GET",
                    "scheme": "http",
                    "path": "/large",
                    "raw_path": b"/large",
                    "query_string": b"",
                    "headers": [(b"accept-encoding", b"gzip")],
                },
                receive,
                send,
            )
            assert headers_received.get("content-encoding") == "gzip"
            assert (
                gzip.decompress(b"".join(raw_chunks)).decode("utf-8") == LARGE_PAYLOAD
            )

    async def test_does_not_compress_payloads_below_threshold(self) -> None:
        app = _build_compression_app()
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://testserver",
        ) as client:
            res = await client.get(
                "/small",
                headers={"accept-encoding": "gzip, br"},
            )
            assert res.status_code == 200
            assert "content-encoding" not in res.headers
            assert res.text == SMALL_PAYLOAD

    async def test_leaves_body_uncompressed_for_identity_or_omitted_header(
        self,
    ) -> None:
        app = _build_compression_app()
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://testserver",
        ) as client:
            res_identity = await client.get(
                "/large",
                headers={"accept-encoding": "identity"},
            )
            assert res_identity.status_code == 200
            assert "content-encoding" not in res_identity.headers
            assert res_identity.text == LARGE_PAYLOAD

            res_empty = await client.get(
                "/large",
                headers={"accept-encoding": ""},
            )
            assert res_empty.status_code == 200
            assert "content-encoding" not in res_empty.headers
            assert res_empty.text == LARGE_PAYLOAD


class TestCreateAppRuntimeAndSecurityHeaders:
    async def test_create_app_root_route_and_helmet_security_headers(
        self,
        valid_base_env: dict[str, str],
    ) -> None:
        app = create_app(valid_base_env)
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://testserver",
        ) as client:
            res = await client.get("/")
            assert res.status_code == 200
            assert res.text == "Hello World!"
            assert res.headers["x-content-type-options"] == "nosniff"
            assert res.headers["x-frame-options"] == "SAMEORIGIN"
            assert res.headers["content-security-policy"] == HELMET_CSP_POLICY
            assert "x-powered-by" not in res.headers
            assert "x-request-id" in res.headers
            assert "x-request-deadline" in res.headers
