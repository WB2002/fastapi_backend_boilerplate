from typing import Any, TypedDict


class ILoggerContext(TypedDict, total=False):
    requestId: str
    userId: str
    traceId: str
    spanId: str
    context: str
    method: str


LoggerExtra = dict[str, Any]
