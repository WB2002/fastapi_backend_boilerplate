from __future__ import annotations

import datetime
import json
import os
import sys
from collections.abc import Callable, Mapping, MutableMapping
from typing import Any, Final

import structlog

from app.common.config.parsers import LogRedactionConfig, parse_log_redaction_config
from app.common.constants.app_constant import LOG_COLOR_MAPPER
from app.core.logger.logger_context import LoggerContext
from app.core.redaction.redactor import (
    REDACTED,
    is_sensitive_log_key,
    redact_for_log,
    redact_log_message,
)

WINSTON_RESERVED: Final[frozenset[str]] = frozenset(
    {"level", "message", "event", "timestamp", "stack"}
)


def create_redaction_format(
    config: LogRedactionConfig | Mapping[str, Any] | bool | None = None,
) -> Callable[[MutableMapping[str, Any]], dict[str, Any]]:
    """Create a redaction transform function matching NestJS createRedactionFormat."""
    if config is None:
        enabled = parse_log_redaction_config(os.environ).enabled
    elif isinstance(config, bool):
        enabled = config
    elif isinstance(config, LogRedactionConfig):
        enabled = config.enabled
    else:
        enabled = bool(config.get("enabled", True))

    def _transform(info: MutableMapping[str, Any]) -> dict[str, Any]:
        out = dict(info)
        if not enabled:
            return out
        if isinstance(out.get("message"), str):
            out["message"] = redact_log_message(out["message"])
        if isinstance(out.get("event"), str):
            out["event"] = redact_log_message(out["event"])
        if isinstance(out.get("stack"), str):
            out["stack"] = redact_log_message(out["stack"])

        for key, value in list(out.items()):
            if key in WINSTON_RESERVED:
                continue
            if is_sensitive_log_key(key):
                out[key] = REDACTED
            else:
                out[key] = redact_for_log(value, enabled=enabled)
        return out

    return _transform


def context_formatter_processor(
    _logger: Any,
    _method_name: str,
    event_dict: MutableMapping[str, Any],
) -> MutableMapping[str, Any]:
    """Merge LoggerContext fields into structlog event dict."""
    storage_ctx = LoggerContext.get()
    for key, val in storage_ctx.items():
        if val is not None:
            event_dict[key] = val
    return event_dict


def redaction_processor(
    _logger: Any,
    _method_name: str,
    event_dict: MutableMapping[str, Any],
) -> MutableMapping[str, Any]:
    """Structlog processor applying PII redaction when enabled."""
    config = parse_log_redaction_config(os.environ)
    transform = create_redaction_format(config)
    return transform(event_dict)


def format_dev_log_line(info: Mapping[str, Any]) -> str:
    """Format a single-line ANSI-colorized dev log entry matching Winston devFormat."""
    ts = str(
        info.get("timestamp")
        or datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%d %H:%M:%S")
    )
    level = str(info.get("level", "info"))
    message = str(info.get("message") or info.get("event") or "")
    context_val = info.get("context")
    method_val = info.get("method")
    stack = info.get("stack")

    class_name = ""
    method_name = str(method_val) if isinstance(method_val, str) else ""
    if isinstance(context_val, str):
        class_name = context_val
    elif isinstance(context_val, Mapping):
        class_name = str(context_val.get("context", ""))
        if not method_name:
            method_name = str(context_val.get("method", ""))

    label_parts = [p for p in (class_name, method_name) if p]
    label = "::".join(label_parts)
    formatted_context = (
        f" {LOG_COLOR_MAPPER['context']}[{label}]{LOG_COLOR_MAPPER['reset']}"
        if label
        else ""
    )

    request_id = info.get("requestId")
    user_id = info.get("userId")
    tracing_parts: list[str] = []
    if request_id:
        tracing_parts.append(
            f"{LOG_COLOR_MAPPER['requestId']}requestId: "
            f"{LOG_COLOR_MAPPER['reset']}{request_id}"
        )
    if user_id:
        tracing_parts.append(
            f"{LOG_COLOR_MAPPER['userId']}userId: {LOG_COLOR_MAPPER['reset']}{user_id}"
        )
    tracing_info = " ".join(tracing_parts)
    formatted_tracing = f" ({tracing_info})" if tracing_info else ""

    excluded_keys = {
        "timestamp",
        "level",
        "message",
        "event",
        "context",
        "method",
        "stack",
        "requestId",
        "userId",
    }
    remaining_meta = {
        k: v for k, v in info.items() if k not in excluded_keys and v is not None
    }
    formatted_meta = (
        f" {json.dumps(remaining_meta, default=str)}" if remaining_meta else ""
    )
    stack_suffix = f"\n{stack}" if stack else ""

    return (
        f"{LOG_COLOR_MAPPER['timestamp']}{ts}{LOG_COLOR_MAPPER['reset']}"
        f"{formatted_context} {level}: {message}"
        f"{formatted_tracing}{formatted_meta}{stack_suffix}"
    )


def format_prod_log_line(info: Mapping[str, Any]) -> str:
    """Format a single-line JSON log entry matching Winston prodFormat."""
    payload = dict(info)
    if "timestamp" not in payload:
        now_iso = (
            datetime.datetime.now(datetime.UTC)
            .isoformat(timespec="milliseconds")
            .replace("+00:00", "Z")
        )
        payload["timestamp"] = now_iso
    if "message" not in payload and "event" in payload:
        payload["message"] = payload.pop("event")
    elif "event" in payload and payload.get("event") == payload.get("message"):
        payload.pop("event", None)
    if "level" not in payload:
        payload["level"] = "info"
    return json.dumps(payload, default=str)


def configure_structlog(env: Mapping[str, str | None] | None = None) -> None:
    """Configure structlog processor pipeline."""
    _ = env
    structlog.configure(
        processors=[
            structlog.stdlib.add_log_level,
            redaction_processor,
            context_formatter_processor,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.BoundLogger,
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=False,
    )


class StructuredLogger:
    """Winston-compatible structured logger writing redacted JSON or dev lines."""

    def __init__(self, context: str | None = None) -> None:
        self._context = context

    def _emit(
        self,
        level: str,
        message: str | Mapping[str, Any],
        stack: str | None = None,
        **extra: Any,
    ) -> dict[str, Any]:
        node_env = os.environ.get("NODE_ENV", "development")
        redaction_cfg = parse_log_redaction_config(os.environ)
        redact_fn = create_redaction_format(redaction_cfg)

        now_utc = datetime.datetime.now(datetime.UTC)
        timestamp = (
            now_utc.isoformat(timespec="milliseconds").replace("+00:00", "Z")
            if node_env == "production"
            else now_utc.strftime("%Y-%m-%d %H:%M:%S")
        )

        raw_info: dict[str, Any] = {
            "timestamp": timestamp,
            "level": level,
        }
        if self._context:
            raw_info["context"] = self._context

        if isinstance(message, Mapping):
            msg_text = message.get("message")
            raw_info["message"] = str(msg_text) if msg_text is not None else ""
            for k, v in message.items():
                if k != "message":
                    raw_info[k] = v
        else:
            raw_info["message"] = str(message)

        if stack:
            raw_info["stack"] = stack

        for k, v in extra.items():
            raw_info[k] = v

        redacted = redact_fn(raw_info)
        ctx = LoggerContext.get()
        for k, v in ctx.items():
            if v is not None:
                redacted[k] = v
        if self._context and "context" not in redacted:
            redacted["context"] = self._context

        line = (
            format_prod_log_line(redacted)
            if node_env == "production"
            else format_dev_log_line(redacted)
        )
        sys.stdout.write(f"{line}\n")
        sys.stdout.flush()
        return redacted

    def log(
        self,
        message: str | Mapping[str, Any],
        **extra: Any,
    ) -> dict[str, Any]:
        return self._emit("info", message, **extra)

    def info(
        self,
        message: str | Mapping[str, Any],
        **extra: Any,
    ) -> dict[str, Any]:
        return self._emit("info", message, **extra)

    def warn(
        self,
        message: str | Mapping[str, Any],
        **extra: Any,
    ) -> dict[str, Any]:
        return self._emit("warn", message, **extra)

    def warning(
        self,
        message: str | Mapping[str, Any],
        **extra: Any,
    ) -> dict[str, Any]:
        return self._emit("warn", message, **extra)

    def error(
        self,
        message: str | Mapping[str, Any],
        stack: str | None = None,
        **extra: Any,
    ) -> dict[str, Any]:
        return self._emit("error", message, stack=stack, **extra)

    def debug(
        self,
        message: str | Mapping[str, Any],
        **extra: Any,
    ) -> dict[str, Any]:
        return self._emit("debug", message, **extra)


def get_logger(context: str | None = None) -> StructuredLogger:
    """Return a StructuredLogger bound to `context`."""
    return StructuredLogger(context)


__all__ = [
    "WINSTON_RESERVED",
    "StructuredLogger",
    "configure_structlog",
    "context_formatter_processor",
    "create_redaction_format",
    "format_dev_log_line",
    "format_prod_log_line",
    "get_logger",
    "redaction_processor",
]
