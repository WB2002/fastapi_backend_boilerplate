from __future__ import annotations

import json
from typing import Any

from app.core.notification.constants import (
    NOTIFICATION_MAX_METADATA_STRING_LENGTH,
    NOTIFICATION_PREFERRED_METADATA_KEYS,
    SENSITIVE_METADATA_KEY_PATTERN,
)
from app.core.notification.types import INotification


def escape_mrkdwn(value: str) -> str:
    return value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def is_sensitive_metadata_key(key: str) -> bool:
    return bool(SENSITIVE_METADATA_KEY_PATTERN.search(key))


def label_for_preferred_key(key: str) -> str:
    labels = {
        "operationId": "Operation ID",
        "userId": "User ID",
        "durationMs": "Duration (ms)",
        "operationStatus": "Operation status",
    }
    return labels.get(key, key)


def truncate(value: str) -> str:
    if len(value) <= NOTIFICATION_MAX_METADATA_STRING_LENGTH:
        return value
    return f"{value[:NOTIFICATION_MAX_METADATA_STRING_LENGTH]}…"


def format_metadata_value(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, str):
        return escape_mrkdwn(truncate(value))
    if isinstance(value, (int, float, bool)):
        return str(value)
    try:
        return escape_mrkdwn(truncate(json.dumps(value)))
    except Exception:
        return "[unserializable]"


def format_slack_message_text(notification: INotification) -> str:
    lines: list[str] = []

    if notification.title and notification.title.strip():
        lines.append(f"*{escape_mrkdwn(notification.title.strip())}*")
        lines.append("")

    lines.append(escape_mrkdwn(notification.message))

    metadata = notification.metadata
    if not metadata:
        return "\n".join(lines)

    preferred_lines: list[str] = []
    additional_lines: list[str] = []
    preferred_set = set(NOTIFICATION_PREFERRED_METADATA_KEYS)
    used_preferred: set[str] = set()

    for key in NOTIFICATION_PREFERRED_METADATA_KEYS:
        if key not in metadata or is_sensitive_metadata_key(key):
            continue
        val = metadata[key]
        if val is None:
            continue
        preferred_lines.append(
            f"{label_for_preferred_key(key)}: {format_metadata_value(val)}"
        )
        used_preferred.add(key)

    for key, val in metadata.items():
        if key in preferred_set and key in used_preferred:
            continue
        if is_sensitive_metadata_key(key):
            continue
        if val is None:
            continue
        additional_lines.append(f"• {escape_mrkdwn(key)}: {format_metadata_value(val)}")

    if preferred_lines:
        lines.append("")
        lines.extend(preferred_lines)

    if additional_lines:
        lines.append("")
        lines.append("Additional metadata:")
        lines.extend(additional_lines)

    return "\n".join(lines)
