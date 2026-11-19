from typing import NotRequired, Protocol, TypedDict

from starlette.requests import Request
from starlette.responses import Response


class AuthenticatedUser(TypedDict):
    id: str
    email: NotRequired[str]
    jti: NotRequired[str]
    role: NotRequired[str]


class RequestStateProtocol(Protocol):
    user: AuthenticatedUser | None


IRequest = Request
IResponse = Response
