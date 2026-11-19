from __future__ import annotations

import asyncio
import re
import struct
from dataclasses import dataclass
from typing import Literal, Protocol

ScanStatus = Literal["clean", "infected", "error"]


@dataclass(slots=True)
class FileScanResult:
    status: ScanStatus
    signature: str | None = None
    message: str | None = None


class FileScanner(Protocol):
    @property
    def is_scanning_enabled(self) -> bool: ...

    async def scan(self, buffer: bytes, filename: str) -> FileScanResult: ...


class NoopFileScanner:
    @property
    def is_scanning_enabled(self) -> bool:
        return False

    async def scan(self, buffer: bytes, filename: str) -> FileScanResult:
        return FileScanResult(status="clean")


DEFAULT_CHUNK_SIZE = 64 * 1024


class ClamAvFileScanner:
    """ClamAV daemon scanner via the INSTREAM TCP protocol."""

    def __init__(
        self,
        host: str,
        port: int,
        timeout_ms: int = 30_000,
        chunk_size: int = DEFAULT_CHUNK_SIZE,
    ) -> None:
        self.host = host
        self.port = port
        self.timeout_ms = timeout_ms
        self.chunk_size = chunk_size

    @property
    def is_scanning_enabled(self) -> bool:
        return True

    async def scan(self, buffer: bytes, filename: str) -> FileScanResult:
        try:
            response = await self._instream(buffer)
            normalized = response.replace("\0", "").strip()

            if re.search(r"^stream:\s*OK$", normalized, re.I | re.M) or re.search(
                r"\bstream:\s*OK\b", normalized, re.I
            ):
                return FileScanResult(status="clean")

            match = re.search(r"stream:\s*([^\n]+?)\s+FOUND", normalized, re.I)
            if match and match.group(1):
                return FileScanResult(
                    status="infected", signature=match.group(1).strip()
                )

            if "FOUND" in normalized.upper():
                return FileScanResult(status="infected", signature=normalized)

            if "ERROR" in normalized.upper():
                return FileScanResult(status="error", message=normalized)

            return FileScanResult(
                status="error", message=f"Unexpected ClamAV response: {normalized}"
            )
        except Exception as exc:
            return FileScanResult(status="error", message=str(exc))

    async def _instream(self, buffer: bytes) -> str:
        timeout_sec = max(0.1, self.timeout_ms / 1000.0)

        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(self.host, self.port),
            timeout=timeout_sec,
        )

        try:
            # ClamAV INSTREAM command: zINSTREAM\0
            writer.write(b"zINSTREAM\0")
            await writer.drain()

            # Stream in chunks: [4-byte big-endian length][chunk data]
            offset = 0
            total = len(buffer)
            while offset < total:
                chunk = buffer[offset : offset + self.chunk_size]
                chunk_len = len(chunk)
                writer.write(struct.pack("!I", chunk_len) + chunk)
                await writer.drain()
                offset += chunk_len

            # Terminate stream: [4-byte length = 0]
            writer.write(struct.pack("!I", 0))
            await writer.drain()

            response_data = await asyncio.wait_for(
                reader.read(4096),
                timeout=timeout_sec,
            )
            return response_data.decode("utf-8", errors="replace")
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:  # noqa: S110
                pass
