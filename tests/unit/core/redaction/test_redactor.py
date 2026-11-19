from __future__ import annotations

import datetime
import json
from typing import Any

from app.core.redaction import (
    MAX_DEPTH,
    REDACTED,
    REDACTED_DEPTH,
    is_sensitive_header_name,
    is_sensitive_log_key,
    is_sensitive_query_key,
    looks_like_sensitive_value,
    parse_log_redaction_config,
    redact_for_log,
    redact_headers,
    redact_log_message,
    redact_otel_attribute_value,
    redact_otel_attributes,
    redact_query,
    redact_url,
)


class TestSensitiveKeys:
    def test_sensitive_log_keys_exact_and_normalized(self) -> None:
        for key in (
            "password",
            "currentPassword",
            "new_password",
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
            "x-api-key",
            "xApiKey",
            "privateKey",
            "custom_key",
        ):
            assert is_sensitive_log_key(key) is True, f"Expected {key} to be sensitive"

        for safe_key in ("requestId", "userId", "status", "monkey", "donkey", "path"):
            assert is_sensitive_log_key(safe_key) is False

    def test_sensitive_header_names(self) -> None:
        assert is_sensitive_header_name("Authorization") is True
        assert is_sensitive_header_name("Proxy-Authorization") is True
        assert is_sensitive_header_name("X-Api-Key") is True
        assert is_sensitive_header_name("X-Partner-Key") is True
        assert is_sensitive_header_name("Cookie") is True
        assert is_sensitive_header_name("Set-Cookie") is True
        assert is_sensitive_header_name("Content-Type") is False
        assert is_sensitive_header_name("X-Request-Id") is False

    def test_sensitive_query_keys(self) -> None:
        for qk in (
            "token",
            "access_token",
            "refresh_token",
            "password",
            "passwd",
            "secret",
            "client_secret",
            "api_key",
            "apikey",
            "key",
            "sig",
            "signature",
            "credential",
        ):
            assert is_sensitive_query_key(qk) is True
        assert is_sensitive_query_key("page") is False
        assert is_sensitive_query_key("ok") is False


class TestRedactUrlHeadersQuery:
    def test_redacts_headers_and_joins_arrays(self) -> None:
        assert redact_headers(None) == {}
        redacted = redact_headers(
            {
                "X-Partner-Key": "sekrit",
                "Accept": ["application/json", "text/plain"],
                "X-Empty": None,
            }
        )
        assert redacted == {
            "X-Partner-Key": REDACTED,
            "Accept": "application/json, text/plain",
        }

    def test_redacts_query_keys(self) -> None:
        assert redact_query(None) == {}
        assert redact_query({"key": "sekrit", "sig": "abc", "ok": 1}) == {
            "key": REDACTED,
            "sig": REDACTED,
            "ok": 1,
        }

    def test_redacts_url_userinfo_and_query_params(self) -> None:
        redacted = redact_url(
            "https://user:pass@example.com/x?access_token=sekrit&ok=1#frag"
        )
        assert "sekrit" not in redacted
        assert "user:pass" not in redacted
        assert "ok=1" in redacted
        assert redacted.endswith("#frag")

    def test_redacts_relative_url_with_query(self) -> None:
        redacted = redact_url("/auth/callback?access_token=sekrit&ok=1")
        assert "sekrit" not in redacted
        assert redacted.startswith("/auth/callback?")
        assert "ok=1" in redacted

    def test_invalid_or_plain_url_returned_unchanged(self) -> None:
        assert redact_url("") == ""
        assert redact_url("not_a_url") == "not_a_url"

    def test_redacts_log_message_urls(self) -> None:
        line = "GET /auth/callback?access_token=sekrit&ok=1 302 - 4ms"
        out = redact_log_message(line)
        assert "sekrit" not in out
        assert "ok=1" in out

        abs_line = "Calling https://admin:hunter2@api.example.com/v1?api_key=123&a=b"
        abs_out = redact_log_message(abs_line)
        assert "hunter2" not in abs_out
        assert "123" not in abs_out
        assert "a=b" in abs_out


class TestRedactForLog:
    def test_redacts_by_object_key_name(self) -> None:
        assert redact_for_log({"email": "user@example.com", "requestId": "abc"}) == {
            "email": REDACTED,
            "requestId": "abc",
        }

    def test_redacts_email_jwt_and_bearer_strings_without_sensitive_key(self) -> None:
        assert looks_like_sensitive_value("   ") is False
        assert redact_for_log({"note": "contact user@example.com please"}) == {
            "note": REDACTED
        }
        jwt = "eyJaa.eyJbb.cc"
        assert redact_for_log({"debug": jwt}) == {"debug": REDACTED}
        assert redact_for_log({"headerVal": "Bearer secret-jwt-token"}) == {
            "headerVal": REDACTED
        }

    def test_preserves_primitives_dates_and_bytes(self) -> None:
        now = datetime.datetime.now(datetime.UTC)
        raw_bytes = b"binary"
        assert redact_for_log(
            {
                "count": 3,
                "ratio": 0.5,
                "ok": True,
                "none": None,
                "dt": now,
                "buf": raw_bytes,
            }
        ) == {
            "count": 3,
            "ratio": 0.5,
            "ok": True,
            "none": None,
            "dt": now,
            "buf": raw_bytes,
        }

    def test_enforces_max_recursion_depth_8(self) -> None:
        assert MAX_DEPTH == 8
        nested: Any = "leaf"
        for _ in range(10):
            nested = {"child": nested}
        redacted = redact_for_log(nested)
        cursor = redacted
        for _ in range(9):
            cursor = cursor["child"]
        assert cursor == REDACTED_DEPTH

    def test_handles_circular_references_and_does_not_mutate_input(self) -> None:
        cyclic_dict: dict[str, Any] = {"safe": "ok", "password": "secret"}
        cyclic_dict["self"] = cyclic_dict

        cyclic_list: list[Any] = ["ok"]
        cyclic_list.append(cyclic_list)

        redacted_dict = redact_for_log(cyclic_dict)
        assert redacted_dict["safe"] == "ok"
        assert redacted_dict["password"] == REDACTED
        assert redacted_dict["self"] == REDACTED
        assert cyclic_dict["password"] == "secret"

        redacted_list = redact_for_log(cyclic_list)
        assert redacted_list == ["ok", REDACTED]

    def test_respects_enabled_flag_and_config_parser(self) -> None:
        payload = {"password": "123", "email": "a@b.com"}
        assert redact_for_log(payload, enabled=False) == payload
        assert parse_log_redaction_config({}).enabled is True
        assert (
            parse_log_redaction_config({"LOG_REDACTION_ENABLED": "false"}).enabled
            is False
        )


class TestRedactOtelAttributes:
    def test_redacts_otel_attributes_and_header_json_blobs(self) -> None:
        jwt = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxIn0.sig"
        out = redact_otel_attributes(
            {
                "http.url": "https://api.example.com?access_token=sekrit",
                "email": "user@example.com",
                "user.password": "hunter2",
                "http.request.headers": json.dumps({"Authorization": f"Bearer {jwt}"}),
                "http.method": "GET",
                "tags": ["safe", "user@example.com", 42, True],
                "nested": {"secret": "val"},
            }
        )
        assert out["email"] == REDACTED
        assert out["user.password"] == REDACTED
        assert "sekrit" not in str(out["http.url"])
        assert jwt not in str(out["http.request.headers"])
        assert out["http.method"] == "GET"
        assert out["tags"] == ["safe", REDACTED, 42, True]
        assert REDACTED in str(out["nested"])

    def test_redact_otel_attribute_value_handles_none_and_invalid_json(self) -> None:
        assert redact_otel_attribute_value("any", None) is None
        bad_json = redact_otel_attribute_value(
            "http.request.headers", "not-json-user@example.com"
        )
        assert bad_json == REDACTED
