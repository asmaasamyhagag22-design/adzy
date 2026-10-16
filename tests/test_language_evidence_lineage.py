"""F-06 lineage: a language the CRAWLER selected must never arrive downstream as brand evidence.

B2 stopped us sending `Accept-Language`; B2.1 made the browser locale follow a representation the
server chose. Both closed acquisition. Neither stopped the last step: a measurement taken while
the browser rendered under a technical FALLBACK locale looked exactly like one the site had
evidenced, so `languages=["en"]` still read as "this brand is English".

The decisive property below is cases 4/5/6: three DIFFERENT fallback render locales over the same
ambiguous site must all produce the same trusted answer — unknown. If the fallback still steered
the outcome, those three would disagree.

Hermetic: plain data through the real decision authority. No socket, no browser, no provider.
"""
from __future__ import annotations

import pytest

from poster.locale import (
    PRODUCT_FALLBACK_LANGUAGE,
    content_language_decision,
    content_language_of,
)


def _profile(measured: str = "", **evidence):
    out: dict = {}
    if measured:
        out["languages"] = [{"code": measured, "proportion": 1.0}]
    if evidence:
        out["language_evidence"] = evidence
    return out


def _evidenced(language: str):
    """A site whose own preference-free representation named its language."""
    return _profile(language,
                    representation_language=language,
                    render_locale=language,
                    render_locale_source="evidence",
                    representation_changed=False)


def _selector_then_js(fallback_locale: str, measured_after_js: str):
    """An ambiguous selector page; the browser rendered under a fallback and JS moved it."""
    return _profile(measured_after_js,
                    representation_language="",
                    is_language_selector=True,
                    render_locale=fallback_locale,
                    render_locale_source="fallback",
                    representation_changed=True)


# ---------------------------------------------------------------------------
# 1–3: trusted representation evidence, three different languages
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("language", ["ar", "en", "de"])
def test_a_site_evidenced_representation_is_trusted(language):
    decision = content_language_decision(_evidenced(language))
    assert decision.language == language
    assert decision.source == "neutral_representation_evidence"
    assert decision.is_evidence is True
    assert content_language_of(_evidenced(language)) == language


# ---------------------------------------------------------------------------
# 4–6: THE PROOF — the fallback locale cannot change the trusted answer
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    ("fallback_locale", "measured_after_js"),
    [("en-US", "en"), ("ar-EG", "ar"), ("de-DE", "de")],
)
def test_a_fallback_render_locale_never_becomes_site_evidence(fallback_locale, measured_after_js):
    profile = _selector_then_js(fallback_locale, measured_after_js)
    decision = content_language_decision(profile)

    assert decision.source == "product_fallback", (
        f"rendering under {fallback_locale} made {measured_after_js!r} look like site evidence")
    assert decision.is_evidence is False
    assert content_language_of(profile) == "", "an invented language leaked through the wrapper"


def test_three_different_fallbacks_agree_because_none_of_them_is_evidence():
    """If the crawler's locale still decided anything, these three would not match."""
    decisions = [content_language_decision(_selector_then_js(loc, lang))
                 for loc, lang in (("en-US", "en"), ("ar-EG", "ar"), ("de-DE", "de"))]
    assert {d.source for d in decisions} == {"product_fallback"}
    assert len({d.language for d in decisions}) == 1
    assert decisions[0].language == PRODUCT_FALLBACK_LANGUAGE


# ---------------------------------------------------------------------------
# 7–9: render-locale provenance combinations
# ---------------------------------------------------------------------------

def test_7_evidence_aligned_locale_is_trusted():
    assert content_language_decision(_evidenced("ar")).is_evidence is True


def test_8_a_fallback_locale_that_changed_nothing_is_still_usable():
    """Not every fallback is poison: if the browser never moved, the measurement stands."""
    profile = _profile("en", representation_language="", render_locale="en-US",
                       render_locale_source="fallback", representation_changed=False)
    decision = content_language_decision(profile)
    assert decision.language == "en"
    assert decision.source == "trusted_measured_content"
    assert decision.is_evidence is True


def test_9_a_fallback_locale_plus_a_language_changing_navigation_is_not():
    profile = _profile("en", representation_language="", render_locale="en-US",
                       render_locale_source="fallback", representation_changed=True)
    assert content_language_decision(profile).source == "product_fallback"


# ---------------------------------------------------------------------------
# 10–11: the owner outranks everything
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("owner", ["en", "ar"])
def test_an_explicit_owner_choice_wins_over_every_inference(owner):
    for profile in (_evidenced("ar"), _evidenced("en"), _selector_then_js("de-DE", "de"), {}):
        decision = content_language_decision(profile, owner_language=owner)
        assert decision.language == owner
        assert decision.source == "explicit_owner"


def test_an_owner_choice_does_not_rewrite_the_underlying_evidence():
    profile = _evidenced("ar")
    content_language_decision(profile, owner_language="en")
    assert content_language_decision(profile).source == "neutral_representation_evidence"
    assert profile["language_evidence"]["representation_language"] == "ar"


# ---------------------------------------------------------------------------
# 12: country still cannot reach the chain
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("country", ["Egypt", "Qatar", "Saudi Arabia"])
def test_market_country_cannot_select_a_language(country):
    profile = {**_evidenced("en"), "locations": [{"country": country}]}
    assert content_language_decision(profile).language == "en"

    ambiguous = {**_selector_then_js("en-US", "en"), "locations": [{"country": country}]}
    assert content_language_decision(ambiguous).source == "product_fallback"


# ---------------------------------------------------------------------------
# §15 legacy matrix — old profiles that predate provenance
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("language", ["en", "ar"])
def test_a_legacy_profile_keeps_deciding_but_is_not_relabelled_as_proven(language):
    """Documented migration choice: old values still decide, under an honest source name.

    Re-labelling every stored brand as `product_fallback` would silently change the language of
    the whole existing corpus, which is a bigger lie than the one being fixed.
    """
    decision = content_language_decision(_profile(language))
    assert decision.language == language
    assert decision.source == "legacy_measured_content"
    assert decision.source != "neutral_representation_evidence"


def test_a_legacy_profile_with_no_language_at_all_is_a_labelled_fallback():
    decision = content_language_decision({})
    assert decision.source == "product_fallback"
    assert decision.is_evidence is False


def test_an_owner_choice_overrides_legacy_data():
    decision = content_language_decision(_profile("en"), owner_language="ar")
    assert (decision.language, decision.source) == ("ar", "explicit_owner")


# ---------------------------------------------------------------------------
# §10 — the fallback must not be able to certify itself
# ---------------------------------------------------------------------------

def test_a_fallback_result_stored_back_as_a_measurement_is_still_not_evidence():
    """Round-trip the product fallback into `languages` the way a careless writer might."""
    ambiguous = _selector_then_js("en-US", "en")
    decision = content_language_decision(ambiguous)
    assert decision.source == "product_fallback"

    laundered = {**ambiguous, "languages": [{"code": decision.language, "proportion": 1.0}]}
    assert content_language_decision(laundered).source == "product_fallback", (
        "writing the fallback back into `languages` promoted it to evidence")


# ---------------------------------------------------------------------------
# §21 — one authority, consumed by every surface
# ---------------------------------------------------------------------------

def test_every_surface_follows_the_same_authority():
    from poster.concept import brand_is_arabic
    from reel.audience import audience_language_for
    from strategy.builder import _brand_is_arabic as calendar_is_arabic

    arabic = _evidenced("ar")
    assert brand_is_arabic(arabic) is True
    assert calendar_is_arabic(arabic) is True
    assert audience_language_for(arabic)[0] == "Arabic"

    ambiguous = _selector_then_js("ar-EG", "ar")
    assert brand_is_arabic(ambiguous) is False, "a surface trusted a crawler-selected language"
    assert calendar_is_arabic(ambiguous) is False
    assert audience_language_for(ambiguous)[0] != "Arabic"
