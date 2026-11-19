from typing import NotRequired, TypedDict


class JwtPayload(TypedDict):
    sub: str
    email: str
    jti: str
    role: str
    iat: NotRequired[int]
    exp: NotRequired[int]
