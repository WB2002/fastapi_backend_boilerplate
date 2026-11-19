from __future__ import annotations

import gzip
from typing import Any, Final

import brotli_asgi
from starlette.types import ASGIApp, Message, Receive, Scope, Send

DEFAULT_COMPRESSION_THRESHOLD: Final[int] = 1024


def _negotiate_encoding(accept_encoding: str) -> str | None:
    """Negotiate 'br' (preferred) or 'gzip' (fallback) from Accept-Encoding header."""
    if not accept_encoding or not accept_encoding.strip():
        return None

    q_values: dict[str, float] = {}
    for raw_part in accept_encoding.split(","):
        part = raw_part.strip()
        if not part:
            continue
        segments = [s.strip() for s in part.split(";")]
        token = segments[0].lower()
        q = 1.0
        for param in segments[1:]:
            if param.lower().startswith("q="):
                try:
                    q = float(param[2:].strip())
                except ValueError:
                    q = 0.0
        if token in ("br", "gzip", "identity", "*"):
            q_values[token] = max(q_values.get(token, -1.0), q)

    br_q = q_values.get("br", 0.0)
    gzip_q = q_values.get("gzip", 0.0)

    if br_q <= 0.0 and gzip_q <= 0.0:
        return None
    if br_q >= gzip_q and br_q > 0.0:
        return "br"
    if gzip_q > 0.0:
        return "gzip"
    return None


def _extract_accept_encoding(scope: Scope) -> str:
    headers: list[tuple[bytes, bytes]] = scope.get("headers") or []
    parts: list[str] = []
    for key, val in headers:
        if key.lower() == b"accept-encoding":
            try:
                parts.append(val.decode("latin-1"))
            except Exception:  # noqa: S112
                continue
    return ", ".join(parts)


def _compress_bytes(body: bytes, encoding: str) -> bytes:
    if encoding == "br":
        compressor_cls: Any = brotli_asgi.Compressor  # type: ignore[attr-defined]
        mode_cls: Any = brotli_asgi.Mode
        compressor = compressor_cls(
            quality=4,
            mode=mode_cls.text,
            lgwin=22,
            lgblock=0,
        )
        return bytes(compressor.process(body) + compressor.finish())
    return gzip.compress(body)


def _build_compressed_headers(
    orig_headers: list[tuple[bytes, bytes]],
    encoding: str,
    compressed_length: int,
) -> list[tuple[bytes, bytes]]:
    new_headers: list[tuple[bytes, bytes]] = []
    vary_values: list[str] = []

    for k, v in orig_headers:
        k_lower = k.lower()
        if k_lower == b"content-length":
            continue
        if k_lower == b"vary":
            vary_values.append(v.decode("latin-1", errors="replace"))
            continue
        new_headers.append((k, v))

    new_headers.append((b"content-encoding", encoding.encode("latin-1")))
    new_headers.append((b"content-length", str(compressed_length).encode("latin-1")))

    existing_vary_tokens = {
        t.strip().lower()
        for raw_v in vary_values
        for t in raw_v.split(",")
        if t.strip()
    }
    if "accept-encoding" not in existing_vary_tokens:
        vary_values.append("Accept-Encoding")
    new_headers.append((b"vary", ", ".join(vary_values).encode("latin-1")))
    return new_headers


def _should_bypass_response(
    method: str,
    status: int,
    raw_headers: list[tuple[bytes, bytes]],
) -> bool:
    has_existing_encoding = any(
        k.lower() == b"content-encoding" for k, _ in raw_headers
    )
    return (
        method == "HEAD"
        or status < 200
        or status in (204, 304)
        or has_existing_encoding
    )


class CompressionMiddleware:
    """Pure ASGI compression middleware supporting Brotli ('br') and Gzip ('gzip')."""

    def __init__(
        self,
        app: ASGIApp,
        *,
        threshold: int = DEFAULT_COMPRESSION_THRESHOLD,
        minimum_size: int | None = None,
    ) -> None:
        self.app = app
        resolved = minimum_size if minimum_size is not None else threshold
        self.threshold = max(0, int(resolved))

    async def __call__(
        self,
        scope: Scope,
        receive: Receive,
        send: Send,
    ) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        chosen_encoding = _negotiate_encoding(_extract_accept_encoding(scope))
        if chosen_encoding is None:
            await self.app(scope, receive, send)
            return

        method = str(scope.get("method", "GET")).upper()
        start_message: Message | None = None
        body_chunks: list[bytes] = []
        passthrough = False

        async def send_wrapper(message: Message) -> None:
            nonlocal start_message, passthrough
            msg_type = message["type"]

            if msg_type == "http.response.start":
                status = int(message.get("status", 200))
                raw_headers: list[tuple[bytes, bytes]] = list(
                    message.get("headers") or []
                )
                if _should_bypass_response(method, status, raw_headers):
                    passthrough = True
                    await send(message)
                    return
                start_message = message
                return

            if msg_type == "http.response.body":
                if passthrough or start_message is None:
                    await send(message)
                    return

                chunk: bytes = message.get("body", b"") or b""
                if chunk:
                    body_chunks.append(chunk)
                if bool(message.get("more_body", False)):
                    return

                full_body = b"".join(body_chunks)
                if len(full_body) < self.threshold:
                    await send(start_message)
                    await send(
                        {
                            "type": "http.response.body",
                            "body": full_body,
                            "more_body": False,
                        }
                    )
                    return

                compressed = _compress_bytes(full_body, chosen_encoding)
                orig_headers = list(start_message.get("headers") or [])
                new_headers = _build_compressed_headers(
                    orig_headers,
                    chosen_encoding,
                    len(compressed),
                )
                await send({**start_message, "headers": new_headers})
                await send(
                    {
                        "type": "http.response.body",
                        "body": compressed,
                        "more_body": False,
                    }
                )
                return

            await send(message)

        await self.app(scope, receive, send_wrapper)


__all__ = ["DEFAULT_COMPRESSION_THRESHOLD", "CompressionMiddleware"]
