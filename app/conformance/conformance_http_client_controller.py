from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, status

from app.conformance.assert_loopback_target_url import assert_loopback_target_url
from app.conformance.conformance_harness_auth_guard import (
    require_conformance_harness_token,
)
from app.conformance.conformance_http_client_service import (
    ConformanceHttpClientService,
)
from app.conformance.dto import ConformanceHttpClientRequestDto

router = APIRouter(
    tags=["Conformance HTTP Client"],
    dependencies=[Depends(require_conformance_harness_token)],
)

_client_service: ConformanceHttpClientService | None = None


def set_conformance_http_client_service(
    service: ConformanceHttpClientService | None,
) -> None:
    global _client_service
    _client_service = service


def get_conformance_http_client_service() -> ConformanceHttpClientService:
    if _client_service is None:
        raise RuntimeError("ConformanceHttpClientService not configured")
    return _client_service


@router.post("/conformance/http-client", status_code=status.HTTP_200_OK)
async def execute(
    request: ConformanceHttpClientRequestDto,
    service: Annotated[
        ConformanceHttpClientService,
        Depends(get_conformance_http_client_service),
    ],
) -> dict[str, Any]:
    assert_loopback_target_url(request.targetUrl)
    return await service.execute(request)
