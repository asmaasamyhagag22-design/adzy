# -*- coding: utf-8 -*-
"""An unknown country must never be spelled "جنيه".

D-8 (the country axis): the system is measured across verticals and almost never across countries,
which is how Egypt-shaped defaults survive every gate. Found while running a QATARI brand: the
currency table maps Qatar correctly, but every fallback path read `currency or "جنيه"` — so a brand
whose country could not be resolved got its budget, CPL and kill-switch denominated in EGYPTIAN
POUNDS. That is a confident, wrong, client-facing number: the same failure class as an unsourced
claim, wearing a plausible unit.

Unknown currency renders as no currency at all. A bare number is honest; the wrong currency is not.
Hermetic."""
from __future__ import annotations

from media_plan_v2.reality_check import build_reality_check


def _profile(**over):
    p = {"name": {"value": "Volume"}, "category": {"value": "cafe"},
         "source_url": {"value": "https://volumeqatar.com/"}}
    p.update(over)
    return p


def test_qatari_brand_is_denominated_in_qatari_riyal():
    rc = build_reality_check(_profile(service_areas=[{"value": "Doha, Qatar"}]),
                             capacity=20, category="cafe")
    assert "ريال قطري" in (rc.kill_switch.currency or "")


def test_unknown_country_says_nothing_rather_than_egyptian_pounds():
    rc = build_reality_check(_profile(), capacity=20, category="cafe")
    cur = rc.kill_switch.currency or ""
    assert "جنيه" not in cur, f"invented an Egyptian currency for an unknown country: {cur!r}"
    # the DISCLOSURE is the client-facing sentence — the currency must not sneak back in there
    assert "جنيه" not in (rc.kill_switch.disclosure or "")
    for note in (rc.notes or []):
        assert "جنيه" not in note, note


def test_egypt_still_reads_in_pounds():
    rc = build_reality_check(_profile(source_url={"value": "https://brand.eg/"}),
                             capacity=20, category="clinic")
    assert "جنيه" in (rc.kill_switch.currency or "")


def test_the_deck_does_not_relabel_an_unknown_currency_as_egyptian():
    from dashboard.war_room import _deck_currency
    assert _deck_currency({"kill_switch": {"currency": "ريال قطري"}}) == "ريال قطري"
    assert "جنيه" not in _deck_currency({"kill_switch": {"currency": ""}})
    assert "جنيه" not in _deck_currency({})
