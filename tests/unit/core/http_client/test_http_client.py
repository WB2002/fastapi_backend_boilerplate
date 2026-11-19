from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from app.core.http_client import (
    AdaptiveConcurrencyGate,
    AdaptiveConcurrencyTuning,
    AdaptiveLimitExceededError,
    CircuitBreaker,
    CircuitBreakerPolicy,
    CircuitOpenError,
    FixedConcurrencyGate,
    HttpClient,
    HttpRequest,
    HttpResponse,
    SingleFlightCoalescer,
    SsrBlockedError,
    apply_jitter,
    compute_backoff_delay,
    default_can_retry,
    default_is_transient,
)
from app.core.request_deadline.deadline_context import (
    REQUEST_DEADLINE_HEADER,
    DeadlineContext,
    RequestAbortSignal,
    RequestDeadlineStore,
)


def test_compute_backoff_and_jitter() -> None:
    # Fixed
    assert compute_backoff_delay("fixed", 1, 1000, 10000) == 1000
    assert compute_backoff_delay("fixed", 5, 1000, 10000) == 1000

    # Linear
    assert compute_backoff_delay("linear", 1, 1000, 10000) == 1000
    assert compute_backoff_delay("linear", 3, 1000, 10000) == 3000
    assert compute_backoff_delay("linear", 15, 1000, 10000) == 10000

    # Exponential
    assert compute_backoff_delay("exponential", 1, 1000, 10000) == 1000
    assert compute_backoff_delay("exponential", 2, 1000, 10000) == 2000
    assert compute_backoff_delay("exponential", 3, 1000, 10000) == 4000
    assert compute_backoff_delay("exponential", 10, 1000, 10000) == 10000

    # Jitter
    assert apply_jitter("none", 500, 1000, 10000) == 500
    full = apply_jitter("full", 500, 1000, 10000)
    assert 0 <= full <= 500
    equal = apply_jitter("equal", 500, 1000, 10000)
    assert 250 <= equal <= 500


def test_retry_predicates() -> None:
    assert default_is_transient(500) is True
    assert default_is_transient(503) is True
    assert default_is_transient(429) is True
    assert default_is_transient(404) is False
    assert default_is_transient(200) is False

    req_get = HttpRequest(method="GET", url="https://api.example.com")
    req_post = HttpRequest(method="POST", url="https://api.example.com")
    assert default_can_retry(req_get) is True
    assert default_can_retry(req_post) is False


def test_circuit_breaker_transitions() -> None:
    policy = CircuitBreakerPolicy(
        failure_threshold=2, reset_timeout_ms=50, half_open_max_attempts=1
    )
    cb = CircuitBreaker(policy)

    # Initial closed state
    cb.assert_closed()
    assert cb.state == "closed"

    # 1 failure -> still closed
    cb.record_failure()
    cb.assert_closed()
    assert cb.state == "closed"

    # 2 failures -> open
    cb.record_failure()
    assert str(cb.state) == "open"
    with pytest.raises(CircuitOpenError):
        cb.assert_closed()

    # Wait for reset timeout
    import time

    time.sleep(0.06)

    # Half open transition
    cb.assert_closed()
    assert str(cb.state) == "half_open"

    # Success restores to closed
    cb.record_success()
    assert str(cb.state) == "closed"


@pytest.mark.asyncio
async def test_concurrency_limiter_fixed() -> None:
    gate = FixedConcurrencyGate(max_concurrent=2)
    assert gate.in_flight == 0

    await gate.acquire()
    assert gate.in_flight == 1
    await gate.acquire()
    assert gate.in_flight == 2

    await gate.release()
    assert gate.in_flight == 1
    await gate.release()
    assert gate.in_flight == 0


@pytest.mark.asyncio
async def test_concurrency_limiter_adaptive() -> None:
    tuning = AdaptiveConcurrencyTuning(
        algorithm="aimd", initial_limit=2, min_limit=1, max_limit=5
    )
    gate = AdaptiveConcurrencyGate(tuning)

    await gate.acquire()
    await gate.acquire()
    with pytest.raises(AdaptiveLimitExceededError):
        await gate.acquire()

    await gate.release()
    await gate.release()

    # Record failure shrinks limit
    gate.record_failure()
    assert gate._limiter.current_limit == 1


@pytest.mark.asyncio
async def test_single_flight_coalescer() -> None:
    coalescer = SingleFlightCoalescer()
    call_count = 0

    async def factory() -> HttpResponse:
        nonlocal call_count
        call_count += 1
        await asyncio.sleep(0.02)
        return HttpResponse(
            status_code=200,
            headers={},
            content=b'{"ok": true}',
            request=HttpRequest(method="GET", url="https://api.example.com"),
        )

    res1, res2 = await asyncio.gather(
        coalescer.run("key-1", factory),
        coalescer.run("key-1", factory),
    )
    assert res1.status_code == 200
    assert res2.status_code == 200
    assert call_count == 1


@pytest.mark.asyncio
async def test_http_client_builder_and_ssrf() -> None:
    client = HttpClient()
    ext = (
        client.external("payments-api")
        .add_base_url("https://api.stripe.com")
        .add_default_headers({"X-Client": "test"})
    )
    assert ext.identity.service_name == "payments-api"
    assert ext.identity.kind == "external"

    # Private IP blocked by default
    with pytest.raises(SsrBlockedError):
        await ext.get("http://127.0.0.1:8080/secret")

    # Allowing private urls
    allowed = ext.allow_private_urls(True)
    assert allowed.policy.allow_private_urls is True

    await client.close()


@pytest.mark.asyncio
async def test_http_client_deadline_propagation() -> None:
    client = HttpClient()
    internal_cli = client.internal("order-service").allow_private_urls(True)

    # Fake deadline context
    now_ms = int(time.time() * 1000)
    target_deadline = now_ms + 60000
    store = RequestDeadlineStore(
        signal=RequestAbortSignal(),
        started_at=now_ms,
        deadline_at=target_deadline,
    )
    token = DeadlineContext.set(store)

    try:
        mock_response = httpx.Response(
            status_code=200,
            json={"status": "ok"},
            request=httpx.Request("GET", "https://order-service/orders"),
        )
        with patch.object(
            httpx.AsyncClient, "request", new_callable=AsyncMock
        ) as mock_req:
            mock_req.return_value = mock_response

            res = await internal_cli.get("https://order-service/orders")
            assert res.status_code == 200
            assert mock_req.call_count == 1
            call_headers = mock_req.call_args.kwargs["headers"]
            assert REQUEST_DEADLINE_HEADER in call_headers
            assert call_headers[REQUEST_DEADLINE_HEADER] == str(target_deadline)
    finally:
        DeadlineContext.reset(token)
        await client.close()
