# -*- coding: utf-8 -*-
"""The fallback must actually fire on a blocked run — and stay honest when it cannot.

A helper nobody calls is a comment. `competitor/full_run.py` is where a 0-page crawl surfaces, so
the wiring is pinned here: try Apify, and only if that also comes back empty print the block
message and stop. When Apify DOES deliver, the run continues and the profile carries the
provenance. Hermetic — no network, no Apify."""
from __future__ import annotations

from competitor.full_run import recover_blocked_scrape


class _Manifest:
    def __init__(self, pages=None):
        self.pages = pages or []
        self.failures = []


def test_apify_pages_become_a_usable_manifest(tmp_path):
    # scrape_dir is explicit: without it an earlier version of this test wrote its fixture text
    # ("real body") into the repository's real `scrapes/` folder.
    man = recover_blocked_scrape(
        "https://www.pepsi.com/", _Manifest(), scrape_dir=tmp_path,
        fetch=lambda url: type("R", (), {"pages": [{"url": "u", "text": "real body"}],
                                         "source": "apify"})())
    assert man is not None
    assert len(man.pages) == 1
    assert getattr(man, "content_source", "") == "apify"     # provenance survives


def test_no_recovery_returns_none_so_the_block_message_stands(tmp_path):
    assert recover_blocked_scrape("https://x.example/", _Manifest(), scrape_dir=tmp_path,
                                  fetch=lambda url: None) is None


def test_a_crashing_fallback_never_breaks_the_error_path(tmp_path):
    def boom(url):
        raise RuntimeError("apify exploded")
    assert recover_blocked_scrape("https://x.example/", _Manifest(), scrape_dir=tmp_path,
                                  fetch=boom) is None


def test_a_run_that_already_has_pages_is_never_re_fetched(tmp_path):
    calls: list = []

    def fetch(url):
        calls.append(url)
        return None
    man = _Manifest(pages=[{"url": "a", "text": "b"}])
    assert recover_blocked_scrape("https://x.example/", man, scrape_dir=tmp_path,
                                  fetch=fetch) is None
    assert calls == []                    # a working site is never billed to Apify
