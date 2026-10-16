# -*- coding: utf-8 -*-
"""Our browser must not contradict itself.

MEASURED (2026-07-21) against the project's own Playwright build:

    user-agent claims .......... Chrome/131.0.0.0
    the engine actually is ..... 147.0.7727.15
    Sec-CH-UA brands ........... (empty)
    navigator.languages ........ ['en-US']   while Accept-Language says "en-US,en;q=0.9,ar;q=0.8"

Three of those are self-inflicted. A hardcoded UA string ages: we shipped a 16-version lie that any
detector catches by comparing the header against `navigator.userAgentData` — and a mismatch reads
as deception, which is exactly what gets a crawler blocked by ORDINARY sites. Fixing them is not
evasion; it is removing contradictions we introduced.

DELIBERATELY NOT FIXED: `navigator.webdriver === true`. Patching that flag exists only to hide
automation from a site that specifically asked. Everything here keeps us honestly identifiable as
an automated browser — a site that blocks on that is exercising a choice we respect.
Hermetic (pure string/derivation logic)."""
from __future__ import annotations

from scraper.config import ACCEPT_LANGUAGE
from scraper.browser_identity import browser_headers, locale_for, user_agent_for


def test_the_user_agent_follows_the_real_engine_version():
    ua = user_agent_for("147.0.7727.15")
    assert "147" in ua
    assert "131" not in ua                       # no stale hardcoded version


def test_headless_is_not_advertised_but_the_browser_is_not_faked():
    ua = user_agent_for("147.0.7727.15")
    assert "HeadlessChrome" not in ua            # not a marker we need to volunteer
    assert "Chrome/" in ua and "Safari/537.36" in ua   # still an honest Chrome UA


def test_sec_ch_ua_matches_the_same_version():
    h = browser_headers("147.0.7727.15")
    assert '"147"' in h["Sec-CH-UA"]
    assert "Chromium" in h["Sec-CH-UA"]
    assert h["Sec-CH-UA-Mobile"] == "?0"
    assert h["Sec-CH-UA-Platform"]


def test_accept_language_and_locale_agree():
    # the JS-visible languages must reflect the header we send, not a hardcoded en-US
    assert locale_for("en-US,en;q=0.9,ar;q=0.8") == "en-US"
    assert locale_for("ar-EG,ar;q=0.9,en;q=0.8") == "ar-EG"
    assert locale_for("") == "en-US"


def test_the_identity_headers_carry_no_language_preference():
    """CONTRACT CHANGED 2026-08-11, and the old one was false in two ways.

    It asserted `browser_headers()["Accept-Language"] == ACCEPT_LANGUAGE`, i.e. "the project's
    language preference is carried through to the crawl". MEASURED against a local origin:
    Chromium serves the TOP-LEVEL navigation's Accept-Language from the context LOCALE and applies
    this dict only to subresources — so this header never reached the request that decides which
    edition a negotiating site returns. The test passed while the thing it named did not happen.

    Second, and the reason it is not simply re-pointed: sending a preference at all is the F-06
    root cause. A negotiating server returns the representation we asked for, and measuring that
    is circular. Language preference now lives in `fetcher._continue_with_language_policy`, which
    sends nothing by default; identity headers state who we are, not what we want served.
    """
    h = browser_headers("147.0.7727.15")
    assert not any(k.lower() == "accept-language" for k in h)


def test_an_unknown_version_degrades_to_something_valid():
    ua = user_agent_for("")
    assert "Chrome/" in ua and "Mozilla/5.0" in ua
