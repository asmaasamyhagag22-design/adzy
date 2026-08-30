# -*- coding: utf-8 -*-
"""When our own crawl gets zero pages, fall back to Apify — capped, disclosed, never silent.

MEASURED (pepsi.com, 2026-07-21): our crawler uses a real headless Chromium and still gets HTTP
403 on the homepage, on `sitemap.xml`, and on `robots.txt` itself. It is not a weak-scraper
problem — the refusal happens at the CDN edge. Owner's decision: route these through Apify, which
runs the fetch on its own infrastructure.

Rules this pins, all of which follow the project's existing provider discipline
(`instagram_intelligence/providers/apify_provider.py`):
  - the fallback fires ONLY after a 0-page crawl — never as the primary path, never a second bill
    for a site that already worked;
  - a hard page cap, because Apify bills per result;
  - the profile records that the content came from Apify, so "we scraped their site" and "a paid
    service fetched it for us" never read the same downstream;
  - no token, or any failure, degrades to the honest block message — never a crash, never silence.
Hermetic — the Apify call is injected."""
from __future__ import annotations

import pytest

from scraper.apify_fallback import apify_scrape, ApifyResult


def _runner(pages, calls=None):
    def run(url, *, max_pages, token):
        if calls is not None:
            calls.append({"url": url, "max_pages": max_pages})
        return [{"url": f"{url}p{i}", "text": f"page {i} body", "html": "<p>x</p>"}
                for i in range(pages)]
    return run


def test_a_blocked_site_comes_back_through_apify():
    res = apify_scrape("https://www.pepsi.com/", token="t", runner=_runner(3))
    assert isinstance(res, ApifyResult)
    assert len(res.pages) == 3
    assert res.source == "apify"                 # provenance, not "we crawled it"


def test_the_page_cap_is_enforced_because_apify_bills_per_result():
    calls: list = []
    apify_scrape("https://x.example/", token="t", max_pages=5, runner=_runner(99, calls))
    assert calls[0]["max_pages"] == 5
    res = apify_scrape("https://x.example/", token="t", max_pages=5, runner=_runner(99))
    assert len(res.pages) == 5                    # capped on the way out too, not just requested


def test_an_absurd_cap_is_clamped():
    calls: list = []
    apify_scrape("https://x.example/", token="t", max_pages=10_000, runner=_runner(1, calls))
    assert calls[0]["max_pages"] <= 50            # a runaway bill is not a user choice


def test_no_token_returns_nothing_rather_than_raising():
    assert apify_scrape("https://x.example/", token="", runner=_runner(3)) is None


def test_a_failing_runner_degrades_quietly():
    def boom(url, *, max_pages, token):
        raise RuntimeError("apify down")
    assert apify_scrape("https://x.example/", token="t", runner=boom) is None


def test_an_empty_run_is_not_a_success():
    assert apify_scrape("https://x.example/", token="t", runner=_runner(0)) is None


def test_pages_without_text_are_dropped():
    def blanks(url, *, max_pages, token):
        return [{"url": "a", "text": ""}, {"url": "b", "text": "real body"}]
    res = apify_scrape("https://x.example/", token="t", runner=blanks)
    assert len(res.pages) == 1 and res.pages[0]["text"] == "real body"


def test_spend_is_recorded_for_every_run():
    seen: list = []
    apify_scrape("https://x.example/", token="t", runner=_runner(4),
                 record=lambda n, url: seen.append((n, url)))
    assert seen and seen[0][0] == 4               # pages billed, logged like every other spend
