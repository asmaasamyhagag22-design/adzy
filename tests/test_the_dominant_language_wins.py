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
# the shared content-language contract is authoritative
# =====================================================================

def test_measured_content_language_beats_an_available_other_edition():
    """An Arabic edition's existence does not make an English crawl Arabic."""
    profile = {"name": {"value": "AlFouad"}, "declared_languages": ["ar"],
               "source_url": {"value": "https://alfouadpharmacies.com/"},
               "languages": [{"code": "en", "proportion": 1.0}]}
    lang, _c, why = audience_language_for(profile)
    assert lang == "English" and "dominant content language" in why


def test_one_unambiguous_declared_edition_is_a_fallback_without_measurement():
    profile = {"name": {"value": "Synthetic"}, "declared_languages": ["ar"],
               "source_url": {"value": "https://shop.example/"}, "languages": []}
    lang, _c, why = audience_language_for(profile)
    assert lang == "Arabic" and "hreflang" in why


@pytest.mark.parametrize("country,language,expected_language,expected_dialect", [
    ("Qatar", "en", "English", None),
    ("Qatar", "ar", "Arabic", "Gulf/Khaleeji Arabic"),
    ("Qatar", "", "English", None),  # existing safe fallback; country cannot choose Arabic
    ("Egypt", "en", "English", None),
    ("Egypt", "ar", "Arabic", "Egyptian Arabic"),
    ("Saudi Arabia", "en", "English", None),
    ("Saudi Arabia", "ar", "Arabic", "Gulf/Khaleeji Arabic"),
    ("", "ar", "Arabic", "natural conversational Arabic"),
])
def test_market_country_never_overwrites_content_language(
    country, language, expected_language, expected_dialect,
):
    """F-06: language is measured first; country only chooses an already-Arabic dialect."""
    from poster.locale import arabic_dialect_for, content_language_of, country_of

    profile = {
        "name": {"value": "Synthetic"},
        "source_url": {"value": "https://shop.example/"},
        "locations": [{"country": country}],
        "languages": ([{"code": language, "proportion": 1.0}] if language else []),
    }
    content_code = content_language_of(profile)
    assert content_code == language
    assert country_of(profile) == country

    selected_language, _country_code, _why = audience_language_for(profile)
    assert selected_language == expected_language

    applied_dialect = arabic_dialect_for(profile) if content_code == "ar" else None
    assert applied_dialect is None if expected_dialect is None else expected_dialect in applied_dialect


# =====================================================================
# on the real records
# =====================================================================

def test_every_stored_brand_is_decided_by_evidence_it_actually_has():
    """A regression net over the real store: the answer, and the rung that produced it."""
    expected = {
        "volumeqatar_com": "English",           # dominant crawled language — the fix
        "alfouadpharmacies_com": "English",      # measured content language
        "nanocareegypt_com": "Arabic",          # measured content language
        "nahdionline_com": "Arabic",            # measured content language
        "abuauf_com": "Arabic",                 # measured content language
        "pepsi_com": "English",                 # safe unknown-language fallback
        "marasimltd_com": "English",            # measured content language
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


def test_incidental_arabic_glyphs_do_not_override_measured_english():
    profile = {
        "name": {"value": "English Shop متجر"},
        "description": {"value": "English-first copy with an incidental مرحبا token."},
        "source_url": {"value": "https://shop.example/"},
        "locations": [{"country": "Qatar"}],
        "languages": [
            {"code": "en", "proportion": 0.97},
            {"code": "ar", "proportion": 0.03},
        ],
    }
    assert audience_language_for(profile)[0] == "English"


def test_volume_prompt_preview_exposes_the_shared_english_decision(monkeypatch):
    import api.prompt_preview as preview

    monkeypatch.setattr(preview, "_dna_for", lambda _profile: None)
    stored = json.loads((_BRANDS / "volumeqatar_com.json").read_text(encoding="utf-8"))
    resolved = preview.reel_preview(stored["_profile"])["resolved"]
    assert resolved["audience_language"] == "English"
    assert resolved["language_effective"] == "en"


def test_creative_director_receives_english_at_the_model_boundary(monkeypatch):
    import reel.creative_director as director

    stored = json.loads((_BRANDS / "volumeqatar_com.json").read_text(encoding="utf-8"))
    captured = {}

    def fake_system_prompt(*args, **kwargs):
        captured["language"] = args[1]
        return "synthetic prompt"

    def no_model_call(*_args, **_kwargs):
        raise RuntimeError("intentional hermetic stop before any provider call")

    monkeypatch.setattr(director, "_system_prompt", fake_system_prompt)
    assert director.design_creative_reel(
        stored["_profile"], [], caller=no_model_call,
    ) is None
    assert captured["language"] == "en"


def test_the_calendar_uses_the_profiles_dominant_content_language():
    """F-06: market/audience inference may not overwrite the calendar's content language."""
    from poster.locale import content_language_of
    from strategy.builder import _brand_is_arabic
    seen = 0
    for p in sorted(_BRANDS.glob("*.json")):
        if ".bak." in p.name:
            continue
        profile = (json.loads(p.read_text(encoding="utf-8")) or {}).get("_profile") or {}
        if not profile:
            continue
        expected = content_language_of(profile) == "ar"
        assert _brand_is_arabic(profile) == expected, (
            f"{p.name}: the calendar ignored the profile's dominant content language")
        seen += 1
    assert seen >= 3
