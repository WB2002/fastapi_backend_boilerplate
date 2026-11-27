from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel

from app.conformance.conformance_harness_auth_guard import (
    JwtAuthGuard,
    require_conformance_harness_token,
)
from app.conformance.dto import ConformanceRedactionRequestDto
from app.core.redaction import redact_for_log, redact_otel_attributes

_ROUTE_AUTH_GUARD = JwtAuthGuard
_ROUTE_VALIDATION = BaseModel

router = APIRouter(
    tags=["Conformance Redaction"],
    dependencies=[Depends(require_conformance_harness_token)],
)
logger = logging.getLogger("ConformanceRedactionController")

MAX_FLATTEN_DEPTH = 4


def _is_record(val: Any) -> bool:
    return isinstance(val, dict)


def _is_primitive(val: Any) -> bool:
    return isinstance(val, (str, int, float, bool))


def _flatten_to_dotted_entries(
    payload: dict[str, Any],
    prefix: str = "",
    current_depth: int = 1,
    accumulated: list[tuple[str, Any]] | None = None,
) -> list[tuple[str, Any]]:
    if accumulated is None:
        accumulated = []
    for key, child in payload.items():
        dotted = f"{prefix}.{key}" if prefix else str(key)
        if _is_record(child):
            if current_depth < MAX_FLATTEN_DEPTH:
                _flatten_to_dotted_entries(
                    child, dotted, current_depth + 1, accumulated
                )
        elif _is_primitive(child):
            accumulated.append((dotted, child))
    return accumulated


def flatten_to_dotted_keys(payload: dict[str, Any]) -> dict[str, Any]:
    return dict(_flatten_to_dotted_entries(payload))


@router.post("/conformance/redaction", status_code=status.HTTP_200_OK)
def redact(request: ConformanceRedactionRequestDto) -> dict[str, Any]:
    logger.info("Conformance redaction payload: %s", request.payload)
    redacted_payload = redact_for_log(request.payload)
    redacted_span_attrs = redact_otel_attributes(
        flatten_to_dotted_keys(request.payload)
    )
    return {
        "redactedPayload": redacted_payload,
        "redactedSpanAttributes": redacted_span_attrs,
    }
