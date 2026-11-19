from __future__ import annotations

import urllib.parse
from typing import Any

from fastapi import HTTPException, status

from app.conformance.dto import ConformanceHttpClientRequestDto
from app.core.http_client import (
    CircuitBreakerPolicy,
    CircuitOpenError,
    ConcurrencyLimitPolicy,
    ConfiguredHttpClient,
    HttpClient,
    HttpClientError,
    HttpNetworkError,
    HttpTimeoutError,
    RetryExhaustedError,
    RetryPolicy,
)


class ConformanceHttpClientService:
    def __init__(self, http_client: HttpClient) -> None:
        self._http_client = http_client
        self._configured_clients: dict[str, ConfiguredHttpClient] = {}
        self._max_cache_entries = 50

    async def execute(self, request: ConformanceHttpClientRequestDto) -> dict[str, Any]:
        client = self._get_or_create_client(request)
        try:
            res = await client.get(request.targetUrl)
            body: Any = None
            try:
                body = res.json()
            except Exception:
                body = res.text
            return {
                "upstreamStatus": res.status_code,
                "upstreamBody": body,
            }
        except Exception as exc:
            self._handle_http_client_error(exc)
            return {}

    def _get_or_create_client(
        self, request: ConformanceHttpClientRequestDto
    ) -> ConfiguredHttpClient:
        existing = self._configured_clients.get(request.clientName)
        if existing:
            return existing

        if len(self._configured_clients) >= self._max_cache_entries:
            oldest_key = next(iter(self._configured_clients))
            self._configured_clients.pop(oldest_key, None)

        client = self._build_client(request)
        self._configured_clients[request.clientName] = client
        return client

    def _build_client(
        self, request: ConformanceHttpClientRequestDto
    ) -> ConfiguredHttpClient:
        parsed = urllib.parse.urlparse(request.targetUrl)
        base_url = f"{parsed.scheme}://{parsed.netloc}"

        client = (
            self._http_client.internal(request.clientName)
            if request.clientKind == "internal"
            else self._http_client.external(request.clientName)
        )
        client = client.add_base_url(base_url)

        if request.maxRetries is not None:
            client = client.add_retry(
                RetryPolicy(
                    max_attempts=request.maxRetries + 1,
                    strategy="exponential",
                    base_delay_ms=request.baseDelayMs or 50,
                    jitter="full",
                )
            )

        if request.circuitBreakerFailureThreshold is not None:
            client = client.add_circuit_breaker(
                CircuitBreakerPolicy(
                    failure_threshold=request.circuitBreakerFailureThreshold,
                    reset_timeout_ms=request.circuitBreakerResetTimeoutMs or 1000,
                )
            )

        if request.shouldCoalesce:
            client = client.add_request_coalescing()

        if request.maxConcurrent is not None:
            client = client.add_concurrency_limit(
                ConcurrencyLimitPolicy(
                    mode=request.concurrencyMode or "fixed",
                    max_concurrent=request.maxConcurrent,
                )
            )

        if request.shouldPropagateDeadline:
            client = client.add_deadline_propagation()

        if request.timeoutMs is not None:
            client = client.add_timeout(request.timeoutMs)

        return client

    def _handle_http_client_error(self, exc: Exception) -> None:
        if isinstance(exc, CircuitOpenError):
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={
                    "errorName": "CircuitOpenError",
                    "errorMessage": str(exc),
                },
            )
        if isinstance(exc, HttpTimeoutError):
            raise HTTPException(
                status_code=status.HTTP_504_GATEWAY_TIMEOUT,
                detail={"errorName": "TimeoutError", "errorMessage": str(exc)},
            )
        if isinstance(exc, (RetryExhaustedError, HttpNetworkError)):
            name = (
                "RetryExhaustedError"
                if isinstance(exc, RetryExhaustedError)
                else "NetworkError"
            )
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail={
                    "errorName": name,
                    "errorMessage": str(exc),
                },
            )
        if isinstance(exc, HttpClientError):
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail={
                    "errorName": "HttpError",
                    "errorMessage": str(exc),
                    "statusCode": getattr(exc, "status_code", 502),
                },
            )
        raise exc
