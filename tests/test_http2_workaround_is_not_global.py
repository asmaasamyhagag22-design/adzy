# -*- coding: utf-8 -*-
"""A one-site workaround was handicapping every site, and a 429 was being called permanent.

THE OWNER'S REPORT: «انا كنت بسكراب الفوائد عادي جدا ... شوف الاسكرابر جراله ايه». She was right
on both counts — the site is ordinary, and the change was ours.

MEASURED A/B (alfouadpharmacies.com, 2026-07-25, same URL, same code, seconds apart, the ONLY
difference being the Chromium launch flag):

    args = []                    ->  HTTP 200 · 1,328,616 bytes of HTML · 5,570 chars rendered
    args = ['--disable-http2']   ->  HTTP 429 ·       169 bytes of HTML ·     18 chars rendered

TWO DEFECTS, STACKED.

  1. `--disable-http2` IS APPLIED TO EVERY SITE. It was added for one measured case (lcwaikiki.eg
     resetting HTTP/2 from headless Chromium) and hardcoded into `crawler.py`'s launch. Forcing
     HTTP/1.1 changes our connection fingerprint, and this site's CDN answers it with 429. A
     workaround for one site became a permanent handicap on all of them.

  2. A 429 WAS REPORTED AS `BOT_PROTECTION`. That verdict prints "مش هيتفتح بإعادة المحاولة" —
     it will not open on a retry — which is the opposite of what 429 means. This codebase already
     knows that: `_is_rate_limited` + `HOMEPAGE_RATE_LIMIT_RETRIES` exist precisely to wait a 429
     out ("a rate-limit cooldown clears in tens of seconds"). The bot-protection branch fired
     first and short-circuited the patience that was built for exactly this.

     403 and 503 stay in that branch: a bare 403 with no content IS a refusal, and 503 with nothing
     rendered is a WAF wall. 429 is the one that says "later", and it is the one that had to go.

Hermetic — no browser, no network: the launch args are inspected, and the detector is called
directly.
"""
from __future__ import annotations

import inspect

from scraper.fetcher import _detect_bot_protection
from scraper.schemas import ErrorCode


# --- 1) HTTP/2 is not disabled for everybody ---------------------------------------------------

def test_the_crawler_does_not_hardcode_disable_http2_on_every_launch():
    """2026-07-26: both crawl paths now go through `scraper.browser_launch.launch_chromium`, so
    this asserts the DEFAULT launch keeps HTTP/2 wherever the launching lives."""
    import scraper.crawler as crawler
    src = "\n".join(l.split("#")[0] for l in inspect.getsource(crawler).splitlines())
    launches = [ln for ln in src.splitlines()
                if "chromium.launch" in ln or "_launch_chromium(" in ln]
    assert launches, "no browser launch found — has the crawler moved?"
    # The FIRST launch is the default one every site gets. A later flagged/http2=False launch is
    # the conditional fallback that `test_the_http2_fallback_still_exists...` requires.
    assert "--disable-http2" not in launches[0] and "http2=False" not in launches[0], (
        "HTTP/2 is still disabled on the default launch; measured 429 on alfouadpharmacies.com")


def test_the_http2_fallback_still_exists_for_the_site_that_needed_it():
    """Removing the flag outright would re-break lcwaikiki.eg. It has to remain REACHABLE, just
    not unconditional — in the crawler's retry AND in the shared helper it delegates to."""
    import scraper.browser_launch as bl
    import scraper.crawler as crawler
    crawler_src = "\n".join(l.split("#")[0] for l in inspect.getsource(crawler).splitlines())
    assert "http2=False" in crawler_src or "--disable-http2" in crawler_src, (
        "the HTTP/2 fallback was deleted rather than made conditional")
    assert "--disable-http2" in inspect.getsource(bl), (
        "the shared launcher can no longer produce the HTTP/1.1 fallback at all")


def test_the_fallback_is_keyed_on_the_error_that_motivated_it():
    import scraper.crawler as crawler
    src = inspect.getsource(crawler)
    assert "HTTP2" in src.upper(), "nothing decides WHEN to fall back to HTTP/1.1"


# --- 2) a 429 is a wait, not a wall ------------------------------------------------------------

def test_a_429_with_no_text_is_not_called_bot_protection():
    """429 = "you are going too fast". Reporting it as a permanent block tells the owner to give
    up on a site that works, and skips the rate-limit patience already built for it."""
    assert _detect_bot_protection("", "", http_status=429, html="") is not ErrorCode.BOT_PROTECTION


def test_a_403_with_no_text_is_still_bot_protection():
    assert _detect_bot_protection("", "", http_status=403, html="") is ErrorCode.BOT_PROTECTION


def test_a_503_with_no_text_is_still_bot_protection():
    assert _detect_bot_protection("", "", http_status=503, html="") is ErrorCode.BOT_PROTECTION


def test_a_429_that_really_is_a_challenge_is_still_caught():
    """The status is not the only evidence. A 429 carrying an actual challenge marker is still a
    block — this narrows the inference, it does not blind it."""
    from scraper.config import BOT_CHALLENGE_MARKERS
    marker = sorted(BOT_CHALLENGE_MARKERS)[0]
    assert _detect_bot_protection("", "", http_status=429,
                                  html=f"<html>{marker}</html>") is ErrorCode.BOT_PROTECTION


def test_a_429_stays_transient_so_the_patience_budget_applies():
    """`fetch_page(rate_limit_retries=...)` waits a 429 out. That only happens while the result is
    classified rate-limited rather than permanently blocked."""
    from scraper.fetcher import FetchResult, _is_rate_limited      # FetchResult lives in fetcher
    from datetime import datetime, timezone
    r = FetchResult(url="https://x.test/", final_url="https://x.test/", http_status=429,
                    fetched_at=datetime.now(timezone.utc), duration_ms=0)
    r.error_code = ErrorCode.HTTP_ERROR
    assert _is_rate_limited(r) is True


def test_the_product_page_path_does_not_force_http1_either():
    """MEASURED 2026-07-26 on the owner's own product link
    (alfouadpharmacies.com/products/la-roche-...), same URL seconds apart, the flag the only
    difference:

        args = []                   ->  974,865 bytes of HTML
        args = ['--disable-http2']  ->      169 bytes  (blocked)

    The crawler was fixed in place while `scraper/product_page.py` kept the global flag, so
    "paste a product link" was broken for every site the flag handicaps. The rule now lives in
    ONE helper both paths call — a fix in one place cannot leave the other behind.
    Comments stripped: this file's own prose must not satisfy the assertion."""
    import inspect

    from scraper import product_page

    src = "\n".join(l.split("#")[0] for l in inspect.getsource(product_page).splitlines())
    assert "launch_chromium" in src, (
        "product_page still launches Chromium itself instead of the shared helper")
    assert 'args=["--disable-http2"]' not in src, (
        "the product-page path still forces HTTP/1.1 on every site")


def test_the_shared_launcher_defaults_to_http2_and_can_opt_out():
    from scraper.browser_launch import launch_chromium

    calls = []

    class _PW:
        class chromium:
            @staticmethod
            def launch(**kw):
                calls.append(kw.get("args") or [])
                return "browser"

    launch_chromium(_PW())
    assert calls[-1] == [], f"HTTP/2 was disabled by default again: {calls[-1]}"
    launch_chromium(_PW(), http2=False)
    assert "--disable-http2" in calls[-1], calls[-1]


def test_the_product_page_retries_over_http1_on_a_protocol_error(monkeypatch):
    """The lcwaikiki case must still work through the product-link path: an HTTP/2 protocol
    error retries once on HTTP/1.1 instead of returning None."""
    from scraper import product_page

    launched = []

    class _Res:
        def __init__(self, html, err=""):
            self.html = html
            self.final_url = "https://x.test/p"
            self.error_message = err
            self.error_code = ""
            self.ok = bool(html)

    attempts = {"n": 0}

    def fake_fetch(context, url, **kw):
        attempts["n"] += 1
        if attempts["n"] == 1:
            return _Res("", "net::ERR_HTTP2_PROTOCOL_ERROR")
        return _Res("<html><h1>Item</h1></html>")

    monkeypatch.setattr(product_page, "_fetch_with_browser",
                        lambda url, http2: (launched.append(http2), fake_fetch(None, url))[1])
    monkeypatch.setattr(product_page, "parse_product", lambda html, url: "PARSED")
    out = product_page.scrape_product_page("https://x.test/p")
    assert launched == [True, False], f"no HTTP/1.1 retry after the protocol error: {launched}"
    assert out == "PARSED"
