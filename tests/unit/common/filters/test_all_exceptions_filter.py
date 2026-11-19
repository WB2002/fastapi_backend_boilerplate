from __future__ import annotations

import json

import pytest
from fastapi import HTTPException

from app.common.filters import (
    AllExceptionsFilter,
    LoadLevelQueueFullError,
    LoadLevelWaitTimeoutError,
    OverloadRejectedError,
    PriorityShedRejectedError,
    is_database_connection_error,
)
from app.core.request_deadline import (
    HTTP_STATUS_CLIENT_CLOSED_REQUEST,
    RequestCancelledError,
    RequestDeadlineExceededError,
)


class _FakeDbError(Exception):
    def __init__(
        self, message: str = "db error", name: str = "MongoNetworkError"
    ) -> None:
        super().__init__(message)
        self.message = message
        self.name = name


class TestAllExceptionsFilter:
    def test_maps_request_deadline_exceeded_error_to_504(self) -> None:
        exc_filter = AllExceptionsFilter()
        req = {"url": "/test", "headers": {"x-request-id": "req-1"}}
        res = exc_filter.handle_exception(
            RequestDeadlineExceededError(),
            req,
        )
        assert res is not None
        status, payload, _headers = res
        assert status == 504
        assert payload["statusCode"] == 504
        assert payload["message"] == "Request deadline exceeded"
        assert payload["path"] == "/test"
        assert payload["requestId"] == "req-1"
        assert "timestamp" in payload

    def test_maps_request_cancelled_error_to_499(self) -> None:
        exc_filter = AllExceptionsFilter()
        req = {"url": "/test", "headers": {"x-request-id": "req-1"}}
        res = exc_filter.handle_exception(
            RequestCancelledError(),
            req,
            response={"raw": {"destroyed": False, "writableEnded": False}},
        )
        assert res is not None
        status, payload, _headers = res
        assert status == HTTP_STATUS_CLIENT_CLOSED_REQUEST
        assert payload["statusCode"] == 499
        assert payload["message"] == "Request cancelled by client"

    def test_skips_reply_for_499_when_response_is_already_destroyed(self) -> None:
        exc_filter = AllExceptionsFilter()
        req = {"url": "/test", "headers": {}}
        res = exc_filter.handle_exception(
            RequestCancelledError(),
            req,
            response={"raw": {"destroyed": True, "writableEnded": False}},
        )
        assert res is None

    def test_maps_overload_exceptions_to_503_with_retry_after_and_emf(
        self,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        exc_filter = AllExceptionsFilter()
        req = {"url": "/api/items", "headers": {"x-request-id": "req-1"}}
        capsys.readouterr()

        cases = [
            (
                PriorityShedRejectedError(),
                "Service unavailable: request priority shed under load",
                "api_priority_shed",
            ),
            (
                OverloadRejectedError(),
                "Service temporarily unavailable",
                "inbound_bulkhead",
            ),
            (
                LoadLevelQueueFullError(),
                "Inbound load leveling queue is full",
                "inbound_load_level_queue_full",
            ),
            (
                LoadLevelWaitTimeoutError(),
                "Timed out waiting for inbound capacity",
                "inbound_load_level_wait_timeout",
            ),
        ]

        for exc, expected_msg, _expected_reason in cases:
            res = exc_filter.handle_exception(exc, req)
            assert res is not None
            status, payload, headers = res
            assert status == 503
            assert payload["statusCode"] == 503
            assert payload["message"] == expected_msg
            assert headers.get("Retry-After") == "5"

        emf_lines = [
            json.loads(line)
            for line in capsys.readouterr().out.splitlines()
            if line.strip().startswith("{") and '"_aws"' in line
        ]
        reasons = [entry.get("Reason") for entry in emf_lines]
        assert reasons == [c[2] for c in cases]

    def test_maps_database_connection_errors_to_503_and_dependency_unavailable_emf(
        self,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        assert is_database_connection_error(_FakeDbError()) is True
        assert (
            is_database_connection_error(
                RuntimeError(
                    "Operation `users.findOne()` buffering timed out after 10000ms"
                )
            )
            is True
        )
        assert is_database_connection_error(ValueError("normal error")) is False

        exc_filter = AllExceptionsFilter()
        req = {"url": "/auth/login", "headers": {"x-request-id": "req-db"}}
        capsys.readouterr()

        res = exc_filter.handle_exception(_FakeDbError(), req)
        assert res is not None
        status, payload, _headers = res
        assert status == 503
        assert payload["statusCode"] == 503
        assert payload["message"] == "Service temporarily unavailable"

        emf_lines = [
            json.loads(line)
            for line in capsys.readouterr().out.splitlines()
            if line.strip().startswith("{") and '"_aws"' in line
        ]
        assert any(
            entry.get("Reason") == "dependency_unavailable"
            and entry.get("Path") == "/auth/login"
            for entry in emf_lines
        )

    def test_merges_terminus_health_503_details_and_emits_health_check_emf(
        self,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        exc_filter = AllExceptionsFilter()
        terminus_body = {
            "status": "error",
            "info": {"memory_heap": {"status": "up"}},
            "error": {"mongo": {"status": "down", "message": "Unavailable"}},
            "details": {
                "memory_heap": {"status": "up"},
                "mongo": {"status": "down", "message": "Unavailable"},
            },
        }
        exc = HTTPException(status_code=503, detail=terminus_body)
        req = {"url": "/health/ready", "headers": {"x-request-id": "req-health"}}
        capsys.readouterr()

        res = exc_filter.handle_exception(exc, req)
        assert res is not None
        status, payload, _headers = res
        assert status == 503
        assert payload["statusCode"] == 503
        assert payload["path"] == "/health/ready"
        assert payload["requestId"] == "req-health"
        assert payload["status"] == "error"
        assert payload["details"]["mongo"]["status"] == "down"
        assert "message" not in payload

        emf_lines = [
            json.loads(line)
            for line in capsys.readouterr().out.splitlines()
            if line.strip().startswith("{") and '"_aws"' in line
        ]
        assert any(
            entry.get("Reason") == "health_check"
            and entry.get("Path") == "/health/ready"
            for entry in emf_lines
        )
