"""Pytest fixtures for opaque-box E2E tests (zero app.* imports)."""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable, Iterator, Mapping
from pathlib import Path
from typing import Any, Literal, TypeVar, cast

import httpx
import pytest
import pytest_asyncio

from tests.e2e.helpers.containers import (
    BackingServices,
    apply_postgres_migrations_sync,
    flush_all,
)
from tests.e2e.helpers.env import RsaKeyPair, build_base_env, generate_rsa_keypair
from tests.e2e.helpers.geoip import get_geoip_mmdb_path
from tests.e2e.helpers.mocks import MockClamAvServer, MockHttpServer
from tests.e2e.helpers.server import LiveAppServer

_F = TypeVar("_F", bound=Callable[..., Any])


def _sync_fixture(
    *,
    scope: Literal["session", "function"] = "function",
) -> Callable[[_F], _F]:
    return cast("Callable[[_F], _F]", pytest.fixture(scope=scope))


def _async_fixture(
    *,
    scope: Literal["session", "function"] = "function",
) -> Callable[[_F], _F]:
    return cast("Callable[[_F], _F]", pytest_asyncio.fixture(scope=scope))


@_sync_fixture(scope="session")
def rsa_keypair() -> RsaKeyPair:
    """Session-scoped 2048-bit RS256 keypair."""
    return generate_rsa_keypair()


@_sync_fixture(scope="session")
def geoip_mmdb_path(tmp_path_factory: Any) -> Path:
    """Session-scoped valid GeoLite2-Country MMDB v2 fixture path."""
    dest_dir = Path(tmp_path_factory.mktemp("geoip"))
    return get_geoip_mmdb_path(dest_dir)


@_sync_fixture(scope="session")
def backing_services() -> Iterator[BackingServices]:
    """Session-scoped PostgreSQL, MongoDB, and Redis backing services."""
    services = BackingServices.start()
    try:
        apply_postgres_migrations_sync(services)
        yield services
    finally:
        services.stop()


@_async_fixture(scope="function")
async def clean_stores(backing_services: BackingServices) -> AsyncIterator[None]:
    """Flush PostgreSQL, MongoDB, and Redis before and after each test."""
    await flush_all(backing_services)
    try:
        yield
    finally:
        await flush_all(backing_services)


@_async_fixture(scope="function")
async def mock_http_server() -> AsyncIterator[MockHttpServer]:
    """Function-scoped ephemeral mock HTTP/1.1 upstream server."""
    async with MockHttpServer() as srv:
        yield srv


@_async_fixture(scope="function")
async def mock_clamav_server() -> AsyncIterator[MockClamAvServer]:
    """Function-scoped ephemeral mock ClamAV TCP ``zINSTREAM`` server."""
    async with MockClamAvServer() as srv:
        yield srv


@_sync_fixture(scope="function")
def base_env(
    rsa_keypair: RsaKeyPair,
    geoip_mmdb_path: Path,
    backing_services: BackingServices,
) -> dict[str, str]:
    """Function-scoped valid 103-variable test environment dictionary."""
    return build_base_env(
        services=backing_services,
        keypair=rsa_keypair,
        geoip_db_path=geoip_mmdb_path,
        node_env="test",
        conformance_enabled=True,
    )


LiveServerFactory = Callable[..., Awaitable[LiveAppServer]]


@_async_fixture(scope="function")
async def make_live_server(
    base_env: dict[str, str],
    clean_stores: None,
) -> AsyncIterator[LiveServerFactory]:
    """Factory fixture launching isolated :class:`LiveAppServer` instances."""
    started: list[LiveAppServer] = []

    async def _factory(
        overrides: Mapping[str, str | None] | None = None,
        **kwargs: Any,
    ) -> LiveAppServer:
        merged: dict[str, str | None] = dict(base_env)
        if overrides is not None:
            for key, val in overrides.items():
                if val is None:
                    merged.pop(key, None)
                else:
                    merged[key] = str(val)
        server = LiveAppServer(merged, **kwargs)
        started.append(server)
        await server.start()
        return server

    try:
        yield _factory
    finally:
        for server in reversed(started):
            try:
                await server.stop()
            except Exception:  # noqa: S110
                pass


@_async_fixture(scope="function")
async def live_server(
    make_live_server: LiveServerFactory,
) -> LiveAppServer:
    """Function-scoped default :class:`LiveAppServer` instance."""
    return await make_live_server()


@_async_fixture(scope="function")
async def http_client(
    live_server: LiveAppServer,
) -> AsyncIterator[httpx.AsyncClient]:
    """Function-scoped ``httpx.AsyncClient`` bound to ``live_server``."""
    async with httpx.AsyncClient(
        base_url=live_server.base_url,
        timeout=15.0,
        trust_env=False,
    ) as client:
        yield client
