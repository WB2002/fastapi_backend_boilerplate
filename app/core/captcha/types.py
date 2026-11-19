from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(slots=True)
class CaptchaVerificationOptions:
    remote_ip: str | None = None

    @property
    def remoteIp(self) -> str | None:
        return self.remote_ip


@dataclass(slots=True)
class CaptchaVerificationResult:
    success: bool
    error_codes: list[str] = field(default_factory=list)

    @property
    def errorCodes(self) -> list[str]:
        return self.error_codes
