from __future__ import annotations

from typing import Any

import pytest
from pydantic import Field, ValidationError

from app.common.sanitization import (
    DEFAULT_SKIP_KEYS,
    SQL_INJECTION_PATTERNS,
    BadRequestException,
    SafeUrl,
    SanitizationPipe,
    SanitizedBaseModel,
    SkipSanitization,
    SkipSqlInjectionCheck,
    UnsafeUrlError,
    assert_public_http_url,
    assert_public_http_url_async,
    looks_like_sql_injection,
    sanitize_html_string,
    validate_safe_url,
    walk_and_sanitize,
)


def test_default_skip_keys_and_sql_patterns_count() -> None:
    assert DEFAULT_SKIP_KEYS == frozenset(
        {
            "password",
            "currentPassword",
            "newPassword",
            "confirmPassword",
            "token",
            "accessToken",
            "refreshToken",
            "authorization",
        }
    )
    assert len(SQL_INJECTION_PATTERNS) == 16


def test_sanitize_html_string_strips_script_and_tags_by_default() -> None:
    assert sanitize_html_string("<script>alert(1)</script>hi") == "hi"
    assert sanitize_html_string("<b>bold</b>") == "bold"
    assert (
        sanitize_html_string(
            "<b>bold</b>",
            {"allowedTags": ["b"], "allowedAttributes": {}},
        )
        == "<b>bold</b>"
    )


def test_looks_like_sql_injection() -> None:
    assert looks_like_sql_injection("' OR 1=1 --") is True
    assert looks_like_sql_injection("1; DROP TABLE users") is True
    assert looks_like_sql_injection("UNION SELECT password FROM users") is True
    assert looks_like_sql_injection("1 AND 1=1") is True
    assert looks_like_sql_injection("pg_sleep(5)") is True
    assert looks_like_sql_injection("/* comment */") is True

    assert looks_like_sql_injection("hello world") is False
    assert looks_like_sql_injection("user@example.com") is False
    assert looks_like_sql_injection("it's fine") is False
    assert looks_like_sql_injection("a") is False


def test_assert_public_http_url() -> None:
    url = assert_public_http_url("https://example.com/path")
    assert url.hostname == "example.com"
    assert validate_safe_url("https://example.com/path") == "https://example.com/path"

    for unsafe in (
        "http://127.0.0.1/",
        "http://10.0.0.5/x",
        "http://192.168.1.1/",
        "http://169.254.169.254/latest",
        "http://100.64.0.1/",
        "http://172.16.0.1/",
        "http://224.0.0.1/",
        "http://[::ffff:127.0.0.1]/",
        "http://[::ffff:7f00:1]/",
        "http://[::ffff:10.0.0.1]/",
        "http://[::ffff:0a00:1]/",
        "http://localhost./",
        "http://sub.localhost/",
        "http://[::127.0.0.1]/",
        "http://[::7f00:1]/",
        "http://[ff02::1]/",
        "http://[fc00::1]/",
        "http://[fe80::1]/",
        "http://[2002:7f00:1::]/",
        "http://metadata.google.internal/",
        "https://user:pass@example.com/",
        "file:///etc/passwd",
        "not-a-url",
    ):
        with pytest.raises(UnsafeUrlError):
            assert_public_http_url(unsafe)
        with pytest.raises(BadRequestException):
            validate_safe_url(unsafe)


async def test_assert_public_http_url_async_dns_resolution() -> None:
    url = await assert_public_http_url_async(
        "https://example.com/hook",
        dns_lookup=lambda _host: ["93.184.216.34"],
    )
    assert url.hostname == "example.com"

    with pytest.raises(
        UnsafeUrlError,
        match="URL resolves to a private or reserved address",
    ):
        await assert_public_http_url_async(
            "https://rebind.example.com/hook",
            dns_lookup=lambda _host: ["127.0.0.1"],
        )

    with pytest.raises(UnsafeUrlError, match="URL hostname could not be resolved"):
        await assert_public_http_url_async(
            "https://nxdomain.example.com/hook",
            dns_lookup=lambda _host: [],
        )


async def test_walk_and_sanitize_nested_and_decorators() -> None:
    outcome = await walk_and_sanitize(
        {
            "name": "<b>Ada</b>",
            "password": "<script>x</script>",
            "nested": {"bio": "<img src=x onerror=alert(1)>"},
        }
    )
    assert outcome["name"] == "Ada"
    assert outcome["password"] == "<script>x</script>"
    assert outcome["nested"]["bio"] == ""

    with pytest.raises(
        BadRequestException,
        match="Potentially malicious input rejected",
    ):
        await walk_and_sanitize({"q": "' OR 1=1 --"})


async def test_walk_and_sanitize_nested_dto_metadata() -> None:
    @SkipSanitization("secret")
    @SafeUrl("link")
    class NestedDto:
        secret: str
        link: str

        def __init__(self, secret: str, link: str) -> None:
            self.secret = secret
            self.link = link

    class ParentDto:
        nested: NestedDto

        def __init__(self, nested: NestedDto) -> None:
            self.nested = nested

    parent = ParentDto(
        nested=NestedDto(secret="<script>keep</script>", link="https://8.8.8.8/path")
    )
    outcome = await walk_and_sanitize(parent, {"metadataTarget": ParentDto})
    assert outcome.nested.secret == "<script>keep</script>"
    assert outcome.nested.link == "https://8.8.8.8/path"

    bad_parent = ParentDto(nested=NestedDto(secret="x", link="http://127.0.0.1/"))
    with pytest.raises(BadRequestException):
        await walk_and_sanitize(bad_parent, {"metadataTarget": ParentDto})


async def test_walk_and_sanitize_skip_sql_injection_check() -> None:
    @SkipSqlInjectionCheck("notes")
    class DocDto:
        notes: str

        def __init__(self, notes: str) -> None:
            self.notes = notes

    dto = DocDto(notes="<b>union</b> of sets -- chapter")
    outcome = await walk_and_sanitize(dto, {"metadataTarget": DocDto})
    assert outcome.notes == "union of sets -- chapter"


async def test_sanitization_pipe_transform() -> None:
    @SkipSanitization("password")
    class SampleDto:
        password: str
        name: str

    pipe = SanitizationPipe()

    outcome = await pipe.transform(
        {"name": "<b>Ada</b>", "password": "<script>x</script>"},
        {"type": "body", "metatype": SampleDto},
    )
    assert outcome["name"] == "Ada"
    assert outcome["password"] == "<script>x</script>"

    with pytest.raises(BadRequestException):
        await pipe.transform(
            {"q": "' OR 1=1 --"},
            {"type": "query", "metatype": dict},
        )

    # Custom metadata type (@Req/@Res) with cyclic reference is returned untouched
    cyclic: dict[str, Any] = {"name": "<b>req</b>"}
    cyclic["self"] = cyclic
    custom_out = await pipe.transform(cyclic, {"type": "custom", "metatype": dict})
    assert custom_out is cyclic
    assert custom_out["name"] == "<b>req</b>"


def test_sanitized_base_model_pre_validation_before_min_length() -> None:
    @SafeUrl("website")
    @SkipSanitization("raw_secret")
    class ProfileInput(SanitizedBaseModel):
        name: str = Field(min_length=3)
        password: str = Field(min_length=3)
        raw_secret: str = "ok"
        website: str = "https://8.8.8.8/hook"

    # HTML is stripped BEFORE min_length=3 runs, so '<b>ab</b>' -> 'ab' fails!
    with pytest.raises(ValidationError):
        ProfileInput(name="<b>ab</b>", password="secretpassword")

    valid = ProfileInput(
        name="<b>Alice</b>",
        password="<script>raw_pw</script>",
        raw_secret="<b>keep</b>",
        website="https://1.1.1.1/ok",
    )
    assert valid.name == "Alice"
    assert valid.password == "<script>raw_pw</script>"
    assert valid.raw_secret == "<b>keep</b>"

    with pytest.raises(BadRequestException):
        ProfileInput(
            name="Alice",
            password="secretpassword",
            website="http://127.0.0.1/admin",
        )
