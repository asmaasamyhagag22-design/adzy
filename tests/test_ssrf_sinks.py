"""SSRF SINK WIRING (P-60) — the scraper's OWN fetches must route through is_safe_public_url,
not just the /api/run entry string.

Before this, is_safe_public_url was applied only to the entry URL at the API boundary; the
crawler's page.goto (homepage + every frontier subpage), the sitemap urlopen, and the robots
urlopen were unguarded — so a public host that 302'd to 169.254.169.254, or a frontier link
resolving to 127.0.0.1, was fetched and saved. These tests pin that each sink now rejects a
non-public target BEFORE any browser/network I/O.

Quality bar: like test_ssrf_coverage, IP-literal targets pass through the conftest network seam
unchanged (literals never hit the getaddrinfo stub), so 127.0.0.1 / 169.254.169.254 / RFC1918 are
classified straight from `ipaddress` and correctly rejected — the real guard is exercised, not a mock.
"""
from __future__ import annotations

import pytest

from scraper.errors import ErrorCode


class _SentinelContext:
    """A browser context whose new_page() raises a unique marker. Lets us prove, without a real
    browser, whether the fetcher reached the network stage or was blocked BEFORE it."""

    def new_page(self):
        raise RuntimeError("REACHED_NEW_PAGE")


def test_fetcher_sink_gates_before_any_browser_io():
    from scraper.fetcher import _fetch_page_once
    ctx = _SentinelContext()

    # Internal targets: blocked at the entry guard, BEFORE context.new_page() — the sentinel never
    # fires, and a BLOCKED_URL result comes back with no captured content.
    for url in ("http://127.0.0.1/", "http://169.254.169.254/latest/meta-data/",
                "http://10.0.0.5/", "http://192.168.1.1/", "http://[::1]/"):
        r = _fetch_page_once(ctx, url)
        assert r.error_code == ErrorCode.BLOCKED_URL, url
        assert not (getattr(r, "rendered_text", "") or "").strip()
        assert not (getattr(r, "html", "") or "").strip()

    # A public host must PASS the guard and reach the browser stage (sentinel raises) — proving the
    # guard gates EXACTLY the internal targets, not everything.
    with pytest.raises(RuntimeError, match="REACHED_NEW_PAGE"):
        _fetch_page_once(ctx, "https://example.com/")


def test_sitemap_sink_blocks_internal_targets():
    from scraper.sitemap import _fetch_sitemap_bytes
    assert _fetch_sitemap_bytes("http://127.0.0.1/sitemap.xml") is None
    assert _fetch_sitemap_bytes("http://169.254.169.254/sitemap.xml") is None
    assert _fetch_sitemap_bytes("http://10.1.2.3/sitemap.xml") is None


def test_robots_sink_skips_internal_without_fetching():
    from scraper.robots import load_robots
    rp, record = load_robots("http://127.0.0.1:8080/admin")
    # Skipped (no network), and marked assume-allowed so a subpage check can't contradict it.
    assert record.allowed is True
    assert "non-public" in (record.note or "").lower()
    assert getattr(rp, "_assume_allowed", False) is True
