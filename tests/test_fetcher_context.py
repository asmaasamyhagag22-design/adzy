"""The browser context must tolerate invalid TLS certs.

Origin (2026-07-06, owner live run): a real brand site (marasimltd.com) had an expired/misconfigured
cert, so Chromium refused it (net::ERR_CERT_AUTHORITY_INVALID) and the whole scrape returned 0 pages
in ~20s ("finished in 23 seconds and produced nothing"). We only READ public marketing content, so a
cert-authority error must NOT block the crawl. Hermetic: a fake browser records the context kwargs.
"""
from __future__ import annotations

from scraper.fetcher import make_browser_context


def test_browser_context_ignores_https_errors():
    captured: dict = {}

    class _FakeBrowser:
        def new_context(self, **kwargs):
            captured.update(kwargs)
            return object()

    make_browser_context(_FakeBrowser())
    # a bad/expired cert must not sink the scrape (read-only public content)
    assert captured.get("ignore_https_errors") is True
    # Service workers can otherwise satisfy requests before page.route sees them.
    assert captured.get("service_workers") == "block"
    # fingerprint still applied
    assert captured.get("user_agent") and captured.get("viewport")
