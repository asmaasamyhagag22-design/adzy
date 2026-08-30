# -*- coding: utf-8 -*-
"""The content calendar asks the same question the reel and the poster ask.

MEASURED 2026-08-06 on the stored AlFouad Pharmacies record — an Egyptian pharmacy chain, on disk,
no model involved:

    reel / poster  (`reel.audience.audience_language_for`) -> Arabic / eg
                   reason: "the site declares a Arabic edition (hreflang)"
    content calendar (`strategy.builder._brand_is_arabic`) -> English
                   12 of 12 topics: "Special Offers", "Bundles", "Vitamins"

Same brand, same evidence, two answers — decided by which surface asked. `info/STATE.md` records
this exact defect for the reel («نفس البراند، لغتين، حسب الباب») and closed it there by making the
weaker calculator ask the stronger one; the calendar was the surface still counting Arabic glyphs
in the brand's own stored text. The crawl reached AlFouad's ENGLISH edition, so that count was
answering "what did we happen to fetch", not "who is this ad for".

It reaches further than the calendar: `poster/from_profile` can take a calendar TOPIC as its
headline, so an English topic becomes an English headline on an Arabic brand's poster.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from strategy.builder import _brand_is_arabic

_BRANDS = Path(__file__).resolve().parent.parent / "outputs" / "webapp" / "brands"


def _profile(dk: str):
    p = _BRANDS / f"{dk}.json"
    if not p.is_file():
        pytest.skip(f"{dk} is not in this machine's brand store")
    return (json.loads(p.read_text(encoding="utf-8")) or {}).get("_profile") or {}


def test_the_calendar_and_the_reel_agree_on_the_language():
    """The property, not the value: whatever the audience resolver decides, the calendar follows.
    Asserting "AlFouad is Arabic" would pin a brand; asserting AGREEMENT pins the defect."""
    from reel.audience import audience_language_for
    checked = 0
    for dk in ("alfouadpharmacies_com", "nanocareegypt_com", "abuauf_com",
               "nahdionline_com", "pepsi_com"):
        p = _BRANDS / f"{dk}.json"
        if not p.is_file():
            continue
        profile = (json.loads(p.read_text(encoding="utf-8")) or {}).get("_profile") or {}
        lang, _country, _why = audience_language_for(profile)
        assert _brand_is_arabic(profile) == str(lang).lower().startswith("ar"), (
            f"{dk}: the calendar says arabic={_brand_is_arabic(profile)} while the reel and the "
            f"poster say {lang!r} — one brand, two languages, decided by which door was used")
        checked += 1
    assert checked >= 2, "no stored brands to check the agreement on"


def test_an_english_crawl_of_an_arabic_market_brand_is_still_arabic():
    """The measured case. AlFouad's stored text is Latin because the crawl reached its English
    edition; counting glyphs there answers the wrong question."""
    profile = _profile("alfouadpharmacies_com")
    assert profile.get("languages") == ["en"], (
        "this test is pinned to the record where the CRAWL was English — if the brand was "
        "re-scraped in Arabic the measurement it guards no longer applies")
    assert _brand_is_arabic(profile) is True


def test_a_brand_with_no_audience_evidence_falls_back_to_counting_glyphs():
    """The degrade is the old behaviour, deliberately: with nothing to resolve, the brand's own
    text is a real answer, and it is what every previous run used."""
    latin = {"name": {"value": "Nordic Studio"}, "description": {"value": "A design studio"}}
    arabic = {"name": {"value": "استوديو الشمال"},
              "description": {"value": "استوديو تصميم يقدم خدمات الهوية البصرية"}}
    assert _brand_is_arabic(latin) is False
    assert _brand_is_arabic(arabic) is True


def test_a_broken_resolver_does_not_break_the_calendar(monkeypatch):
    """A resolver that raises must cost the calendar nothing — it falls back, never crashes."""
    import reel.audience as ra

    def _boom(_profile):
        raise RuntimeError("resolver down")
    monkeypatch.setattr(ra, "audience_language_for", _boom)
    arabic = {"name": {"value": "استوديو الشمال"},
              "description": {"value": "استوديو تصميم يقدم خدمات الهوية البصرية"}}
    assert _brand_is_arabic(arabic) is True
