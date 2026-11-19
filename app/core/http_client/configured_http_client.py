from __future__ import annotations

import asyncio
import os
import time
from typing import Any
from urllib.parse import urljoin

import httpx

from app.common.sanitization.sanitization_pipe import assert_public_http_url_async
from app.core.http_client.circuit_breaker import CircuitBreaker
from app.core.http_client.concurrency_limiter import (
    AdaptiveConcurrencyGate,
    FixedConcurrencyGate,
)
from app.core.http_client.retry_policy import (
    calculate_retry_delay,
    default_can_retry,
    default_is_transient,
)
from app.core.http_client.single_flight import (
    SingleFlightCoalescer,
    default_coalescing_key,
)
from app.core.http_client.types import (
    CircuitBreakerPolicy,
    CircuitOpenError,
    ClientIdentity,
    ClientPolicySnapshot,
    CoalescingPolicy,
    ConcurrencyLimitPolicy,
    DeadlinePropagationPolicy,
    HttpClientError,
    HttpNetworkError,
    HttpRequest,
    HttpResponse,
    HttpTimeoutError,
    RetryExhaustedError,
    RetryPolicy,
    SsrBlockedError,
    TimeoutPolicy,
)
from app.core.request_deadline.deadline_context import (
    REQUEST_DEADLINE_HEADER,
    get_deadline_at,
    remaining_ms,
)
from app.core.telemetry.emf_metrics import emit_http_metrics


class ConfiguredHttpClient:
    def __init__(
        self,
        policy: ClientPolicySnapshot,
        *,
        circuit_breaker: CircuitBreaker | None = None,
        concurrency_gate: FixedConcurrencyGate | AdaptiveConcurrencyGate | None = None,
        coalescer: SingleFlightCoalescer | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.policy = policy
        self._circuit_breaker = circuit_breaker or (
            CircuitBreaker(policy.circuit_breaker) if policy.circuit_breaker else None
        )
        self._concurrency_gate: FixedConcurrencyGate | AdaptiveConcurrencyGate | None
        if concurrency_gate is not None:
            self._concurrency_gate = concurrency_gate
        elif policy.concurrency_limit is not None:
            if policy.concurrency_limit.mode == "adaptive":
                self._concurrency_gate = AdaptiveConcurrencyGate(
                    tuning=policy.concurrency_limit.adaptive,
                    cluster_aware=policy.concurrency_limit.cluster_aware,
                )
            else:
                self._concurrency_gate = FixedConcurrencyGate(
                    max_concurrent=policy.concurrency_limit.max_concurrent
                )
        else:
            self._concurrency_gate = None

        self._coalescer = coalescer or SingleFlightCoalescer()
        self._client = client

    @property
    def identity(self) -> ClientIdentity:
        return self.policy.identity

    def add_base_url(self, base_url: str) -> ConfiguredHttpClient:
        snapshot = self._clone_policy(base_url=base_url)
        return ConfiguredHttpClient(
            snapshot,
            circuit_breaker=self._circuit_breaker,
            concurrency_gate=self._concurrency_gate,
            coalescer=self._coalescer,
            client=self._client,
        )

    def add_default_headers(self, headers: dict[str, str]) -> ConfiguredHttpClient:
        merged = {**self.policy.default_headers, **headers}
        snapshot = self._clone_policy(default_headers=merged)
        return ConfiguredHttpClient(
            snapshot,
            circuit_breaker=self._circuit_breaker,
            concurrency_gate=self._concurrency_gate,
            coalescer=self._coalescer,
            client=self._client,
        )

    def add_timeout(self, timeout: int | TimeoutPolicy) -> ConfiguredHttpClient:
        policy = (
            timeout
            if isinstance(timeout, TimeoutPolicy)
            else TimeoutPolicy(timeout_ms=timeout)
        )
        snapshot = self._clone_policy(timeout=policy)
        return ConfiguredHttpClient(
            snapshot,
            circuit_breaker=self._circuit_breaker,
            concurrency_gate=self._concurrency_gate,
            coalescer=self._coalescer,
            client=self._client,
        )

    def add_deadline_propagation(
        self, policy: DeadlinePropagationPolicy | None = None
    ) -> ConfiguredHttpClient:
        p = policy or DeadlinePropagationPolicy(propagate_header=True)
        snapshot = self._clone_policy(deadline_propagation=p)
        return ConfiguredHttpClient(
            snapshot,
            circuit_breaker=self._circuit_breaker,
            concurrency_gate=self._concurrency_gate,
            coalescer=self._coalescer,
            client=self._client,
        )

    def add_retry(self, policy: RetryPolicy) -> ConfiguredHttpClient:
        snapshot = self._clone_policy(retry=policy)
        return ConfiguredHttpClient(
            snapshot,
            circuit_breaker=self._circuit_breaker,
            concurrency_gate=self._concurrency_gate,
            coalescer=self._coalescer,
            client=self._client,
        )

    def add_request_coalescing(
        self, policy: CoalescingPolicy | None = None
    ) -> ConfiguredHttpClient:
        p = policy or CoalescingPolicy(enabled=True)
        snapshot = self._clone_policy(coalescing=p)
        return ConfiguredHttpClient(
            snapshot,
            circuit_breaker=self._circuit_breaker,
            concurrency_gate=self._concurrency_gate,
            coalescer=self._coalescer,
            client=self._client,
        )

    def add_circuit_breaker(self, policy: CircuitBreakerPolicy) -> ConfiguredHttpClient:
        cb = CircuitBreaker(policy)
        snapshot = self._clone_policy(circuit_breaker=policy)
        return ConfiguredHttpClient(
            snapshot,
            circuit_breaker=cb,
            concurrency_gate=self._concurrency_gate,
            coalescer=self._coalescer,
            client=self._client,
        )

    def add_concurrency_limit(
        self, policy: ConcurrencyLimitPolicy
    ) -> ConfiguredHttpClient:
        gate: FixedConcurrencyGate | AdaptiveConcurrencyGate
        if policy.mode == "adaptive":
            gate = AdaptiveConcurrencyGate(
                tuning=policy.adaptive,
                cluster_aware=policy.cluster_aware,
            )
        else:
            gate = FixedConcurrencyGate(max_concurrent=policy.max_concurrent)
        snapshot = self._clone_policy(concurrency_limit=policy)
        return ConfiguredHttpClient(
            snapshot,
            circuit_breaker=self._circuit_breaker,
            concurrency_gate=gate,
            coalescer=self._coalescer,
            client=self._client,
        )

    def allow_private_urls(self, allow: bool = True) -> ConfiguredHttpClient:
        snapshot = self._clone_policy(allow_private_urls=allow)
        return ConfiguredHttpClient(
            snapshot,
            circuit_breaker=self._circuit_breaker,
            concurrency_gate=self._concurrency_gate,
            coalescer=self._coalescer,
            client=self._client,
        )

    async def get(
        self,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        params: dict[str, Any] | None = None,
        timeout: float | None = None,  # noqa: ASYNC109
    ) -> HttpResponse:
        req = HttpRequest(
            method="GET",
            url=url,
            headers=headers or {},
            params=params or {},
            timeout=timeout,
        )
        return await self.request(req)

    async def post(
        self,
        url: str,
        *,
        body: Any = None,
        json: Any = None,
        headers: dict[str, str] | None = None,
        params: dict[str, Any] | None = None,
        timeout: float | None = None,  # noqa: ASYNC109
    ) -> HttpResponse:
        req = HttpRequest(
            method="POST",
            url=url,
            headers=headers or {},
            params=params or {},
            data=body,
            json=json,
            timeout=timeout,
        )
        return await self.request(req)

    async def put(
        self,
        url: str,
        *,
        body: Any = None,
        json: Any = None,
        headers: dict[str, str] | None = None,
        params: dict[str, Any] | None = None,
        timeout: float | None = None,  # noqa: ASYNC109
    ) -> HttpResponse:
        req = HttpRequest(
            method="PUT",
            url=url,
            headers=headers or {},
            params=params or {},
            data=body,
            json=json,
            timeout=timeout,
        )
        return await self.request(req)

    async def patch(
        self,
        url: str,
        *,
        body: Any = None,
        json: Any = None,
        headers: dict[str, str] | None = None,
        params: dict[str, Any] | None = None,
        timeout: float | None = None,  # noqa: ASYNC109
    ) -> HttpResponse:
        req = HttpRequest(
            method="PATCH",
            url=url,
            headers=headers or {},
            params=params or {},
            data=body,
            json=json,
            timeout=timeout,
        )
        return await self.request(req)

    async def delete(
        self,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        params: dict[str, Any] | None = None,
        timeout: float | None = None,  # noqa: ASYNC109
    ) -> HttpResponse:
        req = HttpRequest(
            method="DELETE",
            url=url,
            headers=headers or {},
            params=params or {},
            timeout=timeout,
        )
        return await self.request(req)

    async def head(
        self,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        params: dict[str, Any] | None = None,
        timeout: float | None = None,  # noqa: ASYNC109
    ) -> HttpResponse:
        req = HttpRequest(
            method="HEAD",
            url=url,
            headers=headers or {},
            params=params or {},
            timeout=timeout,
        )
        return await self.request(req)

    async def request(self, request: HttpRequest) -> HttpResponse:
        full_url = self._resolve_url(request.url)

        # SSRF Check
        allow_private = self.policy.allow_private_urls or os.getenv(
            "HTTP_CLIENT_ALLOW_PRIVATE_URLS", "false"
        ).lower() in ("1", "true", "yes")

        if not allow_private:
            try:
                await assert_public_http_url_async(full_url)
            except Exception as exc:
                raise SsrBlockedError(
                    f"SSRF validation blocked request to {full_url}: {exc}"
                ) from exc

        # Coalescing check
        method = request.method.upper()
        if self.policy.coalescing.enabled and method in self.policy.coalescing.methods:
            key_builder = self.policy.coalescing.key_builder or default_coalescing_key
            req_for_key = HttpRequest(
                method=method,
                url=full_url,
                headers=request.headers,
                params=request.params,
            )
            key = key_builder(req_for_key)
            return await self._coalescer.run(
                key, lambda: self._execute_resilient(request, full_url)
            )

        return await self._execute_resilient(request, full_url)

    async def _execute_resilient(
        self, request: HttpRequest, full_url: str
    ) -> HttpResponse:
        rem = remaining_ms()
        if rem is not None and rem <= 0:
            raise HttpTimeoutError("Request deadline budget exceeded before dispatch")

        gate = self._concurrency_gate
        if gate is not None:
            await gate.acquire()

        start_time = time.time()
        attempt = 0
        last_error: Exception | None = None
        status_code: int | None = None
        prev_delay_ms: int | None = None

        retry_policy = self.policy.retry
        max_attempts = retry_policy.max_attempts if retry_policy else 1

        try:
            while attempt < max_attempts:
                attempt += 1
                if self._circuit_breaker is not None:
                    self._circuit_breaker.assert_closed()

                res, err, retryable = await self._run_attempt(
                    request, full_url, start_time
                )
                if res is not None:
                    return res

                last_error = err
                if isinstance(err, HttpClientError) and err.status_code:
                    status_code = err.status_code

                can_retry_fn = (
                    retry_policy.can_retry
                    if retry_policy and retry_policy.can_retry
                    else default_can_retry
                )
                if attempt < max_attempts and retryable and can_retry_fn(request):
                    delay = calculate_retry_delay(
                        retry_policy or RetryPolicy(),
                        attempt,
                        prev_delay_ms,
                    )
                    prev_delay_ms = delay
                    await asyncio.sleep(delay / 1000.0)
                else:
                    break

            await emit_http_metrics(
                enabled=True,
                identity={
                    "service_name": self.policy.identity.service_name,
                    "kind": self.policy.identity.kind,
                },
                method=request.method,
                latency_ms=(time.time() - start_time) * 1000,
                attempts=attempt,
                success=False,
                status=status_code,
                error_code=(
                    type(last_error).__name__ if last_error else "UnknownError"
                ),
            )

            if max_attempts > 1 and last_error is not None:
                raise RetryExhaustedError(
                    f"Outbound HTTP exhausted {max_attempts} tries: {last_error}",
                    cause=last_error,
                )
            if last_error is not None:
                raise last_error
            raise HttpClientError("HTTP request failed with unknown error")

        finally:
            if gate is not None:
                await gate.release()

    async def _run_attempt(
        self, request: HttpRequest, full_url: str, start_time: float
    ) -> tuple[HttpResponse | None, Exception | None, bool]:
        """Run single HTTP attempt and record circuit breaker / adaptive telemetry."""
        gate = self._concurrency_gate
        retry_policy = self.policy.retry
        is_transient_fn = (
            retry_policy.is_transient
            if retry_policy and retry_policy.is_transient
            else default_is_transient
        )

        try:
            res = await self._send_single_attempt(request, full_url)
            status_code = res.status_code

            if is_transient_fn(status_code):
                if self._circuit_breaker is not None and status_code >= 500:
                    self._circuit_breaker.record_failure()
                if isinstance(gate, AdaptiveConcurrencyGate):
                    gate.record_failure()
                err = HttpClientError(
                    f"HTTP request failed with status {status_code}",
                    status_code=status_code,
                    response=res,
                )
                return None, err, True

            if self._circuit_breaker is not None:
                self._circuit_breaker.record_success()
            if isinstance(gate, AdaptiveConcurrencyGate):
                gate.record_success(int((time.time() - start_time) * 1000))

            await emit_http_metrics(
                enabled=True,
                identity={
                    "service_name": self.policy.identity.service_name,
                    "kind": self.policy.identity.kind,
                },
                method=request.method,
                latency_ms=(time.time() - start_time) * 1000,
                attempts=1,
                success=True,
                status=status_code,
            )
            return res, None, False

        except (httpx.TimeoutException, TimeoutError) as exc:
            if self._circuit_breaker is not None:
                self._circuit_breaker.record_failure()
            if isinstance(gate, AdaptiveConcurrencyGate):
                gate.record_failure()
            return None, HttpTimeoutError(str(exc)), True

        except (httpx.NetworkError, ConnectionError) as exc:
            if self._circuit_breaker is not None:
                self._circuit_breaker.record_failure()
            if isinstance(gate, AdaptiveConcurrencyGate):
                gate.record_failure()
            return None, HttpNetworkError(str(exc)), True

        except CircuitOpenError:
            raise

        except Exception as exc:
            if self._circuit_breaker is not None:
                self._circuit_breaker.record_failure()
            return None, exc, False

    async def _send_single_attempt(
        self, request: HttpRequest, full_url: str
    ) -> HttpResponse:
        headers = {**self.policy.default_headers, **request.headers}

        if self.policy.deadline_propagation.propagate_header:
            deadline_at = get_deadline_at()
            has_deadline = any(k.lower() == REQUEST_DEADLINE_HEADER for k in headers)
            if deadline_at is not None and not has_deadline:
                headers[REQUEST_DEADLINE_HEADER] = str(deadline_at)

        timeout_sec: float | None = request.timeout
        if timeout_sec is None and self.policy.timeout.timeout_ms is not None:
            timeout_sec = self.policy.timeout.timeout_ms / 1000.0

        rem = remaining_ms()
        if rem is not None:
            rem_sec = rem / 1000.0
            timeout_sec = min(timeout_sec, rem_sec) if timeout_sec else rem_sec

        client = self._client
        owns_client = False
        if client is None:
            client = httpx.AsyncClient()
            owns_client = True

        try:
            resp = await client.request(
                method=request.method,
                url=full_url,
                headers=headers,
                params=request.params,
                data=request.data,
                json=request.json,
                timeout=timeout_sec,
            )
            return HttpResponse(
                status_code=resp.status_code,
                headers=dict(resp.headers),
                content=resp.content,
                request=request,
            )
        finally:
            if owns_client:
                await client.aclose()

    def _resolve_url(self, url: str) -> str:
        if url.startswith("http://") or url.startswith("https://"):
            return url
        if self.policy.base_url:
            base = self.policy.base_url.rstrip("/") + "/"
            return urljoin(base, url.lstrip("/"))
        return url

    def _clone_policy(self, **kwargs: Any) -> ClientPolicySnapshot:
        curr = self.policy
        return ClientPolicySnapshot(
            identity=kwargs.get("identity", curr.identity),
            base_url=kwargs.get("base_url", curr.base_url),
            default_headers=kwargs.get("default_headers", curr.default_headers),
            timeout=kwargs.get("timeout", curr.timeout),
            deadline_propagation=kwargs.get(
                "deadline_propagation", curr.deadline_propagation
            ),
            retry=kwargs.get("retry", curr.retry),
            circuit_breaker=kwargs.get("circuit_breaker", curr.circuit_breaker),
            concurrency_limit=kwargs.get("concurrency_limit", curr.concurrency_limit),
            coalescing=kwargs.get("coalescing", curr.coalescing),
            allow_private_urls=kwargs.get(
                "allow_private_urls", curr.allow_private_urls
            ),
        )
