"""D-8: the service-area rung must find a country NAME, not the letters of one inside a word.

MEASURED (elabdfoods.com, 2026-08-27). Not one of the brand's ten service areas names a
country — they are all Egyptian cities. But the rung matched a bare substring, and
"El Shorouk City" contains "ouk", so `country_decision` returned **the UK**. Everything
downstream inherited it: `search_locale` -> ("gb", "en"), the poster's brand research
deep-searched the British market, and the rendered poster carried
"El Abd's authentic Ice Cream Cakes ... are now available in the UK" and
"Now delivering across London." for a patisserie whose own site says its branches are
across Cairo and Giza.

The Evidence Ledger passed that copy (`grounded: true, claims: []`) because a geographic
availability claim carries no number, award, or superlative — its policy answers a
different question. So this rung is the only place the fabrication could have been stopped.

These tests exercise the PUBLIC decision, never the source, and each one fails if the
word-boundary match is reverted to `in`.
"""
from poster.locale import country_decision


def _areas(*values):
    return {"service_areas": [{"value": v} for v in values]}


# ── the false positives that the substring match produced ────────────────────────────────
def test_el_shorouk_city_is_not_the_united_kingdom():
    """The measured original. 'Shor-OUK' must not resolve British."""
    decision = country_decision(_areas("El Shorouk City"))
    assert decision.country == ""
    assert decision.source == "unknown"


def test_a_brands_egyptian_service_areas_leave_the_country_unknown_not_british():
    """El Abd's real ten, in order. No area names a country, so the honest answer is empty —
    the docstring's own rule: 'no evidence, and that stays empty rather than becoming a guess'."""
    decision = country_decision(_areas(
        "Cairo", "Giza", "Marsa Matrouh", "Alexandria", "North Coast",
        "6th of October City", "El Shorouk City", "Shubra El Kheima", "Helwan", "Nasr City",
    ))
    assert decision.country == "", f"resolved {decision.country!r} from {decision.raw!r}"
    assert decision.source == "unknown"


def test_place_names_that_merely_contain_country_letters_do_not_resolve():
    """Same trap, other markets — a Gulf souk, a Singapore hill, a Japanese city, a US state."""
    for area in ("Souk Waqif", "Bukit Timah", "Fukuoka", "Luknow", "Indiana"):
        assert country_decision(_areas(area)).country == "", area


# ── the behaviour that must SURVIVE the fix ──────────────────────────────────────────────
def test_a_country_named_inside_a_phrase_still_resolves():
    """The rung reads free text; finding a country inside a phrase is its whole job."""
    assert country_decision(_areas("Cairo, Egypt")).country == "Egypt"
    assert country_decision(_areas("all over Egypt")).country == "Egypt"
    assert country_decision(_areas("Doha, Qatar")).country == "Qatar"
    assert country_decision(_areas("Riyadh, Saudi Arabia")).country == "Saudi Arabia"
    assert country_decision(_areas("Dubai, UAE")).country == "the UAE"
    assert country_decision(_areas("serving the UK")).country == "the UK"


def test_a_real_country_later_in_the_list_wins_over_an_earlier_lookalike():
    """The lookalike must not shadow the genuine evidence that follows it."""
    decision = country_decision(_areas("El Shorouk City", "Cairo, Egypt"))
    assert decision.country == "Egypt"
    assert decision.raw == "Cairo, Egypt"


def test_the_answer_does_not_depend_on_set_iteration_order():
    """Repeated resolution of the same input returns the same country. The rung used to
    iterate a set, whose order is an implementation detail, not a decision."""
    areas = _areas("Cairo, Egypt", "serving the UK")
    answers = {country_decision(areas).country for _ in range(25)}
    assert answers == {"Egypt"}
