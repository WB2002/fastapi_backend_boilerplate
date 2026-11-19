from __future__ import annotations

import re
from typing import Final

SLACK_WEBHOOK_URL_ENV: Final[str] = "SLACK_WEBHOOK_URL"
SLACK_WEBHOOK_TIMEOUT_MS: Final[int] = 5_000
SLACK_WEBHOOK_HOST: Final[str] = "hooks.slack.com"
SLACK_HTTP_SERVICE_NAME: Final[str] = "slack-incoming-webhook"

NOTIFICATION_PREFERRED_METADATA_KEYS: Final[list[str]] = [
    "operationId",
    "userId",
    "durationMs",
    "operationStatus",
]

NOTIFICATION_MAX_METADATA_STRING_LENGTH: Final[int] = 200

SENSITIVE_METADATA_KEY_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"(password|passwd|token|secret|captcha|credential|authorization|auth|api[_-]?key|private[_-]?key)",
    re.I,
)
