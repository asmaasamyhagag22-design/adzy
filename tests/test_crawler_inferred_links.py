# -*- coding: utf-8 -*-
"""CS-56 (2026-07-18): next_data's INFERRED internal URLs are synthesised from a slug template
(the path was hardcoded Buffalo-Burger-specific) and the module's contract is 'recorded but NOT
fetched'. But the crawler appended them to the page's links and the frontier CRAWLED them anyway —
spending budget on URLs that may not exist. They now carry `inferred=True` and the frontier selectors
skip them (kept in the link inventory, never fetched). Hermetic — pure selector, no network."""
from __future__ import annotations

from scraper.crawler import _select_subpages_to_fetch
from scraper.schemas import LinkCategory, LinkRecord
from scraper.url_utils import normalize_url

_SITE = "https://buffalo.example/"


def _real(path: str) -> LinkRecord:
    return LinkRecord(href=_SITE + path, anchor_text=path, page_url=_SITE,
                      category=LinkCategory.INTERNAL)


def _inferred(path: str) -> LinkRecord:
    return LinkRecord(href=_SITE + path, anchor_text=path, page_url=_SITE,
                      category=LinkCategory.INTERNAL, inferred=True)


def test_inferred_next_data_urls_are_never_added_to_the_frontier():
    links = [_real("menu"), _inferred("branches/all/offer-detail/some-slug"),
             _inferred("branches/all/menu#cat")]
    subs = _select_subpages_to_fetch(links, _SITE, _SITE)
    urls = [normalize_url(s[0]) for s in subs]
    assert any("menu" in u for u in urls)                     # the REAL internal link is fetched
    assert not any("offer-detail" in u for u in urls)         # the synthesised URL is excluded
    assert not any("#cat" in u or "menu%23" in u for u in urls)


def test_a_real_link_is_not_accidentally_skipped_when_no_inferred_present():
    subs = _select_subpages_to_fetch([_real("about"), _real("contact")], _SITE, _SITE)
    assert len(subs) >= 1                                      # regression: real links still selected


def test_frontier_dedups_http_https_and_trailing_slash_twins():
    # CS-94: the BFS frontier extension de-dupes on the SAME key as the initial selection (dedup_key),
    # so an http + trailing-slash twin of an already-queued page is NOT re-fetched.
    from scraper.crawler import _bfs_new_candidates, dedup_key
    frontier = {dedup_key("https://x.example/about")}         # /about already queued
    twin = LinkRecord(href="http://x.example/about/", anchor_text="About", page_url="https://x.example/",
                      category=LinkCategory.INTERNAL)          # http + trailing-slash variant
    out = _bfs_new_candidates([twin], "https://x.example/", frontier, max_new=10)
    assert out == [], "an http/slash twin of a queued page must not re-enter the frontier"
