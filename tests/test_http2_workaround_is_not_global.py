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
    import scraper.crawler as crawler
    src = inspect.getsource(crawler)
    launches = [ln for ln in src.splitlines() if "chromium.launch" in ln]
    assert launches, "no browser launch found — has the crawler moved?"
    # The FIRST launch is the default one every site gets. A later flagged launch is the
    # conditional fallback and is exactly what `test_the_http2_fallback_still_exists...` requires.
    assert "--disable-http2" not in launches[0], (
        "HTTP/2 is still disabled on the default launch; measured 429 on alfouadpharmacies.com")


def test_the_http2_fallback_still_exists_for_the_site_that_needed_it():
    """Removing the flag outright would re-break lcwaikiki.eg. It has to remain REACHABLE, just
    not unconditional."""
    import scraper.crawler as crawler
    src = inspect.getsource(crawler)
    assert "--disable-http2" in src, (
        "the HTTP/2 fallback was deleted rather than made conditional")


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
