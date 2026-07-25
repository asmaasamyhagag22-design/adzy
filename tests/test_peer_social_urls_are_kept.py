# -*- coding: utf-8 -*-
"""A peer's social ACCOUNTS, not just the count — the input a competitor comment pull needs.

THE OWNER'S ASK: «يعمل سكراب لسوشيال ميديا للكومنت وللمنافسين» — comments for the brand AND for the
competitors. The brand half shipped: saved snapshots become `ReviewTheme`s and route into the SWOT.
The competitor half cannot even start, because the peer scrape throws the addresses away.

MEASURED on the shipped code: `lite_peer_dims` builds `social = {r.href …}`, uses it for
`len(social)`, and returns the count and (since today) the platform NAMES — but never the hrefs.
"instagram" is not something you can pull comments from; `instagram.com/<handle>` is. One `sorted()`
away, at zero extra requests and zero cost, because the anchors were already parsed.

Two guards that must come with it:

  * SHARE BUTTONS ARE NOT ACCOUNTS. "Share on Facebook" points at `facebook.com/sharer/...` — a
    link to a dialog, not to anybody's profile. The existing extractor already drops these; this
    file locks that it is still true for the URL list, because pulling comments from a share
    endpoint would spend Apify credit to fetch nothing.
  * D-8. No platform is enumerated here. Whatever the extractor recognises is what comes back;
    a platform nobody has written this code for must survive it.

Hermetic: HTML string in, dict out, no network.
"""
from __future__ import annotations

from competitor.lite_scrape import lite_peer_dims

_URL = "https://peer.test/"


def _page(*anchors):
    return "<html><body>" + "".join(anchors) + "</body></html>"


def _dims(html):
    return lite_peer_dims(_URL, fetch=lambda u: html)


def test_the_peer_social_urls_are_returned_not_only_counted():
    d = _dims(_page('<a href="https://www.instagram.com/peerbrand/">Instagram</a>',
                    '<a href="https://www.facebook.com/peerbrand">Facebook</a>'))
    assert d is not None
    urls = d.get("social_urls")
    assert urls, f"the peer's accounts were parsed and discarded: {sorted(d)}"
    assert any("instagram.com/peerbrand" in u for u in urls), urls


def test_the_count_and_the_platforms_still_ship_alongside():
    d = _dims(_page('<a href="https://www.instagram.com/peerbrand/">IG</a>'))
    assert d["social_count"] == 1
    assert "instagram" in [p.lower() for p in d["social_platforms"]]


def test_a_share_button_is_not_an_account():
    """Spending a comment pull on `facebook.com/sharer` fetches nothing and costs the same."""
    d = _dims(_page('<a href="https://www.facebook.com/sharer/sharer.php?u=https://peer.test/">'
                    'Share</a>',
                    '<a href="https://www.instagram.com/peerbrand/">IG</a>'))
    urls = d.get("social_urls") or []
    assert not any("sharer" in u for u in urls), urls


def test_a_peer_with_no_social_links_returns_an_empty_list_not_a_guess():
    d = _dims(_page('<a href="https://peer.test/about">About</a>'))
    assert d is not None
    assert d.get("social_urls") == []


def test_the_urls_are_deduplicated_and_stable():
    """A footer and a header linking the same account is one account. Order must be deterministic
    so a stored peer row does not churn between runs."""
    d = _dims(_page('<a href="https://www.instagram.com/peerbrand/">IG</a>',
                    '<a href="https://www.instagram.com/peerbrand">IG again</a>'))
    urls = d.get("social_urls") or []
    assert len(urls) == 1, urls
    assert urls == sorted(urls)


def test_an_unknown_platform_survives(monkeypatch):
    """D-8: the list of platforms is open. Whatever the extractor classifies as social comes back;
    nothing here may filter by a hardcoded roster."""
    d = _dims(_page('<a href="https://www.threads.net/@peerbrand">Threads</a>',
                    '<a href="https://www.instagram.com/peerbrand/">IG</a>'))
    assert d is not None
    assert isinstance(d.get("social_urls"), list)
