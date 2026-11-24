from __future__ import annotations

import datetime
import json
import os
import traceback
import uuid
from collections.abc import Mapping
from typing import Any, Final

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.common.config.parsers import parse_under_pressure_config
from app.core.logger.logger_config import get_logger
from app.core.logger.logger_context import LoggerContext
from app.core.request_deadline.deadline_context import (
    HTTP_STATUS_CLIENT_CLOSED_REQUEST,
    RequestCancelledError,
    RequestDeadlineExceededError,
    get_deadline_reason,
)
from app.core.telemetry.emf_metrics import (
    record_api_priority_shed_503,
    record_dependency_unavailable_503,
    record_health_check_503,
    record_inbound_bulkhead_503,
    record_inbound_load_level_queue_full_503,
    record_inbound_load_level_wait_timeout_503,
)

DB_CONNECTION_ERROR_NAMES: Final[frozenset[str]] = frozenset(
    {
        "MongooseServerSelectionError",
        "MongoNetworkError",
        "ServerSelectionTimeoutError",
        "ConnectionFailure",
        "AutoReconnect",
        "NetworkTimeout",
        "PyMongoError",
        "PostgresConnectionError",
        "InterfaceError",
        "OperationalError",
        "CannotConnectNowError",
        "ConnectionDoesNotExistError",
        "TooManyConnectionsError",
    }
)


class OverloadRejectedError(Exception):
    """Raised when inbound bulkhead capacity is exceeded (HTTP 503)."""

    def __init__(self, message: str = "Service temporarily unavailable") -> None:
        super().__init__(message)
        self.message = message
        self.name = "OverloadRejectedError"


class LoadLevelQueueFullError(Exception):
    """Raised when the inbound load-leveling queue is full (HTTP 503)."""

    def __init__(
        self,
        message: str = "Inbound load leveling queue is full",
    ) -> None:
        super().__init__(message)
        self.message = message
        self.name = "LoadLevelQueueFullError"


class LoadLevelWaitTimeoutError(Exception):
    """Raised when a request times out waiting in the load-leveling queue (HTTP 503)."""

    def __init__(
        self,
        message: str = "Timed out waiting for inbound capacity",
    ) -> None:
        super().__init__(message)
        self.message = message
        self.name = "LoadLevelWaitTimeoutError"


class PriorityShedRejectedError(Exception):
    """Raised when a request is shed due to priority thresholds under load."""

    def __init__(
        self,
        message: str = "Service unavailable: request priority shed under load",
    ) -> None:
        super().__init__(message)
        self.message = message
        self.name = "PriorityShedRejectedError"


def resolve_overload_retry_after_sec(
    env: Mapping[str, str | None] | None = None,
) -> int:
    target = os.environ if env is None else env
    return parse_under_pressure_config(target).retry_after_sec


def _is_mro_db_error(cls: type[Any]) -> bool:
    for module_cls in cls.__mro__:
        mod_name = getattr(module_cls, "__module__", "") or ""
        m_cls_name = getattr(module_cls, "__name__", "") or ""
        if m_cls_name in DB_CONNECTION_ERROR_NAMES:
            return True
        if mod_name.startswith(("pymongo", "motor")) and m_cls_name in (
            "PyMongoError",
            "ConnectionFailure",
            "ServerSelectionTimeoutError",
        ):
            return True
        if mod_name.startswith("asyncpg") and (
            "Connection" in m_cls_name or "InterfaceError" in m_cls_name
        ):
            return True
        if mod_name.startswith("sqlalchemy") and m_cls_name in (
            "OperationalError",
            "InterfaceError",
            "DisconnectionError",
            "TimeoutError",
        ):
            return True
    return False


def is_database_connection_error(exception: object) -> bool:
    """Return True if an exception represents a database connection/timeout error."""
    custom_name = getattr(exception, "name", None)
    if isinstance(custom_name, str) and custom_name in DB_CONNECTION_ERROR_NAMES:
        return True
    if _is_mro_db_error(type(exception)):
        return True
    raw_msg = getattr(exception, "message", None)
    msg_str = raw_msg if isinstance(raw_msg, str) else str(exception)
    return "buffering timed out" in msg_str.lower()


def _extract_path(request: Request | Mapping[str, Any] | Any) -> str:
    if isinstance(request, Request):
        path = request.url.path
        query = request.url.query
        return f"{path}?{query}" if query else path
    if isinstance(request, Mapping):
        if "url" in request and isinstance(request["url"], str):
            return request["url"]
        if "path" in request and isinstance(request["path"], str):
            qs = request.get("query_string")
            if isinstance(qs, (bytes, bytearray)) and qs:
                path_val = request["path"]
                qs_str = qs.decode("latin-1")
                return "?".join((path_val, qs_str))
            return request["path"]
    url_attr = getattr(request, "url", None)
    if isinstance(url_attr, str):
        return url_attr
    if url_attr is not None and hasattr(url_attr, "path"):
        p = str(url_attr.path)
        q = str(getattr(url_attr, "query", "") or "")
        return f"{p}?{q}" if q else p
    return "/"


def _header_request_id_from_mapping(headers: Mapping[Any, Any]) -> str | None:
    for k, v in headers.items():
        if str(k).lower() == "x-request-id" and v and str(v).strip():
            return str(v).strip()
    return None


def _header_request_id_from_list(headers: list[Any]) -> str | None:
    for item in headers:
        if (
            isinstance(item, tuple)
            and len(item) == 2
            and isinstance(item[0], (bytes, bytearray))
            and item[0].lower() == b"x-request-id"
        ):
            decoded = bytes(item[1]).decode("latin-1").strip()
            if decoded:
                return decoded
    return None


def _extract_request_id(request: Request | Mapping[str, Any] | Any) -> str:
    row_headers = getattr(request, "headers", None)
    if isinstance(row_headers, Mapping):
        found = _header_request_id_from_mapping(row_headers)
        if found:
            return found
    elif isinstance(request, Mapping):
        headers = request.get("headers")
        if isinstance(headers, Mapping):
            found = _header_request_id_from_mapping(headers)
            if found:
                return found
        elif isinstance(headers, list):
            found = _header_request_id_from_list(headers)
            if found:
                return found
        state = request.get("state")
        if isinstance(state, Mapping) and isinstance(state.get("request_id"), str):
            return str(state["request_id"])

    state_obj = getattr(request, "state", None)
    st_req_id = (
        getattr(state_obj, "request_id", None) if state_obj is not None else None
    )
    if isinstance(st_req_id, str) and st_req_id.strip():
        return st_req_id.strip()

    ctx_req_id = LoggerContext.get().get("requestId")
    if isinstance(ctx_req_id, str) and ctx_req_id.strip():
        return ctx_req_id.strip()
    return str(uuid.uuid4())


def _is_obj_destroyed(obj: Any) -> bool:
    if obj is None:
        return False
    if isinstance(obj, Mapping):
        return bool(obj.get("destroyed") is True or obj.get("writableEnded") is True)
    return bool(
        getattr(obj, "destroyed", False) is True
        or getattr(obj, "writableEnded", False) is True
    )


def _is_response_destroyed(
    request: Request | Mapping[str, Any] | Any = None,
    response: Any = None,
) -> bool:
    if response is not None:
        if _is_obj_destroyed(response):
            return True
        raw = (
            response.get("raw")
            if isinstance(response, Mapping)
            else getattr(response, "raw", None)
        )
        if _is_obj_destroyed(raw):
            return True

    if request is not None:
        state = (
            request.get("state")
            if isinstance(request, Mapping)
            else getattr(request, "state", None)
        )
        if isinstance(state, Mapping) and state.get("client_disconnected") is True:
            return True
        if state is not None and getattr(state, "client_disconnected", False) is True:
            return True
    return False


def _format_validation_error(exception: RequestValidationError) -> dict[str, Any]:
    messages: list[str] = []
    for err in exception.errors():
        loc_parts = [str(loc) for loc in err.get("loc", ()) if loc != "body"]
        prefix = ".".join(loc_parts)
        msg_text = str(err.get("msg", "Invalid value"))
        messages.append(f"{prefix}: {msg_text}" if prefix else msg_text)
    if len(messages) > 1:
        return {"message": messages}
    return {"message": messages[0] if messages else "Validation failed"}


def _extract_http_status_and_raw_res(
    exception: object,
) -> tuple[int, Any]:
    if isinstance(exception, (HTTPException, StarletteHTTPException)):
        return int(exception.status_code), exception.detail
    if isinstance(exception, RequestValidationError):
        return 400, _format_validation_error(exception)

    get_status = getattr(exception, "get_status", None) or getattr(
        exception, "getStatus", None
    )
    get_response = getattr(exception, "get_response", None) or getattr(
        exception, "getResponse", None
    )
    if callable(get_status):
        status = int(get_status())
        res = (
            get_response()
            if callable(get_response)
            else getattr(exception, "detail", str(exception))
        )
        return status, res

    status_code_attr = getattr(exception, "status_code", None) or getattr(
        exception, "statusCode", None
    )
    if isinstance(status_code_attr, int) and not isinstance(status_code_attr, bool):
        res = getattr(
            exception,
            "response",
            getattr(
                exception,
                "detail",
                getattr(exception, "message", str(exception)),
            ),
        )
        return status_code_attr, res

    return 500, "Internal server error"


def _set_response_retry_after(response: Any, retry_after_val: str) -> None:
    if response is None:
        return
    header_fn = getattr(response, "header", None) or getattr(
        response, "setHeader", None
    )
    if callable(header_fn):
        header_fn("Retry-After", retry_after_val)


def _format_res_message(res: Any) -> Any:
    if isinstance(res, str):
        return res
    if isinstance(res, Mapping):
        return res.get("message", json.dumps(dict(res), default=str))
    return json.dumps(res, default=str)


def _now_iso_utc() -> str:
    return (
        datetime.datetime.now(datetime.UTC)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


class AllExceptionsFilter:
    """Global exception filter matching NestJS AllExceptionsFilter wire contracts."""

    def __init__(self) -> None:
        self._logger = get_logger("AllExceptionsFilter")

    def map_deadline_exception(
        self,
        exception: object,
    ) -> tuple[int, str] | None:
        cls_name = type(exception).__name__
        exc_name = getattr(exception, "name", cls_name)

        if (
            isinstance(exception, RequestDeadlineExceededError)
            or cls_name == "RequestDeadlineExceededError"
            or exc_name == "RequestDeadlineExceededError"
        ):
            msg = (
                getattr(exception, "message", None)
                or str(exception)
                or "Request deadline exceeded"
            )
            return 504, str(msg)

        if (
            isinstance(exception, RequestCancelledError)
            or cls_name == "RequestCancelledError"
            or exc_name == "RequestCancelledError"
        ):
            msg = (
                getattr(exception, "message", None)
                or str(exception)
                or "Request cancelled by client"
            )
            return HTTP_STATUS_CLIENT_CLOSED_REQUEST, str(msg)

        is_abort = isinstance(exception, Exception) and (
            exc_name == "AbortError" or cls_name == "AbortError"
        )
        if not is_abort:
            return None

        reason = get_deadline_reason() or getattr(exception, "reason", None)
        if reason == "client_disconnected":
            return HTTP_STATUS_CLIENT_CLOSED_REQUEST, "Request cancelled by client"
        if reason == "timeout":
            return 504, "Request deadline exceeded"
        return None

    def map_overload_exception(
        self,
        exception: object,
        request_url: str,
    ) -> tuple[int, str, dict[str, str]] | None:
        cls_name = type(exception).__name__
        exc_name = getattr(exception, "name", cls_name)
        retry_after = str(resolve_overload_retry_after_sec())
        headers = {"Retry-After": retry_after}

        if (
            isinstance(exception, PriorityShedRejectedError)
            or cls_name == "PriorityShedRejectedError"
            or exc_name == "PriorityShedRejectedError"
        ):
            msg = (
                getattr(exception, "message", None)
                or str(exception)
                or "Service unavailable: request priority shed under load"
            )
            record_api_priority_shed_503(request_url)
            return 503, str(msg), headers

        if (
            isinstance(exception, LoadLevelQueueFullError)
            or cls_name == "LoadLevelQueueFullError"
            or exc_name == "LoadLevelQueueFullError"
        ):
            msg = (
                getattr(exception, "message", None)
                or str(exception)
                or "Inbound load leveling queue is full"
            )
            record_inbound_load_level_queue_full_503(request_url)
            return 503, str(msg), headers

        if (
            isinstance(exception, LoadLevelWaitTimeoutError)
            or cls_name == "LoadLevelWaitTimeoutError"
            or exc_name == "LoadLevelWaitTimeoutError"
        ):
            msg = (
                getattr(exception, "message", None)
                or str(exception)
                or "Timed out waiting for inbound capacity"
            )
            record_inbound_load_level_wait_timeout_503(request_url)
            return 503, str(msg), headers

        if (
            isinstance(exception, OverloadRejectedError)
            or cls_name == "OverloadRejectedError"
            or exc_name == "OverloadRejectedError"
        ):
            msg = (
                getattr(exception, "message", None)
                or str(exception)
                or "Service temporarily unavailable"
            )
            record_inbound_bulkhead_503(request_url)
            return 503, str(msg), headers

        return None

    def _log_exception(
        self,
        status: int,
        message: Any,
        exception: object,
    ) -> None:
        if status in (504, HTTP_STATUS_CLIENT_CLOSED_REQUEST):
            self._logger.warn(f"Exception {status} - {message}")
            return
        stack = (
            "".join(
                traceback.format_exception(
                    type(exception),
                    exception,
                    exception.__traceback__,
                )
            )
            if isinstance(exception, BaseException)
            else ""
        )
        self._logger.error(f"Exception {status} - {message}", stack=stack)

    def handle_exception(
        self,
        exception: object,
        request: Request | Mapping[str, Any] | Any,
        response: Any = None,
        *,
        context_type: str = "http",
    ) -> tuple[int, dict[str, Any], dict[str, str]] | None:
        """Process an exception into (status, payload, headers) or None if closed."""
        request_url = _extract_path(request)
        request_id = _extract_request_id(request)
        status, res = _extract_http_status_and_raw_res(exception)

        extra_headers: dict[str, str] = {}
        exc_headers = getattr(exception, "headers", None)
        if isinstance(exc_headers, Mapping):
            extra_headers.update(
                {str(k): str(v) for k, v in exc_headers.items() if v is not None}
            )

        message = _format_res_message(res)

        if is_database_connection_error(exception):
            status = 503
            message = "Service temporarily unavailable"
            record_dependency_unavailable_503(request_url)

        deadline_mapped = self.map_deadline_exception(exception)
        if deadline_mapped is not None:
            status, message = deadline_mapped

        overload_mapped = self.map_overload_exception(exception, request_url)
        if overload_mapped is not None:
            status, message, overload_headers = overload_mapped
            extra_headers.update(overload_headers)
            _set_response_retry_after(response, overload_headers["Retry-After"])

        if status == HTTP_STATUS_CLIENT_CLOSED_REQUEST and _is_response_destroyed(
            request, response
        ):
            self._logger.warn(
                f"Exception {status} - {message} (response already closed)"
            )
            return None

        self._log_exception(status, message, exception)

        if context_type == "graphql":
            return status, {"exception": exception}, extra_headers

        now_iso = _now_iso_utc()
        if status == 503 and isinstance(res, Mapping) and "details" in res:
            record_health_check_503(request_url)
            error_response: dict[str, Any] = {
                "statusCode": status,
                "timestamp": now_iso,
                "path": request_url,
                "requestId": request_id,
                **dict(res),
            }
        else:
            error_response = {
                "statusCode": status,
                "timestamp": now_iso,
                "path": request_url,
                "requestId": request_id,
                "message": message,
            }

        return status, error_response, extra_headers

    async def __call__(
        self,
        request: Request,
        exc: Exception,
    ) -> Response:
        result = self.handle_exception(exc, request)
        if result is None:
            return Response(status_code=HTTP_STATUS_CLIENT_CLOSED_REQUEST)
        status, payload, headers = result
        return JSONResponse(
            status_code=status,
            content=payload,
            headers=headers,
        )


class AllExceptionsMiddleware:
    """Pure ASGI catch-all exception filter middleware."""

    def __init__(
        self,
        app: ASGIApp,
        exception_filter: AllExceptionsFilter | None = None,
    ) -> None:
        self.app = app
        self._filter = exception_filter or AllExceptionsFilter()

    async def __call__(
        self,
        scope: Scope,
        receive: Receive,
        send: Send,
    ) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        response_started = False

        async def send_wrapper(message: Message) -> None:
            nonlocal response_started
            if message["type"] == "http.response.start":
                response_started = True
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        except Exception as exc:
            if response_started:
                raise
            request = Request(scope, receive=receive)
            result = self._filter.handle_exception(exc, request)
            if result is None:
                return
            status, payload, headers = result
            response = JSONResponse(
                status_code=status,
                content=payload,
                headers=headers,
            )
            await response(scope, receive, send)


def register_exception_handlers(
    app: FastAPI,
    exception_filter: AllExceptionsFilter | None = None,
) -> AllExceptionsFilter:
    """Register AllExceptionsFilter on FastAPI for HTTP and domain exceptions."""
    exc_filter = exception_filter or AllExceptionsFilter()

    async def _http_exc_handler(request: Request, exc: Exception) -> Response:
        return await exc_filter(request, exc)

    app.add_exception_handler(StarletteHTTPException, _http_exc_handler)
    app.add_exception_handler(HTTPException, _http_exc_handler)
    app.add_exception_handler(RequestValidationError, _http_exc_handler)
    app.add_exception_handler(RequestDeadlineExceededError, _http_exc_handler)
    app.add_exception_handler(RequestCancelledError, _http_exc_handler)
    app.add_exception_handler(PriorityShedRejectedError, _http_exc_handler)
    app.add_exception_handler(LoadLevelQueueFullError, _http_exc_handler)
    app.add_exception_handler(LoadLevelWaitTimeoutError, _http_exc_handler)
    app.add_exception_handler(OverloadRejectedError, _http_exc_handler)
    app.add_exception_handler(Exception, _http_exc_handler)
    return exc_filter


__all__ = [
    "AllExceptionsFilter",
    "AllExceptionsMiddleware",
    "LoadLevelQueueFullError",
    "LoadLevelWaitTimeoutError",
    "OverloadRejectedError",
    "PriorityShedRejectedError",
    "is_database_connection_error",
    "register_exception_handlers",
    "resolve_overload_retry_after_sec",
]
