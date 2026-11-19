"""Ephemeral asyncio mock HTTP/1.1 and ClamAV TCP servers for E2E tests."""

from __future__ import annotations

import asyncio
import concurrent.futures
import gzip
import inspect
import json
import struct
import threading
import time
import zlib
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from http import HTTPStatus
from typing import Any, Literal
from urllib.parse import parse_qs, urlsplit

_EICAR_BYTES = (
    b"X5O!P%@AP[4\\PZX54(P^)7CC)7}$" + b"EICAR-STANDARD-ANTIVIRUS-TEST-FILE!$H+H*"
)

ClamAvBehavior = Literal["auto", "clean", "infected", "error", "drop", "hang"]


class _BadRequestError(Exception):
    """Raised on malformed HTTP framing (e.g., invalid Content-Length)."""


@dataclass(frozen=True, slots=True)
class RecordedRequest:
    """Captured HTTP/1.1 request received by :class:`MockHttpServer`."""

    method: str
    path: str
    raw_target: str
    query: dict[str, list[str]]
    headers: dict[str, str]
    raw_body: bytes
    body_bytes: bytes
    body_text: str
    json_body: Any | None
    form_body: dict[str, str] | None
    received_at_ms: int
    monotonic_ms: float


@dataclass(slots=True)
class ScriptedResponse:
    """Configurable response specification for :class:`MockHttpServer`."""

    status: int = 200
    body: bytes | str | dict[str, Any] | list[Any] | None = None
    headers: dict[str, str] = field(default_factory=dict)
    delay_ms: int = 0
    hang: bool = False
    drop_connection: bool = False
    handler: (
        Callable[[RecordedRequest], ScriptedResponse | Awaitable[ScriptedResponse]]
        | None
    ) = None


def _decompress_body(raw_body: bytes, encoding: str) -> bytes:
    enc = encoding.lower().strip()
    if not raw_body or not enc:
        return raw_body
    if "gzip" in enc:
        try:
            return gzip.decompress(raw_body)
        except OSError:
            return raw_body
    if "deflate" in enc:
        try:
            return zlib.decompress(raw_body)
        except zlib.error:
            return raw_body
    return raw_body


def _parse_json_or_none(body_bytes: bytes, content_type: str) -> Any | None:
    if not body_bytes:
        return None
    ct = content_type.lower()
    stripped = body_bytes.lstrip()
    if "json" not in ct and not stripped.startswith((b"{", b"[")):
        return None
    try:
        return json.loads(body_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None


def _parse_form_or_none(
    body_bytes: bytes,
    content_type: str,
) -> dict[str, str] | None:
    if "application/x-www-form-urlencoded" not in content_type.lower():
        return None
    text = body_bytes.decode("utf-8", errors="replace")
    parsed = parse_qs(text, keep_blank_values=True)
    return {k: (vals[0] if vals else "") for k, vals in parsed.items()}


def _build_recorded_request(
    method: str,
    raw_target: str,
    headers: dict[str, str],
    raw_body: bytes,
) -> RecordedRequest:
    split = urlsplit(raw_target)
    path = split.path or "/"
    query = parse_qs(split.query, keep_blank_values=True)
    body_bytes = _decompress_body(raw_body, headers.get("content-encoding", ""))
    body_text = body_bytes.decode("utf-8", errors="replace")
    ct = headers.get("content-type", "")
    return RecordedRequest(
        method=method.upper(),
        path=path,
        raw_target=raw_target,
        query=query,
        headers=headers,
        raw_body=raw_body,
        body_bytes=body_bytes,
        body_text=body_text,
        json_body=_parse_json_or_none(body_bytes, ct),
        form_body=_parse_form_or_none(body_bytes, ct),
        received_at_ms=time.time_ns() // 1_000_000,
        monotonic_ms=time.monotonic() * 1000.0,
    )


def _reason_phrase(status: int) -> str:
    try:
        return HTTPStatus(status).phrase
    except ValueError:
        return "Status"


def _serialize_response_body(
    resp: ScriptedResponse,
) -> tuple[bytes, dict[str, str]]:
    hdrs = {k: v for k, v in resp.headers.items()}
    lower_keys = {k.lower() for k in hdrs}
    body = resp.body
    if body is None:
        payload = b""
    elif isinstance(body, dict | list):
        payload = json.dumps(body).encode("utf-8")
        if "content-type" not in lower_keys:
            hdrs["Content-Type"] = "application/json"
    elif isinstance(body, str):
        payload = body.encode("utf-8")
        if "content-type" not in lower_keys:
            hdrs["Content-Type"] = "text/plain; charset=utf-8"
    else:
        payload = bytes(body)
        if "content-type" not in lower_keys:
            hdrs["Content-Type"] = "application/octet-stream"
    return payload, hdrs


class MockHttpServer:
    """Local pure-stdlib asyncio HTTP/1.1 mock server for edge & conformance tests."""

    def __init__(self, host: str = "127.0.0.1", port: int = 0) -> None:
        self._host = host
        self._port = port
        self._server: asyncio.AbstractServer | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._cond: asyncio.Condition | None = None
        self._requests: list[RecordedRequest] = []
        self._scripted_exact: dict[str, list[ScriptedResponse]] = {}
        self._scripted_prefix: list[tuple[str, list[ScriptedResponse]]] = []
        self._hcaptcha_secret: str | None = None
        self._hcaptcha_valid_tokens: set[str] | None = None
        self._hcaptcha_invalid_tokens: set[str] = {
            "invalid-token",
            "bad-token",
            "fail",
        }
        self._client_tasks: set[asyncio.Task[None]] = set()

    @property
    def host(self) -> str:
        """Bound host address."""
        return self._host

    @property
    def port(self) -> int:
        """Bound TCP port."""
        return self._port

    @property
    def base_url(self) -> str:
        """Base HTTP URL for this server."""
        return f"http://{self._host}:{self._port}"

    def url(self, path: str) -> str:
        """Build an absolute URL for ``path``."""
        if not path.startswith("/"):
            path = f"/{path}"
        return f"{self.base_url}{path}"

    async def start(self) -> None:
        """Start listening on ``host:port``."""
        if self._server is not None:
            return
        self._loop = asyncio.get_running_loop()
        self._cond = asyncio.Condition()
        self._server = await asyncio.start_server(
            self._accept_client,
            self._host,
            self._port,
        )
        sockets = self._server.sockets
        if sockets:
            addr = sockets[0].getsockname()
            self._port = int(addr[1])

    async def close(self) -> None:
        """Stop listening and close active connections."""
        if self._server is None:
            return
        self._server.close()
        for task in list(self._client_tasks):
            task.cancel()
        if self._client_tasks:
            await asyncio.gather(*self._client_tasks, return_exceptions=True)
        await self._server.wait_closed()
        self._server = None

    def start_in_thread(self) -> None:
        """Start the mock server on a dedicated background daemon thread."""
        if self._thread is not None:
            return
        ready: concurrent.futures.Future[None] = concurrent.futures.Future()

        def _runner() -> None:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                loop.run_until_complete(self.start())
                ready.set_result(None)
                loop.run_forever()
            except Exception as exc:
                if not ready.done():
                    ready.set_exception(exc)
            finally:
                loop.run_until_complete(self.close())
                loop.close()

        self._thread = threading.Thread(target=_runner, daemon=True)
        self._thread.start()
        ready.result(timeout=10.0)

    def close_in_thread(self) -> None:
        """Shut down the background thread mock server."""
        if self._thread is None or self._loop is None:
            return
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._thread.join(timeout=5.0)
        self._thread = None
        self._loop = None

    async def __aenter__(self) -> MockHttpServer:
        await self.start()
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.close()

    def script(self, path: str, responses: Sequence[ScriptedResponse]) -> None:
        """Register an ordered FIFO response queue for an exact ``path``."""
        self._scripted_exact[path] = list(responses)

    def script_prefix(
        self,
        prefix: str,
        responses: Sequence[ScriptedResponse],
    ) -> None:
        """Register an ordered FIFO response queue for paths matching ``prefix``."""
        self._scripted_prefix = [
            (p, q) for (p, q) in self._scripted_prefix if p != prefix
        ]
        self._scripted_prefix.append((prefix, list(responses)))

    def configure_hcaptcha(
        self,
        *,
        valid_secret: str | None = None,
        valid_tokens: set[str] | None = None,
        invalid_tokens: set[str] | None = None,
    ) -> None:
        """Configure built-in ``/siteverify`` token and secret expectations."""
        self._hcaptcha_secret = valid_secret
        self._hcaptcha_valid_tokens = (
            set(valid_tokens) if valid_tokens is not None else None
        )
        if invalid_tokens is not None:
            self._hcaptcha_invalid_tokens = set(invalid_tokens)

    def reset(self) -> None:
        """Clear recorded requests, scripted responses, and hCaptcha overrides."""
        self._requests.clear()
        self._scripted_exact.clear()
        self._scripted_prefix.clear()
        self._hcaptcha_secret = None
        self._hcaptcha_valid_tokens = None
        self._hcaptcha_invalid_tokens = {"invalid-token", "bad-token", "fail"}

    def recorded(
        self,
        path: str | None = None,
        *,
        path_prefix: str | None = None,
    ) -> list[RecordedRequest]:
        """Return captured requests matching ``path`` or ``path_prefix``."""
        items = list(self._requests)
        if path is not None:
            items = [r for r in items if r.path == path]
        if path_prefix is not None:
            items = [r for r in items if r.path.startswith(path_prefix)]
        return items

    def otlp_contains(self, needle: str) -> bool:
        """Return ``True`` if any OTLP ``/v1/traces`` body contains ``needle``."""
        for req in self._requests:
            if req.path in ("/v1/traces", "/otlp/v1/traces"):
                decoded = req.body_bytes.decode("utf-8", errors="ignore")
                if needle in decoded:
                    return True
        return False

    async def wait_for_requests(
        self,
        count: int,
        *,
        path: str | None = None,
        path_prefix: str | None = None,
        timeout: float = 10.0,  # noqa: ASYNC109
    ) -> list[RecordedRequest]:
        """Wait until at least ``count`` matching requests are recorded."""
        if self._cond is None:
            msg = "MockHttpServer has not been started"
            raise RuntimeError(msg)

        async def _wait() -> list[RecordedRequest]:
            assert self._cond is not None
            async with self._cond:
                while True:
                    matched = self.recorded(path=path, path_prefix=path_prefix)
                    if len(matched) >= count:
                        return matched
                    await self._cond.wait()

        return await asyncio.wait_for(_wait(), timeout=timeout)

    def wait_for_requests_sync(
        self,
        count: int,
        *,
        path: str | None = None,
        path_prefix: str | None = None,
        timeout: float = 10.0,
    ) -> list[RecordedRequest]:
        """Thread-safe sync wait when server runs via :meth:`start_in_thread`."""
        if self._loop is None:
            msg = "MockHttpServer is not running"
            raise RuntimeError(msg)
        fut = asyncio.run_coroutine_threadsafe(
            self.wait_for_requests(
                count,
                path=path,
                path_prefix=path_prefix,
                timeout=timeout,
            ),
            self._loop,
        )
        return fut.result(timeout=timeout + 1.0)

    def _accept_client(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        task = asyncio.create_task(self._handle_client(reader, writer))
        self._client_tasks.add(task)
        task.add_done_callback(self._client_tasks.discard)

    async def _read_headers(
        self,
        reader: asyncio.StreamReader,
    ) -> tuple[str, str, dict[str, str]] | None:
        try:
            raw_head = await reader.readuntil(b"\r\n\r\n")
        except (
            asyncio.IncompleteReadError,
            asyncio.LimitOverrunError,
            ConnectionError,
        ):
            return None
        header_text = raw_head[:-4].decode("latin-1")
        lines = header_text.split("\r\n")
        if not lines or not lines[0].strip():
            return None
        parts = lines[0].split(" ", 2)
        if len(parts) < 2:
            return None
        method, raw_target = parts[0], parts[1]
        headers: dict[str, str] = {}
        for line in lines[1:]:
            if ":" not in line:
                continue
            name, val = line.split(":", 1)
            lname = name.strip().lower()
            vstr = val.strip()
            if lname in headers:
                headers[lname] = f"{headers[lname]}, {vstr}"
            else:
                headers[lname] = vstr
        return method, raw_target, headers

    async def _read_chunked_body(self, reader: asyncio.StreamReader) -> bytes:
        chunks: list[bytes] = []
        while True:
            size_line = await reader.readuntil(b"\r\n")
            size_hex = size_line[:-2].split(b";", 1)[0].strip()
            try:
                chunk_len = int(size_hex, 16)
            except ValueError as exc:
                raise _BadRequestError("Invalid chunk length") from exc
            if chunk_len < 0:
                raise _BadRequestError("Negative chunk length")
            if chunk_len == 0:
                while True:
                    trailer = await reader.readuntil(b"\r\n")
                    if trailer == b"\r\n":
                        break
                break
            chunks.append(await reader.readexactly(chunk_len))
            await reader.readexactly(2)
        return b"".join(chunks)

    async def _read_body(
        self,
        reader: asyncio.StreamReader,
        headers: dict[str, str],
    ) -> bytes:
        if "chunked" in headers.get("transfer-encoding", "").lower():
            return await self._read_chunked_body(reader)
        if "content-length" in headers:
            try:
                length = int(headers["content-length"])
            except ValueError as exc:
                raise _BadRequestError("Invalid Content-Length") from exc
            if length < 0:
                raise _BadRequestError("Negative Content-Length")
            return await reader.readexactly(length) if length > 0 else b""
        return b""

    def _pop_scripted(self, path: str) -> ScriptedResponse | None:
        queue = self._scripted_exact.get(path)
        if queue:
            return queue.pop(0) if len(queue) > 1 else queue[0]
        for prefix, pqueue in self._scripted_prefix:
            if path.startswith(prefix) and pqueue:
                return pqueue.pop(0) if len(pqueue) > 1 else pqueue[0]
        return None

    def _handle_hcaptcha(self, req: RecordedRequest) -> ScriptedResponse:
        form = req.form_body or {}
        secret = form.get("secret", "")
        token = form.get("response", "")
        if not secret:
            return ScriptedResponse(
                status=200,
                body={"success": False, "error-codes": ["missing-input-secret"]},
            )
        if self._hcaptcha_secret is not None and secret != self._hcaptcha_secret:
            return ScriptedResponse(
                status=200,
                body={"success": False, "error-codes": ["invalid-input-secret"]},
            )
        if not token:
            return ScriptedResponse(
                status=200,
                body={"success": False, "error-codes": ["missing-input-response"]},
            )
        if token in self._hcaptcha_invalid_tokens or (
            self._hcaptcha_valid_tokens is not None
            and token not in self._hcaptcha_valid_tokens
        ):
            return ScriptedResponse(
                status=200,
                body={"success": False, "error-codes": ["invalid-input-response"]},
            )
        return ScriptedResponse(
            status=200,
            body={
                "success": True,
                "challenge_ts": "2026-11-18T20:00:00Z",
                "hostname": "127.0.0.1",
            },
        )

    def _resolve_builtin(self, req: RecordedRequest) -> ScriptedResponse:
        path = req.path
        if path in ("/v1/traces", "/otlp/v1/traces"):
            return ScriptedResponse(status=200, body={"partialSuccess": {}})
        if path in ("/siteverify", "/hcaptcha/siteverify"):
            return self._handle_hcaptcha(req)
        if path == "/slack" or path.startswith("/services/"):
            return ScriptedResponse(status=200, body="ok")
        if path == "/ok":
            return ScriptedResponse(status=200, body={"ok": True})
        if path.startswith("/status/"):
            tail = path[len("/status/") :].split("/", 1)[0]
            if tail.isdigit():
                code = int(tail)
                return ScriptedResponse(status=code, body={"status": code})
        if path == "/echo":
            return ScriptedResponse(
                status=200,
                body={
                    "method": req.method,
                    "path": req.path,
                    "query": req.query,
                    "headers": req.headers,
                    "body": req.body_text,
                    "json": req.json_body,
                },
            )
        if path == "/slow":
            raw_delay = req.query.get("delayMs", ["100"])[0]
            delay_ms = int(raw_delay) if raw_delay.isdigit() else 100
            return ScriptedResponse(
                status=200,
                body={"ok": True, "delayedMs": delay_ms},
                delay_ms=delay_ms,
            )
        if path == "/redirect":
            target = req.query.get("to", [f"{self.base_url}/ok"])[0]
            raw_code = req.query.get("code", ["302"])[0]
            code = int(raw_code) if raw_code.isdigit() else 302
            return ScriptedResponse(status=code, headers={"Location": target}, body="")
        if path == "/hang":
            return ScriptedResponse(hang=True)
        return ScriptedResponse(status=200, body={})

    async def _resolve_response(self, req: RecordedRequest) -> ScriptedResponse:
        resp = self._pop_scripted(req.path) or self._resolve_builtin(req)
        if resp.handler is not None:
            result = resp.handler(req)
            if inspect.isawaitable(result):
                resp = await result
            else:
                resp = result
        return resp

    async def _write_http_response(
        self,
        writer: asyncio.StreamWriter,
        resp: ScriptedResponse,
    ) -> None:
        payload, hdrs = _serialize_response_body(resp)
        lines = [f"HTTP/1.1 {resp.status} {_reason_phrase(resp.status)}"]
        lower_keys = {k.lower() for k in hdrs}
        for key, val in hdrs.items():
            lines.append(f"{key}: {val}")
        if "content-length" not in lower_keys:
            lines.append(f"Content-Length: {len(payload)}")
        if "connection" not in lower_keys:
            lines.append("Connection: keep-alive")
        raw_head = ("\r\n".join(lines) + "\r\n\r\n").encode("latin-1")
        writer.write(raw_head + payload)
        await writer.drain()

    async def _handle_client(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        try:
            while True:
                parsed = await self._read_headers(reader)
                if parsed is None:
                    break
                method, raw_target, headers = parsed
                raw_body = await self._read_body(reader, headers)
                req = _build_recorded_request(method, raw_target, headers, raw_body)
                self._requests.append(req)
                if self._cond is not None:
                    async with self._cond:
                        self._cond.notify_all()

                resp = await self._resolve_response(req)
                if resp.drop_connection:
                    break
                if resp.delay_ms > 0:
                    await asyncio.sleep(resp.delay_ms / 1000.0)
                if resp.hang:
                    await asyncio.Future()
                await self._write_http_response(writer, resp)
                if headers.get("connection", "").lower() == "close":
                    break
        except _BadRequestError:
            try:
                await self._write_http_response(
                    writer,
                    ScriptedResponse(
                        status=400,
                        headers={"Connection": "close"},
                        body={"error": "Bad Request"},
                    ),
                )
            except Exception:  # noqa: S110
                pass
        except (asyncio.CancelledError, ConnectionError, asyncio.IncompleteReadError):
            pass
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:  # noqa: S110
                pass


@dataclass(frozen=True, slots=True)
class RecordedScan:
    """Captured ClamAV ``zINSTREAM`` scan request."""

    command: str
    payload: bytes
    size: int
    chunk_sizes: list[int]
    response_sent: bytes
    received_at_ms: int


class MockClamAvServer:
    """Local pure-stdlib asyncio TCP mock implementing ClamAV ``zINSTREAM``."""

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 0,
        *,
        behavior: ClamAvBehavior = "auto",
        infected_signature: str = "Eicar-Test-Signature",
        error_message: str = "INSTREAM size limit exceeded. ERROR",
        keep_open: bool = True,
        delay_ms: int = 0,
        trigger_bytes: bytes = _EICAR_BYTES,
    ) -> None:
        self._host = host
        self._port = port
        self._behavior: ClamAvBehavior = behavior
        self._infected_signature = infected_signature
        self._error_message = error_message
        self._keep_open = keep_open
        self._delay_ms = delay_ms
        self._trigger_bytes = trigger_bytes
        self._scripted_behaviors: list[ClamAvBehavior] = []
        self._scans: list[RecordedScan] = []
        self._server: asyncio.AbstractServer | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._cond: asyncio.Condition | None = None
        self._client_tasks: set[asyncio.Task[None]] = set()

    @property
    def host(self) -> str:
        """Bound host address."""
        return self._host

    @property
    def port(self) -> int:
        """Bound TCP port."""
        return self._port

    @property
    def recorded_scans(self) -> list[RecordedScan]:
        """Return a snapshot of recorded ClamAV scan invocations."""
        return list(self._scans)

    def set_behavior(
        self,
        behavior: ClamAvBehavior,
        *,
        infected_signature: str | None = None,
        error_message: str | None = None,
        delay_ms: int | None = None,
    ) -> None:
        """Update default response behavior for subsequent scans."""
        self._behavior = behavior
        if infected_signature is not None:
            self._infected_signature = infected_signature
        if error_message is not None:
            self._error_message = error_message
        if delay_ms is not None:
            self._delay_ms = delay_ms

    def script_behaviors(self, behaviors: Sequence[ClamAvBehavior]) -> None:
        """Register an ordered FIFO queue of scan behaviors."""
        self._scripted_behaviors = list(behaviors)

    def reset(self) -> None:
        """Clear recorded scans and reset behavior to ``auto``."""
        self._scans.clear()
        self._scripted_behaviors.clear()
        self._behavior = "auto"
        self._delay_ms = 0

    async def start(self) -> None:
        """Start listening for ClamAV TCP connections."""
        if self._server is not None:
            return
        self._loop = asyncio.get_running_loop()
        self._cond = asyncio.Condition()
        self._server = await asyncio.start_server(
            self._accept_client,
            self._host,
            self._port,
        )
        sockets = self._server.sockets
        if sockets:
            addr = sockets[0].getsockname()
            self._port = int(addr[1])

    async def close(self) -> None:
        """Stop the TCP server and cancel open connections."""
        if self._server is None:
            return
        self._server.close()
        for task in list(self._client_tasks):
            task.cancel()
        if self._client_tasks:
            await asyncio.gather(*self._client_tasks, return_exceptions=True)
        await self._server.wait_closed()
        self._server = None

    def start_in_thread(self) -> None:
        """Start the ClamAV mock on a background daemon thread."""
        if self._thread is not None:
            return
        ready: concurrent.futures.Future[None] = concurrent.futures.Future()

        def _runner() -> None:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                loop.run_until_complete(self.start())
                ready.set_result(None)
                loop.run_forever()
            except Exception as exc:
                if not ready.done():
                    ready.set_exception(exc)
            finally:
                loop.run_until_complete(self.close())
                loop.close()

        self._thread = threading.Thread(target=_runner, daemon=True)
        self._thread.start()
        ready.result(timeout=10.0)

    def close_in_thread(self) -> None:
        """Stop the background thread ClamAV mock."""
        if self._thread is None or self._loop is None:
            return
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._thread.join(timeout=5.0)
        self._thread = None
        self._loop = None

    async def __aenter__(self) -> MockClamAvServer:
        await self.start()
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.close()

    async def wait_for_scans(
        self,
        count: int,
        *,
        timeout: float = 10.0,  # noqa: ASYNC109
    ) -> list[RecordedScan]:
        """Wait until at least ``count`` scans have been processed."""
        if self._cond is None:
            msg = "MockClamAvServer has not been started"
            raise RuntimeError(msg)

        async def _wait() -> list[RecordedScan]:
            assert self._cond is not None
            async with self._cond:
                while len(self._scans) < count:
                    await self._cond.wait()
                return list(self._scans)

        return await asyncio.wait_for(_wait(), timeout=timeout)

    def _accept_client(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        task = asyncio.create_task(self._handle_client(reader, writer))
        self._client_tasks.add(task)
        task.add_done_callback(self._client_tasks.discard)

    def _next_behavior(self) -> ClamAvBehavior:
        if self._scripted_behaviors:
            if len(self._scripted_behaviors) > 1:
                return self._scripted_behaviors.pop(0)
            return self._scripted_behaviors[0]
        return self._behavior

    def _build_response_bytes(self, behavior: ClamAvBehavior, payload: bytes) -> bytes:
        if behavior == "clean":
            return b"stream: OK\0"
        if behavior == "infected":
            return f"stream: {self._infected_signature} FOUND\0".encode()
        if behavior == "error":
            return f"{self._error_message}\0".encode()
        if self._trigger_bytes in payload:
            return f"stream: {self._infected_signature} FOUND\0".encode()
        return b"stream: OK\0"

    async def _read_instream_payload(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> tuple[bytes, list[int]] | None:
        payload_chunks: list[bytes] = []
        chunk_sizes: list[int] = []
        while True:
            len_bytes = await reader.readexactly(4)
            (chunk_len,) = struct.unpack(">I", len_bytes)
            if chunk_len == 0:
                return (b"".join(payload_chunks), chunk_sizes)
            if chunk_len > 50_000_000:
                writer.write(b"INSTREAM size limit exceeded. ERROR\0")
                await writer.drain()
                return None
            chunk = await reader.readexactly(chunk_len)
            payload_chunks.append(chunk)
            chunk_sizes.append(chunk_len)

    async def _record_scan(
        self,
        cmd: str,
        payload: bytes,
        chunk_sizes: list[int],
        response_sent: bytes,
    ) -> None:
        self._scans.append(
            RecordedScan(
                command=cmd,
                payload=payload,
                size=len(payload),
                chunk_sizes=chunk_sizes,
                response_sent=response_sent,
                received_at_ms=time.time_ns() // 1_000_000,
            )
        )
        if self._cond is not None:
            async with self._cond:
                self._cond.notify_all()

    async def _handle_client(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        try:
            raw_cmd = await reader.readuntil(b"\x00")
            if raw_cmd != b"zINSTREAM\x00":
                writer.write(b"UNKNOWN COMMAND\0")
                await writer.drain()
                return

            parsed = await self._read_instream_payload(reader, writer)
            if parsed is None:
                return
            payload, chunk_sizes = parsed

            behavior = self._next_behavior()
            if self._delay_ms > 0:
                await asyncio.sleep(self._delay_ms / 1000.0)

            if behavior == "drop":
                await self._record_scan("zINSTREAM", payload, chunk_sizes, b"")
                return
            if behavior == "hang":
                await self._record_scan("zINSTREAM", payload, chunk_sizes, b"")
                await asyncio.Future()

            resp = self._build_response_bytes(behavior, payload)
            writer.write(resp)
            await writer.drain()
            await self._record_scan("zINSTREAM", payload, chunk_sizes, resp)
            if self._keep_open:
                await reader.read(1)
        except (
            asyncio.CancelledError,
            ConnectionError,
            asyncio.IncompleteReadError,
            asyncio.LimitOverrunError,
        ):
            pass
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:  # noqa: S110
                pass


__all__ = [
    "ClamAvBehavior",
    "MockClamAvServer",
    "MockHttpServer",
    "RecordedRequest",
    "RecordedScan",
    "ScriptedResponse",
]
