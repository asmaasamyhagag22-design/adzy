# -*- coding: utf-8 -*-
"""The look must read what the brand IS, not only how it talks.

MEASURED on every real stored brand (2026-07-27), immediately after wiring the brand-aware grade:

    nanocareegypt_com       family=warm  temp=+0.080   tone_of_voice="inspirational"
    abuauf_com              family=warm  temp=+0.080   tone_of_voice="friendly"
    alfouadpharmacies_com   family=warm  temp=+0.080   tone_of_voice=None

Every brand in the system resolved to the SAME family — including Nano Care, the deep-blue
pharmaceutical supplement brand the brief names as the cool/clinical case. It would have been
graded golden.

THE CAUSE. `brand.look._dna_blob` reads exactly one field off a profile: `tone_of_voice`. That
field is a VOICE descriptor — "inspirational", "friendly" — and matches none of the family
keywords, which are about the brand's WORLD ("clinical", "pharmac", "medical", "lab", "luxury",
"vibrant"). So the blob never matched, every brand fell to DEFAULT_LOOK, and the whole per-brand
look mechanism was inert on real data while passing its own tests on hand-written fixtures.

This is the session's recurring failure in its purest form: correct in the test, dead in production,
because the test supplied the input the code wanted instead of the input production actually has.

The fix reads the fields that describe the business — website_type / market_category / description —
alongside the tone. `tone_of_voice` stays: a brand that describes ITSELF as clinical still counts.
"""
from __future__ import annotations

import json
import pathlib

_ROOT = pathlib.Path(__file__).resolve().parents[1]


def test_a_supplement_brand_reads_as_clinical_not_golden():
    from reel.grade import grade_for_brand
    prof = {"tone_of_voice": {"value": "inspirational"},
            "website_type": "liposomal nutritional supplements",
            "description": "a new generation of nutritional supplements"}
    g = grade_for_brand(profile=prof)
    assert g["family"] == "clinical", (
        f"a pharmaceutical supplement brand resolved to {g['family']!r} — the brief's own "
        "example of the cool case would ship warm")
    assert g["temperature"] < 0


def test_a_pharmacy_reads_as_clinical():
    from reel.grade import grade_for_brand
    prof = {"tone_of_voice": {"value": "friendly"},
            "website_type": "online pharmacy", "description": "health and beauty products"}
    assert grade_for_brand(profile=prof)["family"] == "clinical"


def test_a_food_brand_still_reads_warm():
    """The widening must not make everything clinical."""
    from reel.grade import grade_for_brand
    prof = {"tone_of_voice": {"value": "friendly"},
            "website_type": "nuts and dried fruit retailer",
            "description": "roasted nuts, coffee and snacks"}
    assert grade_for_brand(profile=prof)["family"] == "warm"


def test_tone_of_voice_still_counts():
    """A brand that calls ITSELF clinical is clinical, whatever it sells."""
    from brand.look import _LOOKS, look_lock
    assert look_lock(profile={"tone_of_voice": "clean clinical precise"}) == _LOOKS["clinical"]


def test_the_real_brands_no_longer_all_grade_the_same():
    """The measurement that started this: not every brand may land on one family."""
    from reel.grade import grade_for_brand
    d = _ROOT / "outputs" / "webapp" / "brands"
    if not d.is_dir():
        import pytest
        pytest.skip("no stored brands on this machine")
    fams = set()
    for p in d.glob("*.json"):
        if ".bak" in p.name:
            continue
        try:
            prof = (json.loads(p.read_text(encoding="utf-8")) or {}).get("_profile") or {}
        except Exception:
            continue
        if prof:
            fams.add(grade_for_brand(profile=prof)["family"])
    if len(fams) <= 1 and fams:
        raise AssertionError(
            f"every stored brand still grades as {fams} — the look resolver is reading a field "
            "real profiles do not populate")


# ---------------------------------------------------------------------
# the substring trap — third occurrence of this class in one session
# ---------------------------------------------------------------------

def test_a_keyword_does_not_match_inside_an_unrelated_word():
    """MEASURED: Abu Auf — a nuts, dried fruit and coffee retailer — resolved CLINICAL because the
    keyword "lab" matched inside the word "available". The same class as «بشرة» matching inside
    «بالبشرة» and "men" inside "women", both found earlier today. Keywords are word PREFIXES
    ("pharmac" must still reach "pharmaceutical"), so the match has to start at a word boundary."""
    from brand.look import _LOOKS, look_lock
    prof = {"description": "fresh nuts and dried fruit, available in every branch",
            "website_type": "nuts and dried fruit retailer"}
    assert look_lock(profile=prof) != _LOOKS["clinical"], (
        "'lab' matched inside 'available' and turned a food brand clinical")


def test_a_prefix_keyword_still_reaches_its_word():
    from brand.look import _LOOKS, look_lock
    assert look_lock(profile={"description": "a pharmaceutical laboratory"}) == _LOOKS["clinical"]
    assert look_lock(profile={"description": "sophisticated heritage house"}) == _LOOKS["luxury"]
