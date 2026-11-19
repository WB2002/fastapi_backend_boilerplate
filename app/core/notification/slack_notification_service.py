from __future__ import annotations

import logging
import os
from urllib.parse import urlparse

from app.core.http_client import (
    HttpClient,
    HttpClientError,
    HttpNetworkError,
    HttpTimeoutError,
)
from app.core.notification.constants import (
    SLACK_HTTP_SERVICE_NAME,
    SLACK_WEBHOOK_HOST,
    SLACK_WEBHOOK_TIMEOUT_MS,
    SLACK_WEBHOOK_URL_ENV,
)
from app.core.notification.slack_message_formatter import format_slack_message_text
from app.core.notification.types import INotification, NotificationService

logger = logging.getLogger("SlackNotificationService")


class SlackNotificationService(NotificationService):
    def __init__(
        self,
        webhook_url: str | None = None,
        http_client: HttpClient | None = None,
    ) -> None:
        raw_url = webhook_url or os.getenv(SLACK_WEBHOOK_URL_ENV)
        self._webhook_url = raw_url.strip() if raw_url and raw_url.strip() else None
        self._http_client = http_client or HttpClient()

        if self._webhook_url:
            parsed = urlparse(self._webhook_url)
            if parsed.scheme != "https" or parsed.hostname != SLACK_WEBHOOK_HOST:
                raise ValueError(
                    f"Invalid Slack webhook URL: host must be {SLACK_WEBHOOK_HOST}"
                )

    async def send(self, notification: INotification) -> None:
        if not self._webhook_url:
            logger.debug("Slack notification skipped: webhook URL not configured")
            return

        formatted = format_slack_message_text(notification)
        payload = {"text": formatted}

        client = self._http_client.external(SLACK_HTTP_SERVICE_NAME).add_timeout(
            SLACK_WEBHOOK_TIMEOUT_MS
        )

        try:
            res = await client.post(self._webhook_url, json=payload)
            if res.status_code != 200:
                logger.warning(
                    "Slack webhook returned status %d: %s",
                    res.status_code,
                    res.text,
                )
        except (HttpTimeoutError, HttpNetworkError, HttpClientError) as exc:
            logger.warning("Failed to deliver Slack notification: %s", exc)
        except Exception as exc:
            logger.warning("Unexpected error sending Slack notification: %s", exc)
