# -*- coding: utf-8 -*-
"""CS-49 (2026-07-18): deep_search collected social links from the BROAD brand search, so a
same-named other company's (or a private person's) social profile was attributed to the brand —
contradicting the module's "NEVER fabricates" promise. A social link is now kept only when it is
plausibly the brand's: first-party (from the site's own `site:` listing) OR its handle contains the
brand token. Hermetic — a fake provider, no network."""
from __future__ import annotations

from scraper.deep_search import gather_deep_search_evidence


class _Hit:
    def __init__(self, title, link, snippet="", rank=0):
        self.title, self.link, self.snippet, self.rank = title, link, snippet, rank


class _Provider:
    name = "fake"

    def __init__(self, own, brand):
        self._own, self._brand = own, brand

    def search(self, query, num=10):
        return self._own if query.startswith("site:") else self._brand


def test_stranger_social_is_not_attributed_to_the_brand():
    # site:rawafrican.net returns the brand's own IG; the broad "rawafrican" search returns a
    # DIFFERENT person's instagram that must NOT be attributed to the brand.
    own = [_Hit("Raw African", "https://rawafrican.net/", "home"),
           _Hit("Raw African IG", "https://instagram.com/rawafrican", "official")]
    brand = [_Hit("Some Person", "https://instagram.com/john_the_traveler", "a stranger"),
             _Hit("Raw African FB", "https://facebook.com/rawafrican", "brand page")]
    res = gather_deep_search_evidence("https://rawafrican.net/", provider=_Provider(own, brand))
    socials = " ".join(res.social_links)
    assert "instagram.com/rawafrican" in socials          # first-party, kept
    assert "facebook.com/rawafrican" in socials            # handle contains the brand, kept
    assert "john_the_traveler" not in socials              # a stranger — DROPPED, not fabricated


def test_social_platform_matches_host_not_substring():
    # CS-93: "netflix.com" ends with "x.com" — a substring match mislabeled it as X/Twitter.
    from scraper.deep_search import _social_platform
    assert _social_platform("https://x.com/brand") == "twitter"    # exact host (x.com -> twitter)
    assert _social_platform("https://m.facebook.com/brand") == "facebook"   # subdomain
    assert _social_platform("https://netflix.com/title") is None   # ends with "x.com" — was mislabeled
    assert _social_platform("https://dropbox.com/s/x") is None     # not "box.com"
