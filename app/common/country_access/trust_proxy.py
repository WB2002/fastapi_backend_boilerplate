from __future__ import annotations

import ipaddress

from fastapi import Request

_TRUST_PROXY_STATE_KEY = "trust_proxy"


def _host_in_trusted_list(host: str, trusted: list[str]) -> bool:
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    for entry in trusted:
        try:
            if "/" in entry:
                if address in ipaddress.ip_network(entry, strict=False):
                    return True
            elif address == ipaddress.ip_address(entry):
                return True
        except ValueError:
            continue
    return False


def _peer_host(request: Request) -> str | None:
    client = request.client
    if client is None or not client.host:
        return None
    return client.host


def _xff_chain(request: Request, peer: str | None) -> list[str]:
    forwarded = request.headers.get("x-forwarded-for")
    if not forwarded:
        return [peer] if peer else []
    chain = [part.strip() for part in forwarded.split(",") if part.strip()]
    if peer:
        chain.append(peer)
    return chain


def _client_from_trusted_chain(chain: list[str], trusted: list[str]) -> str | None:
    if not chain:
        return None
    working = list(chain)
    while len(working) > 1 and _host_in_trusted_list(working[-1], trusted):
        working.pop()
    return working[-1]


def resolve_client_ip(request: Request, trust_proxy: bool | list[str]) -> str | None:
    """Resolve the client IP honoring TRUST_PROXY trust boundaries."""
    peer = _peer_host(request)

    if trust_proxy is False:
        return peer

    if trust_proxy is True:
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            first = forwarded.split(",")[0].strip()
            if first:
                return first
        return peer

    if not isinstance(trust_proxy, list) or not trust_proxy:
        return peer

    if peer is None or not _host_in_trusted_list(peer, trust_proxy):
        return peer

    chain = _xff_chain(request, peer)
    return _client_from_trusted_chain(chain, trust_proxy)


def resolve_client_ip_from_request(request: Request) -> str | None:
    """Read trust_proxy from app.state and resolve the client IP."""
    trust_proxy: bool | list[str] = False
    if request.app is not None:
        raw = getattr(request.app.state, _TRUST_PROXY_STATE_KEY, False)
        if raw is False or raw is True or isinstance(raw, list):
            trust_proxy = raw
    return resolve_client_ip(request, trust_proxy)


__all__ = [
    "resolve_client_ip",
    "resolve_client_ip_from_request",
]
