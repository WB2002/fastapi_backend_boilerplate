from __future__ import annotations

import copy
from typing import Any

from hypothesis import given, settings
from hypothesis import strategies as st

from app.core.redaction import (
    MAX_DEPTH,
    REDACTED,
    REDACTED_DEPTH,
    is_sensitive_log_key,
    looks_like_sensitive_value,
    redact_for_log,
    redact_url,
)

SENSITIVE_KEY_SAMPLES = [
    "password",
    "currentPassword",
    "newPassword",
    "confirmPassword",
    "token",
    "accessToken",
    "refreshToken",
    "sessionId",
    "csrfToken",
    "authorization",
    "cookie",
    "set-cookie",
    "setCookie",
    "email",
    "phone",
    "mobile",
    "phoneNumber",
    "ssn",
    "socialSecurityNumber",
    "taxId",
    "creditCard",
    "cardNumber",
    "cvv",
    "secret",
    "clientSecret",
    "apiKey",
    "xApiKey",
    "privateKey",
]

SAFE_KEY_SAMPLES = [
    "requestId",
    "userId",
    "status",
    "count",
    "enabled",
    "path",
    "durationMs",
    "monkey",
    "donkey",
]

_alpha_num = st.text(
    alphabet="abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789",
    min_size=3,
    max_size=16,
)


@given(
    sensitive_key=st.sampled_from(SENSITIVE_KEY_SAMPLES),
    secret_val=_alpha_num,
    safe_key=st.sampled_from(SAFE_KEY_SAMPLES),
    safe_val=st.integers(min_value=-1000, max_value=1000),
)
@settings(max_examples=50)
def test_sensitive_keys_always_redacted_and_input_unmutated(
    sensitive_key: str,
    secret_val: str,
    safe_key: str,
    safe_val: int,
) -> None:
    original: dict[str, Any] = {
        sensitive_key: secret_val,
        safe_key: safe_val,
        "nested": {sensitive_key: secret_val},
    }
    snapshot = copy.deepcopy(original)

    redacted = redact_for_log(original)
    assert original == snapshot
    assert redacted[sensitive_key] == REDACTED
    assert redacted["nested"][sensitive_key] == REDACTED
    assert redacted[safe_key] == safe_val


@given(
    local_part=st.from_regex(r"[a-zA-Z0-9._%+-]{2,12}", fullmatch=True),
    domain=st.from_regex(r"[a-zA-Z0-9-]{2,10}", fullmatch=True),
    tld=st.from_regex(r"[a-zA-Z]{2,5}", fullmatch=True),
)
@settings(max_examples=50)
def test_email_values_always_redacted_regardless_of_key(
    local_part: str,
    domain: str,
    tld: str,
) -> None:
    email = f"{local_part}@{domain}.{tld}"
    assert looks_like_sensitive_value(email) is True
    out = redact_for_log({"unassumingField": f"User email is {email}!"})
    assert out["unassumingField"] == REDACTED


@given(
    part1=st.from_regex(r"[A-Za-z0-9_-]{4,16}", fullmatch=True),
    part2=st.from_regex(r"[A-Za-z0-9_-]{4,16}", fullmatch=True),
    part3=st.from_regex(r"[A-Za-z0-9_-]{4,16}", fullmatch=True),
)
@settings(max_examples=50)
def test_jwt_and_bearer_values_always_redacted(
    part1: str,
    part2: str,
    part3: str,
) -> None:
    jwt = f"eyJ{part1}.eyJ{part2}.{part3}"
    assert looks_like_sensitive_value(jwt) is True
    assert redact_for_log({"data": jwt}) == {"data": REDACTED}
    assert redact_for_log({"authHeader": f"Bearer {jwt}"}) == {"authHeader": REDACTED}


@given(depth=st.integers(min_value=1, max_value=14))
@settings(max_examples=20)
def test_depth_cap_property(depth: int) -> None:
    nested: Any = "safe_leaf"
    for _ in range(depth):
        nested = {"level": nested}

    result = redact_for_log(nested)
    cursor: Any = result
    steps = 0
    while isinstance(cursor, dict) and "level" in cursor:
        cursor = cursor["level"]
        steps += 1

    if depth <= MAX_DEPTH:
        assert steps == depth
        assert cursor == "safe_leaf"
    else:
        assert steps == MAX_DEPTH + 1
        assert cursor == REDACTED_DEPTH


@given(
    user=_alpha_num,
    password=_alpha_num,
    token=_alpha_num,
    safe_param=_alpha_num,
)
@settings(max_examples=40)
def test_url_redaction_strips_userinfo_and_query_secrets(
    user: str,
    password: str,
    token: str,
    safe_param: str,
) -> None:
    raw_url = (
        f"https://{user}:{password}@example.com/api/callback"
        f"?access_token={token}&state={safe_param}"
    )
    redacted = redact_url(raw_url)
    assert f"{user}:{password}" not in redacted
    assert f"access_token={token}" not in redacted
    assert f"state={safe_param}" in redacted
    assert is_sensitive_log_key("password") is True
