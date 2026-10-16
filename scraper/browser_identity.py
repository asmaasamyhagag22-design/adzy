# -*- coding: utf-8 -*-
"""One consistent browser identity — derived from the engine we are actually running.

MEASURED (2026-07-21) on this project's own Playwright build:

    user-agent claimed ......... Chrome/131.0.0.0
    the engine actually was .... 147.0.7727.15
    Sec-CH-UA brands ........... (empty)
    navigator.languages ........ ['en-US']  while Accept-Language said "en-US,en;q=0.9,ar;q=0.8"

Three self-inflicted contradictions. A hardcoded UA ages into a lie — ours was 16 major versions
behind the engine sending it — and the cheapest bot check in existence is comparing the UA header
against `navigator.userAgentData` or the JS-visible language list. An ordinary site with ordinary
protection blocks that mismatch, and it should: it looks like deception.

So the identity is DERIVED, never hardcoded: real version, matching client hints, and a locale that
agrees with the Accept-Language we actually send.

WHAT THIS DELIBERATELY DOES NOT DO — `navigator.webdriver` stays `true`. Patching it exists only to
hide automation from a site that specifically checks for it. We remove our own inconsistencies; we
do not defeat a control someone deliberately put up. A site that blocks an honest automated browser
is making a choice, and the honest answer there is the Apify decision, taken per brand by the owner
(see `scraper/apify_fallback.py`), not a stealth patch shipped for every site at once.
"""
from __future__ import annotations

from .config import ACCEPT_LANGUAGE

# The platform we present. Kept fixed and truthful about the OS family the crawler runs on.
_PLATFORM = '"Windows"'
_UA_PLATFORM = "Windows NT 10.0; Win64; x64"
_FALLBACK_MAJOR = "140"


def _major(version: str) -> str:
    """"147.0.7727.15" -> "147". Falls back to a plausible recent major, never to a stale one."""
    head = str(version or "").split(".")[0].strip()
    return head if head.isdigit() else _FALLBACK_MAJOR


def user_agent_for(version: str) -> str:
    """An honest Chrome UA for the engine actually in use.

    `HeadlessChrome` is omitted — that token is not information a site needs and its presence is
    treated as a bot marker by nearly everything. The browser, engine and version we state are all
    real, which is the part that matters.
    """
    return (f"Mozilla/5.0 ({_UA_PLATFORM}) AppleWebKit/537.36 "
            f"(KHTML, like Gecko) Chrome/{_major(version)}.0.0.0 Safari/537.36")


def locale_for(accept_language: str) -> str:
    """The context locale implied by the Accept-Language header we send.

    Previously the locale was pinned to "en-US" while the header advertised Arabic too, so
    `navigator.languages` and the header disagreed on every MENA site we crawl.
    """
    first = str(accept_language or "").split(",")[0].split(";")[0].strip()
    return first or "en-US"


def browser_headers(version: str) -> dict:
    """Client hints that agree with the user agent.

    An empty `Sec-CH-UA` from something claiming to be Chrome is a contradiction on its own; real
    Chrome always sends brands.

    `Accept-Language` is deliberately ABSENT. Language preference is not identity, it selects
    which representation a negotiating server returns — so it is decided once, per-request, by
    `fetcher._continue_with_language_policy`, and defaults to sending nothing at all. Setting it
    here would not have worked anyway: MEASURED against a local origin, Chromium serves the
    TOP-LEVEL navigation's `Accept-Language` from the context locale and only applies this dict to
    subresources, so the header here governed neither the edition a site chose nor half the crawl.
    """
    m = _major(version)
    return {
        "Sec-CH-UA": f'"Chromium";v="{m}", "Google Chrome";v="{m}", "Not?A_Brand";v="24"',
        "Sec-CH-UA-Mobile": "?0",
        "Sec-CH-UA-Platform": _PLATFORM,
        "Upgrade-Insecure-Requests": "1",
    }
