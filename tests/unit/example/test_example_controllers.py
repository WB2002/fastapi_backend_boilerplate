from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException, Request

from app.core.captcha import (
    CaptchaConfigurationError,
    CaptchaTimeoutError,
    CaptchaVerificationResult,
)
from app.example.dto import (
    CreateExampleNotificationDto,
    ExampleCaptchaRequestDto,
)
from app.example.example_captcha_controller import verify_captcha
from app.example.example_notification_controller import send_example_notification


@pytest.mark.asyncio
async def test_example_captcha_controller_success() -> None:
    captcha_svc = MagicMock()
    captcha_svc.verify = AsyncMock(return_value=CaptchaVerificationResult(success=True))

    req = MagicMock(spec=Request)
    req.client.host = "1.2.3.4"

    res = await verify_captcha(
        ExampleCaptchaRequestDto(captchaToken="valid"), req, captcha_svc
    )
    assert res == {"message": "Captcha verification successful"}


@pytest.mark.asyncio
async def test_example_captcha_controller_failures() -> None:
    captcha_svc = MagicMock()
    req = MagicMock(spec=Request)
    req.client.host = "1.2.3.4"

    # Verification failure -> 400
    captcha_svc.verify = AsyncMock(
        return_value=CaptchaVerificationResult(success=False)
    )
    with pytest.raises(HTTPException) as exc1:
        await verify_captcha(
            ExampleCaptchaRequestDto(captchaToken="invalid"), req, captcha_svc
        )
    assert exc1.value.status_code == 400

    # Timeout -> 503
    captcha_svc.verify = AsyncMock(side_effect=CaptchaTimeoutError("timeout"))
    with pytest.raises(HTTPException) as exc2:
        await verify_captcha(
            ExampleCaptchaRequestDto(captchaToken="t"), req, captcha_svc
        )
    assert exc2.value.status_code == 503

    # Config error -> 500
    captcha_svc.verify = AsyncMock(
        side_effect=CaptchaConfigurationError("not configured")
    )
    with pytest.raises(HTTPException) as exc3:
        await verify_captcha(
            ExampleCaptchaRequestDto(captchaToken="t"), req, captcha_svc
        )
    assert exc3.value.status_code == 500


@pytest.mark.asyncio
async def test_example_notification_controller() -> None:
    notif_svc = MagicMock()
    notif_svc.send = AsyncMock()

    dto = CreateExampleNotificationDto(message="Test alert", metadata={"env": "test"})
    res = await send_example_notification(dto, notif_svc)
    assert res["status"] == "success"
    assert res["notificationSent"] is True
    assert "operationId" in res
    assert notif_svc.send.await_count == 1
