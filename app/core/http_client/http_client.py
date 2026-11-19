from __future__ import annotations

import httpx

from app.core.http_client.circuit_breaker import CircuitBreaker
from app.core.http_client.concurrency_limiter import (
    AdaptiveConcurrencyGate,
    FixedConcurrencyGate,
)
from app.core.http_client.configured_http_client import ConfiguredHttpClient
from app.core.http_client.single_flight import SingleFlightCoalescer
from app.core.http_client.types import (
    ClientIdentity,
    ClientKind,
    ClientPolicySnapshot,
    DeadlinePropagationPolicy,
    HttpClientError,
)


class HttpClient:
    """Outbound resilient HTTP client manager matching NestJS HttpClient."""

    def __init__(self) -> None:
        self._is_closed = False
        self._client: httpx.AsyncClient | None = None
        self._circuit_breakers: dict[str, CircuitBreaker] = {}
        self._concurrency_gates: dict[
            str, FixedConcurrencyGate | AdaptiveConcurrencyGate
        ] = {}
        self._coalescers: dict[str, SingleFlightCoalescer] = {}

    @property
    def is_closed(self) -> bool:
        return self._is_closed

    def internal(self, service_name: str) -> ConfiguredHttpClient:
        return self.create("internal", service_name)

    def external(self, service_name: str) -> ConfiguredHttpClient:
        return self.create("external", service_name)

    def create(self, kind: ClientKind, service_name: str) -> ConfiguredHttpClient:
        if self._is_closed:
            raise HttpClientError("HttpClient is closed")
        if not service_name or not service_name.strip():
            raise HttpClientError("service_name is required")

        norm_name = service_name.strip()
        reg_key = f"{kind}:{norm_name}"

        if reg_key not in self._coalescers:
            self._coalescers[reg_key] = SingleFlightCoalescer()

        # Internal clients propagate deadline header by default
        propagate = kind == "internal"
        policy = ClientPolicySnapshot(
            identity=ClientIdentity(kind=kind, service_name=norm_name),
            deadline_propagation=DeadlinePropagationPolicy(propagate_header=propagate),
        )

        if self._client is None:
            self._client = httpx.AsyncClient()

        return ConfiguredHttpClient(
            policy,
            coalescer=self._coalescers[reg_key],
            client=self._client,
        )

    async def aclose(self) -> None:
        if self._is_closed:
            return
        self._is_closed = True
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def close(self) -> None:
        await self.aclose()
