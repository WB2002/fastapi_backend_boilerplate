from __future__ import annotations

import asyncio
import os
from collections.abc import Mapping, Sequence
from typing import Any, Final
from urllib.parse import urlparse

from beanie import Document, init_beanie
from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase

from app.core.logger.logger_config import get_logger

DEFAULT_MONGO_URI: Final[str] = "mongodb://localhost:27017/nestjs-boilerplate"
DEFAULT_MONGO_DB_NAME: Final[str] = "nestjs-boilerplate"
DEFAULT_SERVER_SELECTION_TIMEOUT_MS: Final[int] = 2_000
DEFAULT_CONNECT_TIMEOUT_MS: Final[int] = 2_000


def extract_database_name(
    mongo_uri: str,
    default_db_name: str = DEFAULT_MONGO_DB_NAME,
) -> str:
    """Extract database name from a MongoDB URI, falling back to `default_db_name`."""
    try:
        parsed = urlparse(mongo_uri)
        path_db = parsed.path.lstrip("/").split("/", 1)[0].strip()
        if path_db:
            return path_db
    except Exception:  # noqa: S110
        pass
    return default_db_name


class MongoService:
    """Lazy Motor + Beanie ODM wrapper with 2000ms timeouts and non-blocking boot."""

    def __init__(
        self,
        mongo_uri: str | None = None,
        *,
        env: Mapping[str, str | None] | None = None,
        document_models: Sequence[type[Document]] | None = None,
        server_selection_timeout_ms: int = DEFAULT_SERVER_SELECTION_TIMEOUT_MS,
        connect_timeout_ms: int = DEFAULT_CONNECT_TIMEOUT_MS,
        client_factory: Any = None,
    ) -> None:
        target = os.environ if env is None else env
        resolved_uri = (
            mongo_uri
            if mongo_uri is not None
            else (target.get("MONGO_URI") or DEFAULT_MONGO_URI)
        )
        self.mongo_uri: str = resolved_uri
        self.database_name: str = extract_database_name(resolved_uri)
        self.server_selection_timeout_ms: int = server_selection_timeout_ms
        self.connect_timeout_ms: int = connect_timeout_ms
        self._document_models: list[type[Document]] = list(document_models or [])
        self._client_factory = client_factory or AsyncIOMotorClient
        self._client: AsyncIOMotorClient[Any] | None = None
        self._beanie_initialized = False
        self._beanie_lock = asyncio.Lock()
        self._closed = False
        self._logger = get_logger("MongoService")

    @property
    def is_connected(self) -> bool:
        return self._client is not None and not self._closed

    @property
    def is_beanie_initialized(self) -> bool:
        return self._beanie_initialized

    @property
    def is_closed(self) -> bool:
        return self._closed

    @property
    def document_models(self) -> tuple[type[Document], ...]:
        return tuple(self._document_models)

    def register_document_models(
        self,
        models: Sequence[type[Document]],
    ) -> None:
        for model in models:
            if model not in self._document_models:
                self._document_models.append(model)
                self._beanie_initialized = False

    def on_module_init(self) -> AsyncIOMotorClient[Any]:
        """Construct the Motor client lazily without awaiting network I/O."""
        return self.get_client()

    def get_client(self) -> AsyncIOMotorClient[Any]:
        """Return (or lazily instantiate) the non-blocking Motor client."""
        if self._closed:
            raise RuntimeError("MongoService client has been closed")
        if self._client is None:
            self._client = self._client_factory(
                self.mongo_uri,
                serverSelectionTimeoutMS=self.server_selection_timeout_ms,
                connectTimeoutMS=self.connect_timeout_ms,
            )
        return self._client

    def get_database(self, name: str | None = None) -> AsyncIOMotorDatabase[Any]:
        """Return the Motor database handle for `name` (or URI database)."""
        client = self.get_client()
        db_name = name or self.database_name
        return client[db_name]

    async def ensure_beanie_initialized(
        self,
        document_models: Sequence[type[Document]] | None = None,
    ) -> bool:
        """Initialize Beanie ODM lazily on first use; returns False if unreachable."""
        if document_models:
            self.register_document_models(document_models)
        if self._beanie_initialized:
            return True
        if not self._document_models:
            self._beanie_initialized = True
            return True
        async with self._beanie_lock:
            if self._beanie_initialized:
                return True
            try:
                db = self.get_database()
                await init_beanie(
                    database=db,  # type: ignore[arg-type]
                    document_models=list(self._document_models),
                )
                self._beanie_initialized = True
                return True
            except Exception as exc:
                self._logger.warn(f"Lazy Beanie initialization deferred: {exc}")
                return False

    async def ping(self) -> bool:
        """Ping MongoDB admin database (`{ ping: 1 }`) within 2000ms timeout."""
        client = self.get_client()
        timeout_sec = max(0.1, self.server_selection_timeout_ms / 1000.0)
        result = await asyncio.wait_for(
            client.admin.command("ping"),
            timeout=timeout_sec,
        )
        if isinstance(result, Mapping):
            return bool(result.get("ok"))
        return True

    async def close(self) -> None:
        """Abort in-flight/pending MongoDB connections during SIGTERM before_close."""
        self._closed = True
        self._beanie_initialized = False
        client = self._client
        self._client = None
        if client is None:
            return
        try:
            close_fn = getattr(client, "close", None)
            if callable(close_fn):
                res = close_fn()
                if asyncio.iscoroutine(res):
                    await res
        except Exception as exc:
            self._logger.warn(f"Failed to close MongoDB client: {exc}")

    async def destroy(self) -> None:
        """Alias for `close()` matching Mongoose `mongoose.connection.destroy()`."""
        await self.close()

    async def on_module_destroy(self) -> None:
        """Lifecycle hook matching NestJS `OnModuleDestroy`."""
        await self.close()


__all__ = [
    "DEFAULT_CONNECT_TIMEOUT_MS",
    "DEFAULT_MONGO_DB_NAME",
    "DEFAULT_MONGO_URI",
    "DEFAULT_SERVER_SELECTION_TIMEOUT_MS",
    "MongoService",
    "extract_database_name",
]
