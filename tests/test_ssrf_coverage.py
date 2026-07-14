"""SSRF guard (scraper.url_utils.is_safe_public_url) — adversarial coverage + sink wiring.

is_safe_public_url is the single choke every external-input fetch routes through
(api/routes/run.py, dashboard/server.py, competitor/lite_scrape.py, brand/brand_book.py,
poster/imagen_edit_provider.py, reel/video_provider._load_reference_image). Before this file
its IP-classification logic was never tested directly — callers only monkeypatched it True/False.
This pins the actual defense so a refactor can't silently weaken it.

DNS is monkeypatched per hostname case, so the checks are hermetic AND independent of the
conftest two-seam network guard (which otherwise makes every hostname look public). IP-literal
cases need no patch — literals never hit DNS.
"""
from __future__ import annotations

import socket

import pytest

from scraper.url_utils import is_safe_public_url


def _resolves_to(monkeypatch, *ips):
    """Force the validator's DNS to chosen IP(s) so we test its IP-classification directly."""
    infos = [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", (ip, 0)) for ip in ips]
    monkeypatch.setattr("scraper.url_utils.socket.getaddrinfo",
                        lambda host, port, *a, **k: infos)


@pytest.mark.parametrize("url", [
    "http://127.0.0.1/",
    "http://localhost/",
    "http://0.0.0.0/",
    "http://169.254.169.254/latest/meta-data/",   # AWS / GCP cloud metadata (link-local)
    "http://10.0.0.5/",                            # RFC1918
    "http://192.168.1.1/",
    "http://172.16.0.9/",
    "http://[::1]/",                               # IPv6 loopback
])
def test_rejects_ip_literal_internal_targets(url):
    # IP literals never touch DNS — the validator classifies them straight from ipaddress.
    assert is_safe_public_url(url) is False


@pytest.mark.parametrize("url", [
    "ftp://example.com/resource",
    "file:///etc/passwd",
    "gopher://internal/_",
    "data:text/html,<script>x</script>",
    "",                                            # no host at all
])
def test_rejects_non_http_schemes_and_hostless(url):
    # These fail at the scheme/host check, before any DNS — guard-independent.
    assert is_safe_public_url(url) is False


def test_hostname_resolving_to_metadata_ip_is_rejected(monkeypatch):
    # The DNS-rebinding-shaped bypass: a public-LOOKING hostname that resolves to the cloud
    # metadata address. The validator must reject on the RESOLVED ip, never on the name.
    _resolves_to(monkeypatch, "169.254.169.254")
    assert is_safe_public_url("http://metadata.attacker.example/") is False


def test_hostname_resolving_to_public_ip_is_allowed(monkeypatch):
    _resolves_to(monkeypatch, "93.184.216.34")
    assert is_safe_public_url("https://legit.example.com/") is True


def test_mixed_resolution_with_one_private_record_fails_closed(monkeypatch):
    # getaddrinfo returns a public AND a private record — the socket could bind the private one,
    # so the validator must reject when ANY resolved ip is internal.
    _resolves_to(monkeypatch, "93.184.216.34", "10.0.0.1")
    assert is_safe_public_url("http://dual.example.com/") is False


def test_dns_failure_fails_closed(monkeypatch):
    def _boom(host, port, *a, **k):
        raise socket.gaierror("unresolvable")
    monkeypatch.setattr("scraper.url_utils.socket.getaddrinfo", _boom)
    assert is_safe_public_url("http://never-resolves.example/") is False


def test_reel_reference_image_sink_refuses_internal_url():
    # reel/video_provider._load_reference_image is a fetch sink for user-influenceable image URLs;
    # it must route through the guard before any network read (returns None on an internal target).
    import reel.video_provider as vp
    assert vp._load_reference_image("http://169.254.169.254/latest/meta-data/") is None
    assert vp._load_reference_image("http://127.0.0.1:8080/secret.png") is None
