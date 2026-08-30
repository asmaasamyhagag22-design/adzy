"""Evidence-derived brand locale (poster/locale.py) — FIX-C3/D of the 2026-07-11 campaign.

The primer must come from PROFILE EVIDENCE (ccTLD -> service_areas -> phone e164 -> language),
be universal (any country, nothing hardcoded), and stay HONEST: unknown locale injects no
cultural claim. Hermetic.
"""
from __future__ import annotations

from poster.locale import CCTLD_COUNTRY, brand_locale, country_code, country_of


def test_cctld_wins():
    assert country_of({"source_url": "https://nti.sci.eg/"}) == "Egypt"
    assert country_of({"source_url": "https://brand.com.sa/"}) == "Saudi Arabia"
    assert country_of({"source_url": "https://shop.co.uk/"}) == "the UK"


def test_service_areas_fallback_for_dot_com():
    p = {"source_url": "https://brand.com/",
         "service_areas": [{"value": "Cairo, Egypt"}]}
    assert country_of(p) == "Egypt"


def test_phone_e164_fallback():
    p = {"source_url": "https://brand.com/",
         "contact_channels": {"phones": [{"e164": "+201099569334", "raw": "010..."}]}}
    assert country_of(p) == "Egypt"
    p2 = {"contact_channels": {"phones": [{"e164": "+966501234567"}]}}
    assert country_of(p2) == "Saudi Arabia"


def test_primer_wording_and_language_cue():
    country, line = brand_locale({"source_url": "https://x.eg/"})
    assert country == "Egypt"
    assert "authentically from Egypt" in line and "everyday style" in line
    assert "Khaleeji" in line                        # anti-drift kept from the proven wording
    # Arabic content, no country evidence -> honest REGIONAL cue only
    c2, line2 = brand_locale({"source_url": "https://brand.com/", "languages": ["ar"]})
    assert c2 == "" and "Arabic-speaking-region" in line2


def test_unknown_locale_injects_nothing():
    assert brand_locale({"source_url": "https://brand.com/"}) == ("", "")
    assert brand_locale({}) == ("", "")


def test_arabic_saudi_brand_is_not_forced_egyptian():
    # the anti-hardcode regression: an ARABIC brand with .sa evidence must be Saudi, never
    # 'Egyptian by default' (the reel used to map ANY Arabic brand to Egypt)
    country, line = brand_locale({"source_url": "https://brand.sa/", "languages": ["ar"]})
    assert country == "Saudi Arabia" and "Saudi Arabia" in line and "Egypt" not in line


# --- country_code: the ISO-3166 alpha-2 the SERP needs (D-8, 2026-07-15) ------------------

def test_country_code_round_trips_from_the_resolved_country():
    """The whole point: evidence -> country -> code -> `gl`. One signal, two uses."""
    assert country_code(country_of({"source_url": "https://brand.qa/"})) == "qa"
    assert country_code(country_of({"source_url": "https://nti.sci.eg/"})) == "eg"
    assert country_code(country_of({"source_url": "https://brand.com.sa/"})) == "sa"


def test_country_code_handles_the_names_that_carry_an_article():
    # `country_of` speaks display names, and five of them start with "the".
    assert country_code("the UAE") == "ae"
    assert country_code("the Netherlands") == "nl"
    assert country_code("the Philippines") == "ph"


def test_country_code_uk_is_gb_not_uk():
    """The ONE ccTLD in the table that is not its own ISO code: `.uk` -> GB.
    Verified by hand against all 62 keys; if a future entry adds another mismatch, it belongs
    in `_CCTLD_ISO_OVERRIDE` beside this one."""
    assert country_of({"source_url": "https://shop.co.uk/"}) == "the UK"
    assert country_code("the UK") == "gb"


def test_country_code_is_empty_rather_than_wrong():
    """The honest absence. Every one of these must yield NO country claim — `SerperTrendSource`
    turns "" into `gl=None`, so the request carries no market rather than a guessed one."""
    assert country_code("") == ""                    # country_of's own unknown answer
    assert country_code(None) == ""                  # defensive: never raise on a bad caller
    assert country_code("Atlantis") == ""            # a name we have no code for
    # `_E164_COUNTRY`'s "+1" -> "the USA/Canada" is genuinely ambiguous: a +1 phone does not
    # say WHICH country. It must resolve to nothing, never to one of the two.
    assert country_code("the USA/Canada") == ""


def test_every_resolvable_country_has_a_code_and_no_name_is_ambiguous():
    """Structural guard: the map is DERIVED from CCTLD_COUNTRY, so a new ccTLD entry gains its
    code for free. This fails the day someone adds a country the inversion can't resolve —
    which is exactly when a silent `gl=''` would otherwise start shipping."""
    for name in set(CCTLD_COUNTRY.values()):
        code = country_code(name)
        assert code, f"{name!r} resolves to no ISO code"
        assert len(code) == 2 and code.islower(), f"{name!r} -> {code!r} is not an alpha-2 code"
