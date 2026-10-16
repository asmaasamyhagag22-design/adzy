# -*- coding: utf-8 -*-
"""C6: the same country, written the way people actually write it, is the same country.

`_known_country` accepted only an exact ISO code or an exact casefold match against the canonical
display name (minus its article). So "UAE" and "AE" resolved, while "United Arab Emirates",
"U.A.E." and "United Arab Emirates (UAE)" — all of them ordinary values in a scraped
`locations[].country` — resolved to nothing at all, and the brand silently lost its country.

The fix is ONE normalization boundary for country FIELDS. Not a global text rule: nothing here may
turn the ordinary English word "us" inside a sentence into the United States. It normalizes the
surface of a value that is already claimed to be a country, and nothing else.

CANONICAL FORM IS UNCHANGED. Normalization means equivalent surfaces reach the same canonical
country; it does not restyle the project's display names. "the UAE" stays "the UAE".

CRITICAL INVARIANT, re-asserted here: COUNTRY != CONTENT LANGUAGE != DIALECT. Country never
selects the content language. Only after Arabic has been selected independently may the country
choose an Arabic register.
"""
from __future__ import annotations

import pytest

from poster.locale import (
    arabic_dialect_for,
    content_language_decision,
    content_language_of,
    country_code,
    country_of,
)


def _profile(**kw):
    return {"source_url": "https://example.com", **kw}


def _with_country(value):
    """A structured `locations[].country`, the shape BusinessProfile actually stores."""
    return _profile(locations=[{"country": value}])


# =====================================================================
# surface equivalence
# =====================================================================

@pytest.mark.parametrize("surface", [
    "UAE", "uae", "the UAE", "AE", "ae",
    "United Arab Emirates", "united arab emirates", "UNITED ARAB EMIRATES",
    "U.A.E.", "U.A.E", "  United Arab Emirates  ",
    "United Arab Emirates (UAE)", "UAE (United Arab Emirates)",
])
def test_every_uae_surface_resolves_to_the_one_canonical_country(surface):
    assert country_of(_with_country(surface)) == "the UAE", f"{surface!r} lost its country"


@pytest.mark.parametrize("surface", [
    "UK", "the UK", "GB", "gb", "United Kingdom", "U.K.", "United Kingdom (UK)",
])
def test_every_uk_surface_resolves_to_the_one_canonical_country(surface):
    assert country_of(_with_country(surface)) == "the UK", f"{surface!r} lost its country"


@pytest.mark.parametrize("surface", [
    "USA", "the USA", "US", "us", "United States", "United States of America",
    "U.S.A.", "U.S.", "United States (USA)",
])
def test_every_usa_surface_resolves_to_the_one_canonical_country(surface):
    assert country_of(_with_country(surface)) == "the USA", f"{surface!r} lost its country"


def test_the_canonical_display_form_is_not_restyled():
    """Normalization must not become a rename of every country in the project."""
    assert country_of(_with_country("Egypt")) == "Egypt"
    assert country_of(_with_country("Saudi Arabia")) == "Saudi Arabia"
    assert country_of(_with_country("United Arab Emirates")) == "the UAE", (
        "the canonical form keeps its article")
    assert country_code("the UAE") == "ae", "the code map still speaks the canonical name"


# =====================================================================
# the boundary is country-specific, not a global text rule
# =====================================================================

def test_normalization_does_not_leak_into_ordinary_text():
    """`us` is a country CODE in a country field and an ordinary pronoun everywhere else."""
    assert country_of(_with_country("us")) == "the USA"
    # A description that merely contains the word must not acquire a country.
    assert country_of(_profile(description="Come and visit us today")) == ""
    assert country_of(_profile(tagline="us")) == "", (
        "a non-country field was read as a country")


@pytest.mark.parametrize("junk", ["", "   ", "Atlantis", "N/A", "-", "unknown", "()", "..."])
def test_unrecognised_surfaces_stay_unknown_rather_than_guessing(junk):
    assert country_of(_with_country(junk)) == ""


# =====================================================================
# neighbours must not be dragged along
# =====================================================================

@pytest.mark.parametrize("name,expected", [
    ("Qatar", "Qatar"), ("Saudi Arabia", "Saudi Arabia"), ("Kuwait", "Kuwait"),
    ("Bahrain", "Bahrain"), ("Oman", "Oman"), ("Egypt", "Egypt"), ("Jordan", "Jordan"),
])
def test_neighbouring_countries_are_untouched(name, expected):
    assert country_of(_with_country(name)) == expected


@pytest.mark.parametrize("name,region_word", [
    ("Qatar", "خليجية"), ("Saudi Arabia", "خليجية"), ("Kuwait", "خليجية"),
    ("the UAE", "خليجية"), ("Egypt", "مصرية"),
])
def test_neighbour_dialect_mapping_is_stable(name, region_word):
    prof = _with_country(name)
    prof["languages"] = [{"code": "ar"}]
    assert region_word in arabic_dialect_for(prof)


# =====================================================================
# COUNTRY != LANGUAGE (the invariant C6 must never breach)
# =====================================================================

def test_a_uae_brand_writing_english_stays_english():
    prof = _with_country("United Arab Emirates")
    prof["languages"] = [{"code": "en"}]
    assert country_of(prof) == "the UAE"
    assert content_language_of(prof) == "en", "the country forced the content language"


def test_a_uae_brand_writing_arabic_may_then_receive_the_gulf_register():
    prof = _with_country("United Arab Emirates")
    prof["languages"] = [{"code": "ar"}]
    assert content_language_of(prof) == "ar"
    assert "خليجية" in arabic_dialect_for(prof), "Arabic was selected but the register was not"


def test_country_alone_never_selects_a_language():
    """No language evidence at all: the country must not fill the gap."""
    prof = _with_country("United Arab Emirates")
    assert country_of(prof) == "the UAE"
    assert content_language_of(prof) == "", "a country was allowed to establish a language"
    decision = content_language_decision(prof)
    assert decision.source == "product_fallback"
    assert decision.is_evidence is False


def test_normalizing_a_country_does_not_change_the_language_decision():
    """The same brand, the country written two ways — the language decision is identical."""
    short = _with_country("UAE")
    long_form = _with_country("United Arab Emirates")
    for p in (short, long_form):
        p["languages"] = [{"code": "en"}]
    assert country_of(short) == country_of(long_form) == "the UAE"
    assert content_language_decision(short) == content_language_decision(long_form)
