from __future__ import annotations

from unittest.mock import MagicMock

from fastapi import Request

from app.common.country_access.trust_proxy import resolve_client_ip


def _request(
    *,
    peer: str | None,
    xff: str | None = None,
) -> Request:
    scope: dict[str, object] = {
        "type": "http",
        "headers": [],
        "method": "GET",
        "path": "/",
        "query_string": b"",
        "client": (peer, 12345) if peer else None,
    }
    if xff is not None:
        scope["headers"] = [(b"x-forwarded-for", xff.encode())]
    return Request(scope)


def test_trust_proxy_false_ignores_xff() -> None:
    req = _request(peer="198.51.100.10", xff="8.8.8.8")
    assert resolve_client_ip(req, False) == "198.51.100.10"


def test_trust_proxy_false_without_peer() -> None:
    req = _request(peer=None, xff="8.8.8.8")
    assert resolve_client_ip(req, False) is None


def test_trust_proxy_true_uses_leftmost_xff() -> None:
    req = _request(peer="10.0.0.1", xff="203.0.113.5, 10.0.0.1")
    assert resolve_client_ip(req, True) == "203.0.113.5"


def test_trust_proxy_list_untrusted_peer_ignores_xff() -> None:
    req = _request(peer="198.51.100.10", xff="8.8.8.8")
    assert resolve_client_ip(req, ["10.0.0.0/8"]) == "198.51.100.10"


def test_trust_proxy_list_strips_spoofed_leftmost() -> None:
    req = _request(
        peer="10.0.1.50",
        xff="8.8.8.8, 203.0.113.9",
    )
    trusted = ["10.0.0.0/8"]
    assert resolve_client_ip(req, trusted) == "203.0.113.9"


def test_trust_proxy_list_no_xff_returns_peer() -> None:
    req = _request(peer="10.0.1.50")
    assert resolve_client_ip(req, ["10.0.0.0/8"]) == "10.0.1.50"


def test_resolve_client_ip_from_request_reads_app_state() -> None:
    from app.common.country_access.trust_proxy import resolve_client_ip_from_request

    app = MagicMock()
    app.state.trust_proxy = False
    scope: dict[str, object] = {
        "type": "http",
        "headers": [(b"x-forwarded-for", b"8.8.8.8")],
        "method": "GET",
        "path": "/",
        "query_string": b"",
        "client": ("198.51.100.10", 12345),
        "app": app,
    }
    req = Request(scope)
    assert resolve_client_ip_from_request(req) == "198.51.100.10"
