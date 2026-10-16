# -*- coding: utf-8 -*-
"""C6-R1: when the evidence disagrees about a country, what a STATED fact beats.

C6 answered "which country is this surface?". This answers a different question: "several signals
disagree — which one decides, and can a caller find out which one it was?".

The old order put the website's ccTLD FIRST, above the brand's own stated country, and called it
"the most reliable signal". That ranking is older than the structured country fields: when the only
thing the profile carried was a URL, the ccTLD really was the best available guess. It stopped
being true the moment `locations[].country` existed, and nothing re-ranked it. A UK company on a
.ae domain was recorded as Emirati, over its own address.

Stated beats inferred:

    1. explicit_country   the profile's own top-level country field
    2. location_country   a country on the brand's structured address
    3. website_cctld      inferred from the domain
    4. service_area       a place the brand says it SERVES, not where it IS
    5. phone_prefix       inferred from a dial code
    6. unknown            no evidence — and that stays empty, never a guess

The decision also has to SAY which of those it used. A bare string cannot be stored honestly:
"the UAE" from a stated address and "the UAE" guessed from a domain are not the same fact, and a
database that keeps only the string can never tell them apart again. This mirrors the shape the
module already uses for language — `ContentLanguageDecision(language, source)`.
"""
from __future__ import annotations

import pytest

from poster.locale import (
    arabic_dialect_for,
    content_language_decision,
    content_language_of,
    country_decision,
    country_of,
)


def _profile(url="https://example.com", **kw):
    return {"source_url": url, **kw}


# =====================================================================
# the decision carries its own provenance
# =====================================================================

def test_the_decision_reports_country_and_source():
    d = country_decision(_profile(country="Egypt"))
    assert d.country == "Egypt"
    assert d.source == "explicit_country"
    assert d.is_known is True


def test_an_absent_country_is_unknown_not_a_guess():
    d = country_decision(_profile(url="https://example.com"))
    assert d.country == ""
    assert d.source == "unknown"
    assert d.is_known is False


def test_country_of_stays_the_string_compatibility_wrapper():
    prof = _profile(country="Qatar")
    assert country_of(prof) == country_decision(prof).country == "Qatar"


# =====================================================================
# the precedence matrix — every rung beats the one below it
# =====================================================================

def test_explicit_country_beats_every_inferred_signal():
    prof = _profile(
        url="https://brand.ae/",                      # ccTLD says the UAE
        country="the UK",                             # the brand says the UK
        locations=[{"country": "Qatar"}],
        service_areas=[{"value": "Kuwait"}],
        contact_channels={"phones": [{"e164": "+201012581929"}]},
    )
    d = country_decision(prof)
    assert d.country == "the UK", "an inferred signal outranked the brand's own statement"
    assert d.source == "explicit_country"


def test_a_stated_address_country_beats_the_domain():
    """The regression: a UK company on a .ae domain was recorded as Emirati."""
    prof = _profile(url="https://brand.ae/", locations=[{"country": "United Kingdom"}])
    d = country_decision(prof)
    assert d.country == "the UK", "the ccTLD overruled the brand's own address"
    assert d.source == "location_country"


def test_the_domain_decides_when_nothing_is_stated():
    d = country_decision(_profile(url="https://brand.ae/"))
    assert d.country == "the UAE"
    assert d.source == "website_cctld"


def test_a_service_area_is_used_only_below_the_domain():
    """Where a brand SERVES is weaker evidence than where its site says it is."""
    prof = _profile(url="https://brand.qa/", service_areas=[{"value": "Kuwait"}])
    d = country_decision(prof)
    assert d.country == "Qatar", "a served market outranked the brand's own domain"
    assert d.source == "website_cctld"

    only_service = _profile(url="https://brand.com/", service_areas=[{"value": "Kuwait"}])
    d2 = country_decision(only_service)
    assert d2.country == "Kuwait"
    assert d2.source == "service_area"


def test_a_phone_prefix_is_the_last_resort():
    prof = _profile(url="https://brand.com/",
                    contact_channels={"phones": [{"e164": "+201012581929"}]})
    d = country_decision(prof)
    assert d.country == "Egypt"
    assert d.source == "phone_prefix"


def test_a_stated_country_beats_a_phone_from_another_country():
    prof = _profile(url="https://brand.com/",
                    locations=[{"country": "Saudi Arabia"}],
                    contact_channels={"phones": [{"e164": "+201012581929"}]})
    d = country_decision(prof)
    assert d.country == "Saudi Arabia"
    assert d.source == "location_country"


@pytest.mark.parametrize("source,prof", [
    ("explicit_country", _profile(country="Oman")),
    ("location_country", _profile(locations=[{"country": "Oman"}])),
    ("website_cctld", _profile(url="https://brand.om/")),
    ("service_area", _profile(url="https://brand.com/", service_areas=[{"value": "Oman"}])),
    ("phone_prefix", _profile(url="https://brand.com/",
                              contact_channels={"phones": [{"e164": "+96812345678"}]})),
])
def test_every_source_class_is_reachable_and_named(source, prof):
    d = country_decision(prof)
    assert d.country == "Oman"
    assert d.source == source


# =====================================================================
# C6 normalization still applies at every rung
# =====================================================================

@pytest.mark.parametrize("surface", ["United Arab Emirates", "U.A.E.", "UAE (United Arab Emirates)"])
def test_normalization_applies_to_the_explicit_field(surface):
    d = country_decision(_profile(country=surface))
    assert d.country == "the UAE"
    assert d.source == "explicit_country"


def test_an_unrecognised_stated_country_falls_through_rather_than_blocking():
    """Junk in the strongest field must not veto the weaker evidence below it."""
    prof = _profile(url="https://brand.qa/", country="N/A")
    d = country_decision(prof)
    assert d.country == "Qatar"
    assert d.source == "website_cctld"


# =====================================================================
# provenance must not contaminate language (the standing invariant)
# =====================================================================

def test_provenance_does_not_change_the_language_decision():
    stated = _profile(url="https://brand.com/", locations=[{"country": "United Arab Emirates"}],
                      languages=[{"code": "en"}])
    inferred = _profile(url="https://brand.ae/", languages=[{"code": "en"}])
    assert country_decision(stated).source == "location_country"
    assert country_decision(inferred).source == "website_cctld"
    assert country_of(stated) == country_of(inferred) == "the UAE"
    assert content_language_of(stated) == content_language_of(inferred) == "en"
    assert content_language_decision(stated) == content_language_decision(inferred)


def test_a_stated_country_still_never_selects_arabic():
    prof = _profile(country="the UAE", languages=[{"code": "en"}])
    assert country_decision(prof).source == "explicit_country"
    assert content_language_of(prof) == "en"


def test_dialect_follows_the_decided_country_whatever_its_source():
    stated = _profile(url="https://brand.com/", country="Qatar", languages=[{"code": "ar"}])
    inferred = _profile(url="https://brand.qa/", languages=[{"code": "ar"}])
    assert "خليجية" in arabic_dialect_for(stated)
    assert "خليجية" in arabic_dialect_for(inferred)


# =====================================================================
# what a future database has to be able to store
# =====================================================================

def test_the_decision_survives_as_a_storable_pair():
    """A bare string cannot record HOW the country was known; this pair can."""
    d = country_decision(_profile(url="https://brand.ae/",
                                  locations=[{"country": "United Kingdom"}]))
    row = {"country_canonical": d.country, "country_source": d.source}
    assert row == {"country_canonical": "the UK", "country_source": "location_country"}
    assert d.raw.strip() == "United Kingdom", "the surface actually seen must survive too"
