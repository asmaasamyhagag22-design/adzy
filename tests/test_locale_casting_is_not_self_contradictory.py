# -*- coding: utf-8 -*-
"""The casting instruction must not forbid the look it just asked for.

MEASURED (2026-07-25), `poster.locale.brand_locale` for a Qatari brand:

    "PEOPLE & SETTING — every person and environment is authentically from Qatar: real local people
     in their own everyday style and dress (NOT a generic 'Arab' or Gulf/Khaleeji look, NOT
     Western/European casting)"

Qatar IS the Gulf. The sentence tells the model to be authentically Qatari and, in the same breath,
not to look Khaleeji. Identical output for Saudi Arabia, the UAE, Kuwait, Bahrain and Oman.

HOW IT GOT HERE is the project's own failure class. The negation is CORRECT — for the incident it
was written from: an Egyptian brand kept being rendered with a generic Gulf look, so the line
banned it. Generalising then swapped the COUNTRY NAME and left the NEGATION fixed. A true statement
about Egypt became the answer to a different question: "what should a Qatari brand's people look
like?"

This bites every poster for every Gulf brand today — and it bites the influencer feature first,
because that feature generates a PERSON, and its own test brand is volumeqatar.com.

THE FIX: derive the exclusions instead of fixing them. `poster.locale.arabic_region_of` already
classifies a country as Gulf / Levant / Maghreb, so the line can rule out the regions the brand is
NOT in and never its own. Hermetic."""
from __future__ import annotations

import pytest

from poster.locale import arabic_region_of, brand_locale

_GULF = ("Qatar", "Saudi Arabia", "the UAE", "Kuwait", "Bahrain", "Oman")


def _profile(url: str) -> dict:
    return {"name": {"value": "X"}, "source_url": {"value": url}}


@pytest.mark.parametrize("url,country", [
    ("https://x.qa/", "Qatar"),
    ("https://x.sa/", "Saudi Arabia"),
    ("https://x.ae/", "the UAE"),
    ("https://x.kw/", "Kuwait"),
])
def test_a_gulf_brand_is_never_told_to_avoid_looking_gulf(url, country):
    _, line = brand_locale(_profile(url))
    assert country in line, f"the measured country vanished: {line[:120]}"
    low = line.lower()
    assert "not a generic 'arab' or gulf" not in low and "not gulf" not in low, (
        f"a {country} brand is told to avoid the Gulf look it should have:\n{line}")


def test_an_egyptian_brand_still_gets_the_protection_the_line_was_written_for():
    """The original incident must stay fixed: Egypt kept being rendered with a generic Gulf look."""
    _, line = brand_locale(_profile("https://x.com.eg/"))
    assert "Egypt" in line
    assert "gulf" in line.lower() or "khaleeji" in line.lower(), (
        "the Egyptian brand lost the exclusion this line exists for")


def test_western_casting_is_still_excluded_everywhere():
    for url in ("https://x.qa/", "https://x.com.eg/", "https://x.ma/"):
        _, line = brand_locale(_profile(url))
        assert "western" in line.lower(), line[:120]


def test_the_positive_anchor_survives_in_every_market():
    for url in ("https://x.qa/", "https://x.com.eg/", "https://x.tr/"):
        _, line = brand_locale(_profile(url))
        if line:
            assert "authentically from" in line


def test_a_brand_with_no_measured_country_gets_no_casting_claim():
    country, line = brand_locale({"name": {"value": "X"}})
    assert not country and not line, "a nationality was asserted with no evidence"


def test_the_exclusion_is_derived_from_the_region_not_a_fixed_string():
    """A Maghreb brand and a Gulf brand must not receive the same exclusions."""
    _, gulf = brand_locale(_profile("https://x.qa/"))
    _, maghreb = brand_locale(_profile("https://x.ma/"))
    assert arabic_region_of(_profile("https://x.qa/")) != arabic_region_of(_profile("https://x.ma/"))
    assert gulf != maghreb
