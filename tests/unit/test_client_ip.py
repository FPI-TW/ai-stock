"""Unit tests for `_client_ip` real-client-IP resolution behind a reverse proxy.

The per-IP throttles (login:ip, pwreset:ip) hinge on this returning the actual
client, not the proxy — otherwise every request shares one bucket once Nginx is in
front. We build Starlette Requests directly so no DB / TestClient is involved.
"""

import pytest
from starlette.requests import Request

from app.api.routes.auth import _client_ip
from app.core.config import Settings


def _settings(monkeypatch: pytest.MonkeyPatch, trusted: str | None = None) -> Settings:
    monkeypatch.setenv("LOCAL_USER_ID", "00000000-0000-0000-0000-000000000001")
    monkeypatch.setenv("LOCAL_MODE", "true")
    monkeypatch.setenv("QUOTE_PROVIDER", "in_memory")
    if trusted is None:
        monkeypatch.delenv("TRUSTED_PROXY_IPS", raising=False)
    else:
        monkeypatch.setenv("TRUSTED_PROXY_IPS", trusted)
    return Settings(_env_file=None)  # type: ignore[call-arg]


def _request(peer: str | None, *, forwarded: str | None = None) -> Request:
    headers: list[tuple[bytes, bytes]] = []
    if forwarded is not None:
        headers.append((b"x-forwarded-for", forwarded.encode()))
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/auth/login",
        "headers": headers,
        "client": None if peer is None else (peer, 12345),
    }
    return Request(scope)


def test_direct_connection_returns_peer_when_no_trusted_proxy(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _settings(monkeypatch)
    request = _request("203.0.113.7", forwarded="1.2.3.4")

    # No trusted proxy configured: ignore the (spoofable) forwarded header.
    assert _client_ip(request, settings) == "203.0.113.7"


def test_non_ip_peer_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    # TestClient peer is 'testclient' -> gate skipped.
    assert _client_ip(_request("testclient"), _settings(monkeypatch)) is None


def test_missing_client_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    assert _client_ip(_request(None), _settings(monkeypatch)) is None


def test_trusted_proxy_resolves_real_client_from_forwarded(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _settings(monkeypatch, trusted="172.18.0.0/16")
    request = _request("172.18.0.5", forwarded="198.51.100.23")

    assert _client_ip(request, settings) == "198.51.100.23"


def test_untrusted_peer_ignores_forwarded_header(monkeypatch: pytest.MonkeyPatch) -> None:
    # Peer is not in the trusted range: never trust XFF (anti-spoof) -> use peer.
    settings = _settings(monkeypatch, trusted="172.18.0.0/16")
    request = _request("203.0.113.9", forwarded="198.51.100.23")

    assert _client_ip(request, settings) == "203.0.113.9"


def test_spoofed_left_entries_are_ignored_rightmost_untrusted_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    # Attacker prepends a fake IP; Nginx appends the real peer on the right.
    settings = _settings(monkeypatch, trusted="172.18.0.0/16")
    request = _request("172.18.0.5", forwarded="6.6.6.6, 198.51.100.23")

    assert _client_ip(request, settings) == "198.51.100.23"


def test_multiple_trusted_hops_skipped_from_the_right(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _settings(monkeypatch, trusted="172.18.0.0/16,10.0.0.0/8")
    request = _request("172.18.0.5", forwarded="198.51.100.23, 10.0.0.4, 172.18.0.5")

    assert _client_ip(request, settings) == "198.51.100.23"


def test_trusted_peer_without_forwarded_falls_back_to_peer(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _settings(monkeypatch, trusted="172.18.0.0/16")
    request = _request("172.18.0.5")

    assert _client_ip(request, settings) == "172.18.0.5"
