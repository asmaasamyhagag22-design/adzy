# -*- coding: utf-8 -*-
"""The calendar's content language stays separate from market and written script.

F-06 supersedes the former "market/audience may choose language" contract: an Egyptian market is
not evidence that measured English content should be rewritten in Arabic, and a quoted Arabic word
is not evidence that the complete brand is Arabic. This guards the calendar's auto mode only.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from poster.locale import content_language_of
from strategy.builder import _brand_is_arabic

_BRANDS = Path(__file__).resolve().parent.parent / "outputs" / "webapp" / "brands"


def _profile(dk: str):
    p = _BRANDS / f"{dk}.json"
    if not p.is_file():
        pytest.skip(f"{dk} is not in this machine's brand store")
    return (json.loads(p.read_text(encoding="utf-8")) or {}).get("_profile") or {}


def test_the_calendar_follows_the_profile_content_language():
    """Regression over stored profiles without deriving language from market country."""
    checked = 0
    for dk in ("alfouadpharmacies_com", "nanocareegypt_com", "abuauf_com",
               "nahdionline_com", "pepsi_com"):
        p = _BRANDS / f"{dk}.json"
        if not p.is_file():
            continue
        profile = (json.loads(p.read_text(encoding="utf-8")) or {}).get("_profile") or {}
        expected = content_language_of(profile) == "ar"
        assert _brand_is_arabic(profile) == expected, (
            f"{dk}: the calendar ignored the profile's dominant content language")
        checked += 1
    assert checked >= 2, "no stored brands to check the agreement on"


def test_an_english_crawl_of_an_egyptian_market_brand_stays_english():
    """Language and market are independent dimensions in F-06."""
    profile = _profile("alfouadpharmacies_com")
    assert profile.get("languages") == ["en"], (
        "this test is pinned to the record where the CRAWL was English — if the brand was "
        "re-scraped in Arabic the measurement it guards no longer applies")
    assert _brand_is_arabic(profile) is False


def test_a_brand_with_no_language_evidence_does_not_count_glyphs():
    latin = {"name": {"value": "Nordic Studio"}, "description": {"value": "A design studio"}}
    arabic = {"name": {"value": "استوديو الشمال"},
              "description": {"value": "استوديو تصميم يقدم خدمات الهوية البصرية"}}
    assert _brand_is_arabic(latin) is False
    assert _brand_is_arabic(arabic) is False


def test_one_unambiguous_declared_arabic_edition_is_language_evidence():
    arabic = {"name": {"value": "Synthetic"}, "declared_languages": ["ar"]}
    assert _brand_is_arabic(arabic) is True
