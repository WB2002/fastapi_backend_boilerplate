from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.core.captcha import (
    CaptchaConfigurationError,
    CaptchaMalformedResponseError,
    CaptchaTimeoutError,
    CaptchaVerificationOptions,
    HCaptchaService,
)
from app.core.http_client import HttpTimeoutError


def test_hcaptcha_missing_secret() -> None:
    with pytest.raises(CaptchaConfigurationError):
        HCaptchaService(secret="")


@pytest.mark.asyncio
async def test_hcaptcha_verify_success_and_failure() -> None:
    mock_http = MagicMock()
    mock_configured = MagicMock()
    chain = mock_http.external.return_value.add_base_url.return_value
    chain.add_timeout.return_value = mock_configured

    # 1. Success response
    mock_res_ok = MagicMock()
    mock_res_ok.json.return_value = {"success": True, "error-codes": []}
    mock_configured.post = AsyncMock(return_value=mock_res_ok)

    service = HCaptchaService(secret="test-secret", http_client=mock_http)
    res = await service.verify(
        "good-token", CaptchaVerificationOptions(remote_ip="1.2.3.4")
    )
    assert res.success is True
    assert res.error_codes == []

    # 2. Failure response with error codes
    mock_res_fail = MagicMock()
    mock_res_fail.json.return_value = {
        "success": False,
        "error-codes": ["invalid-input-response"],
    }
    mock_configured.post = AsyncMock(return_value=mock_res_fail)

    res_fail = await service.verify("bad-token")
    assert res_fail.success is False
    assert res_fail.error_codes == ["invalid-input-response"]

    # 3. Timeout error
    mock_configured.post = AsyncMock(side_effect=HttpTimeoutError("timed out"))
    with pytest.raises(CaptchaTimeoutError):
        await service.verify("token")

    # 4. Malformed JSON
    mock_res_malformed = MagicMock()
    mock_res_malformed.json.return_value = "not-a-dict"
    mock_configured.post = AsyncMock(return_value=mock_res_malformed)
    with pytest.raises(CaptchaMalformedResponseError):
        await service.verify("token")
