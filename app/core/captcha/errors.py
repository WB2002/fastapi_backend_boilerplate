from __future__ import annotations


class CaptchaConfigurationError(Exception):
    def __init__(self, message: str = "Captcha is not configured") -> None:
        super().__init__(message)


class CaptchaTimeoutError(Exception):
    def __init__(self, message: str = "Captcha verification timed out") -> None:
        super().__init__(message)


class CaptchaProviderUnavailableError(Exception):
    def __init__(self, message: str = "Captcha provider unavailable") -> None:
        super().__init__(message)


class CaptchaMalformedResponseError(Exception):
    def __init__(
        self, message: str = "Captcha provider returned a malformed response"
    ) -> None:
        super().__init__(message)
