"""Unit tests for MongoDB direct connection URI normalization."""

from __future__ import annotations

from urllib.parse import parse_qs, urlparse

from tests.e2e.helpers.containers import mongo_direct_connection_uri


def test_adds_default_db_direct_connection_and_admin_auth_source_for_credentials() -> (
    None
):
    uri = mongo_direct_connection_uri("mongodb://test:test@localhost:53233")
    parsed = urlparse(uri)
    params = parse_qs(parsed.query)

    assert parsed.path == "/e2e_test"
    assert params["directConnection"] == ["true"]
    assert params["authSource"] == ["admin"]


def test_preserves_explicit_auth_source() -> None:
    uri = mongo_direct_connection_uri(
        "mongodb://test:test@localhost:53233?authSource=custom"
    )
    parsed = urlparse(uri)
    params = parse_qs(parsed.query)

    assert parsed.path == "/e2e_test"
    assert params["directConnection"] == ["true"]
    assert params["authSource"] == ["custom"]


def test_does_not_add_auth_source_without_credentials() -> None:
    uri = mongo_direct_connection_uri("mongodb://127.0.0.1:27017")
    parsed = urlparse(uri)
    params = parse_qs(parsed.query)

    assert parsed.path == "/e2e_test"
    assert params["directConnection"] == ["true"]
    assert "authSource" not in params


def test_does_not_override_auth_source_when_database_already_present() -> None:
    uri = mongo_direct_connection_uri("mongodb://user:pass@127.0.0.1:27017/appdb")
    parsed = urlparse(uri)
    params = parse_qs(parsed.query)

    assert parsed.path == "/appdb"
    assert params["directConnection"] == ["true"]
    assert "authSource" not in params
