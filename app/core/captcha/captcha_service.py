from __future__ import annotations

import logging
import os
from abc import ABC, abstractmethod

from app.core.captcha.errors import (
    CaptchaConfigurationError,
    CaptchaMalformedResponseError,
    CaptchaProviderUnavailableError,
    CaptchaTimeoutError,
)
from app.core.captcha.types import (
    CaptchaVerificationOptions,
    CaptchaVerificationResult,
)
from app.core.http_client import (
    ConfiguredHttpClient,
    HttpClient,
    HttpClientError,
    HttpNetworkError,
    HttpTimeoutError,
)

logger = logging.getLogger("CaptchaService")

HCAPTCHA_DEFAULT_VERIFY_BASE_URL = "https://api.hcaptcha.com"
HCAPTCHA_SITEVERIFY_PATH = "/siteverify"
HCAPTCHA_VERIFY_TIMEOUT_MS = 5_000


class CaptchaService(ABC):
    @abstractmethod
    async def verify(
        self,
        token: str,
        options: CaptchaVerificationOptions | None = None,
    ) -> CaptchaVerificationResult: ...


class HCaptchaService(CaptchaService):
    def __init__(
        self,
        secret: str | None = None,
        base_url: str | None = None,
        http_client: HttpClient | None = None,
    ) -> None:
        raw_secret = secret or os.getenv("HCAPTCHA_SECRET")
        if not raw_secret or not raw_secret.strip():
            raise CaptchaConfigurationError("HCAPTCHA_SECRET is not configured")
        self._secret = raw_secret.strip()

        configured_base = base_url or os.getenv("HCAPTCHA_VERIFY_BASE_URL")
        self._base_url = (
            configured_base.strip()
            if configured_base and configured_base.strip()
            else HCAPTCHA_DEFAULT_VERIFY_BASE_URL
        )

        cli = http_client or HttpClient()
        self._client: ConfiguredHttpClient = (
            cli.external("hcaptcha")
            .add_base_url(self._base_url)
            .add_timeout(HCAPTCHA_VERIFY_TIMEOUT_MS)
        )

    async def verify(
        self,
        token: str,
        options: CaptchaVerificationOptions | None = None,
    ) -> CaptchaVerificationResult:
        if not token or not token.strip():
            return CaptchaVerificationResult(
                success=False, error_codes=["missing-input-response"]
            )

        data: dict[str, str] = {
            "secret": self._secret,
            "response": token.strip(),
        }
        if options and options.remote_ip:
            data["remoteip"] = options.remote_ip.strip()

        try:
            res = await self._client.post(
                HCAPTCHA_SITEVERIFY_PATH,
                body=data,
            )
            raw = res.json()
            if not isinstance(raw, dict):
                raise CaptchaMalformedResponseError(
                    "hCaptcha response is not a JSON object"
                )

            success = bool(raw.get("success", False))
            error_codes = raw.get("error-codes") or raw.get("errorCodes") or []
            if not isinstance(error_codes, list):
                error_codes = []

            return CaptchaVerificationResult(
                success=success,
                error_codes=[str(c) for c in error_codes],
            )

        except HttpTimeoutError as exc:
            raise CaptchaTimeoutError(f"hCaptcha verify timed out: {exc}") from exc
        except (HttpNetworkError, HttpClientError) as exc:
            raise CaptchaProviderUnavailableError(
                f"hCaptcha provider error: {exc}"
            ) from exc
        except CaptchaMalformedResponseError:
            raise
        except Exception as exc:
            raise CaptchaProviderUnavailableError(
                f"hCaptcha unexpected failure: {exc}"
            ) from exc
