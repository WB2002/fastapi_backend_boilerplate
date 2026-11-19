from __future__ import annotations

from hypothesis import given
from hypothesis import strategies as st

from app.common.constants import (
    ACCESS_TOKEN_COOKIE,
    CSRF_TOKEN_COOKIE,
    SESSION_ID_COOKIE,
)
from app.core.csrf import (
    CSRF_FORBIDDEN_BODY,
    CsrfMiddleware,
    CsrfTokenService,
    csrf_tokens_match,
    generate_csrf_token,
    should_enforce_csrf,
)

_TOKEN_ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"


@given(st.integers(min_value=1, max_value=20))
def test_generated_csrf_tokens_are_unique_43_char_base64url(count: int) -> None:
    tokens = [generate_csrf_token() for _ in range(count)]
    for tok in tokens:
        assert len(tok) == 43
        assert all(ch in _TOKEN_ALPHABET for ch in tok)
        assert csrf_tokens_match(tok, tok) is True
    assert len(set(tokens)) == count


@given(
    token=st.text(alphabet=_TOKEN_ALPHABET, min_size=1, max_size=86),
    other=st.text(alphabet=_TOKEN_ALPHABET, min_size=1, max_size=86),
)
def test_csrf_tokens_match_is_reflexive_and_symmetric(
    token: str,
    other: str,
) -> None:
    svc = CsrfTokenService()
    assert svc.tokens_match(token, token) is True
    assert svc.tokens_match(token, other) == (token == other)
    assert svc.tokens_match(token, other) == svc.tokens_match(other, token)


@given(
    method=st.sampled_from(
        ["GET", "HEAD", "OPTIONS", "POST", "PUT", "PATCH", "DELETE"]
    ),
    has_bearer=st.booleans(),
    has_access_cookie=st.booleans(),
    has_session_cookie=st.booleans(),
    cookie_token=st.one_of(
        st.none(),
        st.text(alphabet=_TOKEN_ALPHABET, min_size=1, max_size=43),
    ),
    header_token=st.one_of(
        st.none(),
        st.text(alphabet=_TOKEN_ALPHABET, min_size=1, max_size=43),
    ),
)
def test_csrf_middleware_enforcement_decision_property(
    method: str,
    has_bearer: bool,
    has_access_cookie: bool,
    has_session_cookie: bool,
    cookie_token: str | None,
    header_token: str | None,
) -> None:
    headers: dict[str, str] = {}
    if has_bearer:
        headers["authorization"] = "Bearer valid.jwt.token"
    if header_token is not None:
        headers["x-csrf-token"] = header_token

    cookies: dict[str, str] = {}
    if has_access_cookie:
        cookies[ACCESS_TOKEN_COOKIE] = "access-jwt"
    if has_session_cookie:
        cookies[SESSION_ID_COOKIE] = "session-id"
    if cookie_token is not None:
        cookies[CSRF_TOKEN_COOKIE] = cookie_token

    expected_enforce = (
        method in ("POST", "PUT", "PATCH", "DELETE")
        and not has_bearer
        and (has_access_cookie or has_session_cookie)
    )
    assert (
        should_enforce_csrf(method=method, headers=headers, cookies=cookies)
        == expected_enforce
    )

    mw = CsrfMiddleware()
    outcome = mw.check_request(
        method=method,
        url="/api/resource",
        headers=headers,
        cookies=cookies,
    )
    if not expected_enforce:
        assert outcome is None
    elif (
        cookie_token is not None
        and header_token is not None
        and cookie_token == header_token
    ):
        assert outcome is None
    else:
        assert outcome == CSRF_FORBIDDEN_BODY
