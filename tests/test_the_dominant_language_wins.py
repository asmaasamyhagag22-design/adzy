# -*- coding: utf-8 -*-
"""The crawl's rung of the audience ladder reads its own ranking.

MEASURED 2026-08-06 on the stored Volume Cafe record (a café in Doha):

    languages: ['en', 'ar']        ->  audience_language_for said **Arabic**

`scraper/language.detect_languages` builds that list with `counter.most_common()`, so it is RANKED
BY PROPORTION and every entry carries one. The rung took "the first code that is not English",
which throws the ranking away — one Arabic page outvotes an entire English site — and the answer
went on to decide the reel's voiceover, the poster's copy and the content calendar.

`process.md` had already measured this brand independently and recorded the opposite:
**en @ 100%**, and *"hl='ar' would have been wrong — the site is English"*. The system was
overruling its own measurement with a tie-break rule.

This rung is a genuine last resort — it fires only when the site declares no other edition, the
market is unknown, and the brand does not write about itself in Arabic. There, the dominant
measured language is the best evidence available, and it is the one thing the rung was ignoring.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from reel.audience import _dominant_language, audience_language_for

_BRANDS = Path(__file__).resolve().parent.parent / "outputs" / "webapp" / "brands"


# =====================================================================
# the ranking is the evidence
# =====================================================================

@pytest.mark.parametrize("entries,expected", [
    (["en", "ar"], "en"),                                   # flattened: order IS the ranking
    (["ar", "en"], "ar"),
    ([{"code": "ar", "proportion": 0.18}, {"code": "en", "proportion": 0.82}], "en"),
    ([{"code": "en", "proportion": 0.09}, {"code": "ar", "proportion": 0.91}], "ar"),
    ([], ""),
    ([""], ""),
])
def test_the_dominant_language_is_the_one_there_is_most_of(entries, expected):
    assert _dominant_language(entries) == expected


def test_a_minority_language_no_longer_outvotes_the_site():
    """The measured case, as data: an overwhelmingly English site with one Arabic page."""
    profile = {"name": {"value": "Volume Cafe"}, "source_url": {"value": "https://volumeqatar.com/"},
               "languages": [{"code": "en", "proportion": 0.98},
                             {"code": "ar", "proportion": 0.02}]}
    lang, _country, why = audience_language_for(profile)
    assert lang == "English", f"a 2% Arabic share decided the ad's language ({why})"


def test_a_genuinely_arabic_site_still_resolves_arabic():
    """The fix must not buy the English case by losing the Arabic one."""
    profile = {"name": {"value": "Shop"}, "source_url": {"value": "https://shop.example/"},
               "languages": [{"code": "ar", "proportion": 0.87},
                             {"code": "en", "proportion": 0.13}]}
    assert audience_language_for(profile)[0] == "Arabic"


# =====================================================================
# the higher rungs still outrank it
# =====================================================================

def test_a_declared_edition_beats_the_crawl():
    """hreflang is the brand publishing its own answer; a crawl is ours about the brand."""
    profile = {"name": {"value": "AlFouad"}, "declared_languages": ["ar"],
               "source_url": {"value": "https://alfouadpharmacies.com/"},
               "languages": [{"code": "en", "proportion": 1.0}]}
    lang, _c, why = audience_language_for(profile)
    assert lang == "Arabic" and "hreflang" in why


def test_the_market_beats_the_crawl():
    """A pharmacy in Cairo serves Arabic speakers whatever language its storefront ships in."""
    profile = {"name": {"value": "Shop"}, "country": {"value": "Egypt"},
               "source_url": {"value": "https://shop.example/"},
               "languages": [{"code": "en", "proportion": 1.0}]}
    lang, country, why = audience_language_for(profile)
    assert lang == "Arabic" and country == "eg" and "trades in" in why


# =====================================================================
# on the real records
# =====================================================================

def test_every_stored_brand_is_decided_by_evidence_it_actually_has():
    """A regression net over the real store: the answer, and the rung that produced it."""
    expected = {
        "volumeqatar_com": "English",           # dominant crawled language — the fix
        "alfouadpharmacies_com": "Arabic",      # hreflang
        "nanocareegypt_com": "Arabic",          # hreflang
        "nahdionline_com": "Arabic",            # market 'sa'
        "abuauf_com": "Arabic",                 # hreflang
        "pepsi_com": "English",                 # nothing but English
        "marasimltd_com": "Arabic",             # market 'eg'
    }
    seen = 0
    for dk, want in expected.items():
        p = _BRANDS / f"{dk}.json"
        if not p.is_file():
            continue
        profile = (json.loads(p.read_text(encoding="utf-8")) or {}).get("_profile") or {}
        got, _country, why = audience_language_for(profile)
        assert got == want, f"{dk}: expected {want}, got {got} — {why}"
        seen += 1
    assert seen >= 3, "not enough stored brands to regression-test the ladder"


def test_the_calendar_the_reel_and_the_poster_still_agree():
    """The alignment closed earlier the same day must survive this change."""
    from strategy.builder import _brand_is_arabic
    seen = 0
    for p in sorted(_BRANDS.glob("*.json")):
        if ".bak." in p.name:
            continue
        profile = (json.loads(p.read_text(encoding="utf-8")) or {}).get("_profile") or {}
        if not profile:
            continue
        lang, _c, _w = audience_language_for(profile)
        assert _brand_is_arabic(profile) == str(lang).lower().startswith("ar"), (
            f"{p.name}: the calendar and the reel disagree again")
        seen += 1
    assert seen >= 3
