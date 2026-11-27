from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request

from app.common.decorators import public
from app.core.captcha import (
    CaptchaConfigurationError,
    CaptchaMalformedResponseError,
    CaptchaProviderUnavailableError,
    CaptchaService,
    CaptchaTimeoutError,
    CaptchaVerificationOptions,
    HCaptchaService,
)
from app.example.dto import ExampleCaptchaRequestDto

router = APIRouter(tags=["Example"])


def get_captcha_service() -> CaptchaService:
    return HCaptchaService()


@router.post("/example/captcha")
@public
async def verify_captcha(
    dto: ExampleCaptchaRequestDto,
    request: Request,
    service: Annotated[CaptchaService, Depends(get_captcha_service)],
) -> dict[str, str]:
    try:
        remote_ip = None
        if request.client:
            remote_ip = request.client.host
        res = await service.verify(
            dto.captchaToken,
            options=CaptchaVerificationOptions(remote_ip=remote_ip),
        )
        if not res.success:
            raise HTTPException(status_code=400, detail="Captcha verification failed")
        return {"message": "Captcha verification successful"}
    except (
        CaptchaTimeoutError,
        CaptchaProviderUnavailableError,
        CaptchaMalformedResponseError,
    ) as exc:
        raise HTTPException(
            status_code=503, detail="Captcha verification unavailable"
        ) from exc
    except CaptchaConfigurationError as exc:
        raise HTTPException(status_code=500, detail="Internal server error") from exc
