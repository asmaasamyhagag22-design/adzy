# -*- coding: utf-8 -*-
"""A peer's social PLATFORMS survive the lite scrape — not just how many links it has.

MEASURED (2026-07-25): 18 of 22 stored profiles carry `social_presence` (82%), against only 5 of 17
runs with any Places review (29%). Social is by far the widest evidence source this system already
collects — and `competitor/swot.py` mentions it ZERO times. The one thing called "social" in the
SWOT is `social_count`, a count of links on a homepage.

To say the useful sentence — "three of your four competitors are on TikTok and you are not" — the
peer's PLATFORMS are needed, not a number. And they are already in memory: `extract_links_from_html`
fills `LinkRecord.platform` for every social link, and `lite_peer_dims` then keeps only `len(...)`
and throws the names away. This costs one extra request: none.

Reuses the classifier that already exists, including its share-button guard — a "share on Facebook"
button is not the brand's Facebook account, and `scraper/extractors/links._is_share_url` is the
measured fix for that. Hermetic: HTML strings, no network."""
from __future__ import annotations

from competitor.lite_scrape import lite_peer_dims

_HTML = """
<html><body>
  <a href="https://www.facebook.com/rivalbrand">fb</a>
  <a href="https://www.instagram.com/rivalbrand/">ig</a>
  <a href="https://www.tiktok.com/@rivalbrand">tiktok</a>
  <a href="https://rival.example/menu">Order now</a>
</body></html>
"""


def _dims(html: str) -> dict:
    return lite_peer_dims("https://rival.example/", fetch=lambda u: html) or {}


def test_the_platform_names_survive_not_only_the_count():
    dims = _dims(_HTML)
    assert set(dims.get("social_platforms") or []) == {"facebook", "instagram", "tiktok"}


def test_the_count_still_works_exactly_as_before():
    """Existing consumers read `social_count`; adding a key must not disturb them."""
    assert _dims(_HTML)["social_count"] == 3


def test_a_share_button_is_not_the_peers_account():
    """The measured trap: a 'share this page on Facebook' link is not a Facebook presence."""
    html = """<html><body>
      <a href="https://www.facebook.com/sharer.php?u=https://rival.example/">Share</a>
      <a href="https://twitter.com/intent/tweet?url=https://rival.example/">Tweet</a>
      <a href="https://www.instagram.com/rivalbrand/">ig</a>
    </body></html>"""
    plats = set(_dims(html).get("social_platforms") or [])
    assert plats == {"instagram"}, f"a share button was counted as an account: {plats}"


def test_several_accounts_on_one_platform_count_once():
    """Coca-Cola stores 17 social entries across 4 platforms (regional accounts). The comparison
    is about which platforms a brand is ON, so the unit is the platform."""
    html = """<html><body>
      <a href="https://www.instagram.com/brand_eg/">eg</a>
      <a href="https://www.instagram.com/brand_gulf/">gulf</a>
      <a href="https://www.facebook.com/brand">fb</a>
    </body></html>"""
    assert set(_dims(html).get("social_platforms") or []) == {"instagram", "facebook"}


def test_a_peer_with_no_socials_reports_an_empty_set_not_a_missing_key():
    dims = _dims("<html><body><a href='https://rival.example/x'>x</a></body></html>")
    assert dims.get("social_platforms") == []


def test_an_unreadable_peer_still_returns_unknown_for_everything():
    """A JS-only shell must not report 'no social accounts' as a fact — the existing contract."""
    assert lite_peer_dims("https://rival.example/", fetch=lambda u: "<html><body></body></html>") is None


def test_an_unknown_platform_does_not_break_the_extraction():
    """D-8: the platform list is discovered, never enumerated. A social domain the table does not
    know must not crash or poison the set."""
    html = """<html><body>
      <a href="https://www.facebook.com/rivalbrand">fb</a>
      <a href="https://some-new-network.example/rivalbrand">new</a>
    </body></html>"""
    plats = set(_dims(html).get("social_platforms") or [])
    assert "facebook" in plats
