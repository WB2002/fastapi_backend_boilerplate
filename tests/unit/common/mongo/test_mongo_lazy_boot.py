from __future__ import annotations

import time
from typing import Any

import pytest

from app.common.mongo import (
    DEFAULT_CONNECT_TIMEOUT_MS,
    DEFAULT_SERVER_SELECTION_TIMEOUT_MS,
    MongoService,
    extract_database_name,
)
from app.main import create_app


class _FakeAdmin:
    def __init__(self, should_fail: bool = False) -> None:
        self.should_fail = should_fail
        self.commands: list[str] = []

    async def command(self, cmd: str) -> dict[str, Any]:
        self.commands.append(cmd)
        if self.should_fail:
            raise ConnectionError("MongoDB down")
        return {"ok": 1}


class _FakeMotorClient:
    def __init__(self, uri: str, **kwargs: Any) -> None:
        self.uri = uri
        self.kwargs = kwargs
        self.closed = False
        self.admin = _FakeAdmin()
        self.dbs: dict[str, Any] = {}

    def __getitem__(self, name: str) -> Any:
        if name not in self.dbs:
            self.dbs[name] = {"name": name}
        return self.dbs[name]

    def close(self) -> None:
        self.closed = True


def test_extract_database_name() -> None:
    assert (
        extract_database_name("mongodb://mongo:27017/nestjs-boilerplate")
        == "nestjs-boilerplate"
    )
    assert (
        extract_database_name(
            "mongodb://localhost:27017/custom_db?directConnection=true"
        )
        == "custom_db"
    )
    assert extract_database_name("mongodb://localhost:27017/") == "nestjs-boilerplate"


def test_mongo_lazy_boot_does_not_block_when_unreachable(
    valid_base_env: dict[str, str],
) -> None:
    assert DEFAULT_SERVER_SELECTION_TIMEOUT_MS == 2000
    assert DEFAULT_CONNECT_TIMEOUT_MS == 2000

    # Point MONGO_URI to an unreachable port; startup must be instant and non-blocking
    env = {
        **valid_base_env,
        "MONGO_URI": "mongodb://127.0.0.1:1/unreachable-db",
    }
    started = time.monotonic()
    app = create_app(env=env)
    elapsed_ms = (time.monotonic() - started) * 1000.0

    assert elapsed_ms < 1000.0
    mongo_svc: MongoService = app.state.mongo_service
    assert mongo_svc.mongo_uri == "mongodb://127.0.0.1:1/unreachable-db"
    assert mongo_svc.database_name == "unreachable-db"
    assert mongo_svc.server_selection_timeout_ms == 2000
    assert mongo_svc.connect_timeout_ms == 2000


async def test_mongo_service_client_lifecycle_and_ping() -> None:
    svc = MongoService(
        "mongodb://localhost:27017/my_app",
        client_factory=_FakeMotorClient,
    )
    assert svc.is_connected is False

    client = svc.on_module_init()
    assert svc.is_connected is True
    assert isinstance(client, _FakeMotorClient)
    assert client.kwargs["serverSelectionTimeoutMS"] == 2000
    assert client.kwargs["connectTimeoutMS"] == 2000

    db = svc.get_database()
    assert db == {"name": "my_app"}

    assert await svc.ping() is True
    assert client.admin.commands == ["ping"]

    await svc.close()
    assert svc.is_closed is True
    assert client.closed is True

    with pytest.raises(RuntimeError, match="MongoService client has been closed"):
        svc.get_client()


async def test_mongo_service_ping_failure_propagates() -> None:
    def _failing_factory(uri: str, **kwargs: Any) -> _FakeMotorClient:
        c = _FakeMotorClient(uri, **kwargs)
        c.admin.should_fail = True
        return c

    svc = MongoService(
        "mongodb://localhost:27017/my_app",
        client_factory=_failing_factory,
    )
    with pytest.raises(ConnectionError, match="MongoDB down"):
        await svc.ping()
    await svc.destroy()
