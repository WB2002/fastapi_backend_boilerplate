from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.core.notification import (
    INotification,
    SlackNotificationService,
    escape_mrkdwn,
    format_slack_message_text,
    is_sensitive_metadata_key,
)


def test_slack_formatting() -> None:
    assert escape_mrkdwn("foo & bar <baz>") == "foo &amp; bar &lt;baz&gt;"
    assert is_sensitive_metadata_key("password") is True
    assert is_sensitive_metadata_key("api_key") is True
    assert is_sensitive_metadata_key("userId") is False

    notification = INotification(
        title="Alert",
        message="System failure",
        metadata={
            "operationId": "op-123",
            "userId": "usr-456",
            "password": "secret-password",  # should be redacted
            "customKey": "customValue",
        },
    )
    formatted = format_slack_message_text(notification)
    assert "*Alert*" in formatted
    assert "System failure" in formatted
    assert "Operation ID: op-123" in formatted
    assert "User ID: usr-456" in formatted
    assert "password" not in formatted
    assert "• customKey: customValue" in formatted


@pytest.mark.asyncio
async def test_slack_notification_service() -> None:
    # Invalid host raises
    with pytest.raises(ValueError, match="Invalid Slack webhook URL"):
        SlackNotificationService("https://malicious.com/webhook")

    # Valid host
    mock_http = MagicMock()
    mock_configured = MagicMock()
    mock_configured.post = AsyncMock(return_value=MagicMock(status_code=200, text="ok"))
    mock_http.external.return_value.add_timeout.return_value = mock_configured

    svc = SlackNotificationService(
        "https://hooks.slack.com/services/T00/B00/X00", http_client=mock_http
    )
    await svc.send(INotification(message="Hello from test"))
    assert mock_configured.post.await_count == 1
