from typing import Final

LOG_COLOR_MAPPER: Final[dict[str, str]] = {
    "timestamp": "\x1b[33m",
    "context": "\x1b[35m",
    "requestId": "\x1b[32m",
    "userId": "\x1b[36m",
    "reset": "\x1b[0m",
}
