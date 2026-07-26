# -*- coding: utf-8 -*-
"""The analysis is computed once and shown in ONE place — or it is not shown at all.

MEASURED 2026-07-26 on the owner's own brand (Al Fouad Pharmacies), after a full CLI run:

    outputs/alfouad_full_result.json      20 top-level keys, INCLUDING
                                          swot (11 strengths, 6 opportunities, 1 threat,
                                          2 site_readiness), trends (6), competitors (2)
    outputs/webapp/brands/
      alfouadpharmacies_com.json          has_swot: false · swot: null
                                          _full_result: 8 of those 20 keys
                                          (swot, trends and competitors NOT among them)

So the owner's question — "the site is a big pharmacy, it obviously has competitors, how did the
SWOT not come out?" — had the wrong premise. It DID come out. It came out in the other world.

Three defects sit in the same seam, and all three are the same failure class: something produced,
serialized, and then lost one step before the screen.

  1. There is no supported path at all from a CLI `<slug>_result.json` into the webapp brand
     store. `grep -rn "_full_result" --include=*.py` over the whole repo returns ZERO writers —
     the key in the owner's stored brand was put there by a throwaway script, which carried 8
     keys because 8 is what that script happened to list.
  2. `_swot_from_raw` rebuilds a SWOT for TOWS and drops `site_readiness`. Those items were MOVED
     out of the strategic quadrants on the owner's own ruling, and `_swot_to_frontend` already
     carries them precisely so the move is honest rather than a deletion — then the rebuild path
     deletes them anyway.
  3. `market_trends` is set as a DYNAMIC attribute (`api/webapp_analysis.py:159`), so
     `dataclasses.asdict(swot)` never captures it. `_swot_raw` therefore cannot round-trip the
     trends, and every rebuilt SWOT reports an empty Trends tab while claiming to be the same SWOT.
"""
from __future__ import annotations


def _raw_swot() -> dict:
    """The shape a CLI full run actually writes (verified against alfouad_full_result.json)."""
    def item(text, cite):
        return {"text": text, "citation": [cite], "evidence": "peer comparison",
                "claim_strength": "validated", "id": text[:4], "priority": None}
    return {
        "strengths": [item("You have whatsapp contact; 2/2 peers don't", "https://x.test/a")],
        "weaknesses": [],
        "opportunities": [item("Online booking is absent for every peer", "https://x.test/b")],
        "threats": [item("A funded entrant opened this quarter", "https://x.test/c")],
        "notes": ["4 peers, 4 with an inspectable site"],
        "mode": "competitive",
        "site_readiness": [item("No booking widget on the site", "https://x.test/d")],
    }


def _result() -> dict:
    return {
        "subject_url": "https://x.test",
        "subject_category": "health & beauty products",
        "generated_at": 1785000000,
        "swot": _raw_swot(),
        "trends": [{"title": "Beauty market forecast", "url": "https://stat.test/1"},
                   {"title": "Pharmacy retail growth", "url": "https://stat.test/2"}],
        "competitors": [{"candidate": {"name": "Peer One"}, "has_scrapable_site": True},
                        {"candidate": {"name": "Peer Two"}, "has_scrapable_site": False}],
        "tows": {"so": []}, "media_plan": {}, "media_plan_v2": {},
        "brand_maturity": {"tier": "established"}, "marketing_intel": {},
        "ad_presence": {}, "registry_diff": {}, "competitor_intel": {},
    }


# ---------------------------------------------------------------------
# 2 — the rebuild must not delete what the owner ruled must stay visible
# ---------------------------------------------------------------------

def test_a_rebuilt_swot_keeps_the_site_readiness_items():
    """`_swot_from_raw` fed a raw SWOT with 1 site_readiness item returned 0 of them."""
    from api.webapp_analysis import _swot_from_raw
    swot = _swot_from_raw(_raw_swot())
    assert len(getattr(swot, "site_readiness", []) or []) == 1, (
        "the rebuild dropped site_readiness — the quadrant move becomes a deletion")
    assert (swot.site_readiness[0].text or "").startswith("No booking widget")


def test_a_rebuilt_swot_keeps_the_market_trends():
    """The trends live on a DYNAMIC attribute, so asdict() never stored them. The raw dict must
    carry them explicitly, and the rebuild must put them back."""
    from api.webapp_analysis import _swot_from_raw
    raw = dict(_raw_swot(), market_trends=[{"title": "T", "url": "https://t.test"}])
    swot = _swot_from_raw(raw)
    assert len(getattr(swot, "market_trends", []) or []) == 1, (
        "the rebuild lost the market trends — the Trend tab empties on every round-trip")


def test_the_stored_raw_swot_can_round_trip_the_trends():
    """generate_swot stores `_swot_raw` for later rebuilds. If asdict() is used bare, the trends
    it just computed are gone from the record the moment they are written."""
    import api.webapp_analysis as wa
    from competitor.swot import SWOT

    swot = SWOT()
    swot.market_trends = [{"title": "T", "url": "https://t.test"}]
    raw = wa._swot_raw_dict(swot)
    assert raw.get("market_trends"), (
        "_swot_raw_dict must persist the dynamically-attached trends; dataclasses.asdict does not")
    assert wa._swot_from_raw(raw).market_trends == swot.market_trends


# ---------------------------------------------------------------------
# 1 — a CLI result must have a supported way into the one front end
# ---------------------------------------------------------------------

def test_a_cli_full_run_result_can_be_adopted_by_the_webapp():
    from api.webapp_analysis import adopt_full_result
    patch = adopt_full_result({}, _result())
    assert patch.get("has_swot") is True
    swot = patch.get("swot") or {}
    assert len(swot.get("strengths") or []) == 1
    assert len(swot.get("site_readiness") or []) == 1, "site_readiness must survive the adoption"
    assert len(swot.get("trends") or []) == 2, (
        "the result's top-level trends must land on the SWOT the Trend tab reads")
    assert len(patch.get("competitors") or []) == 2


def test_adopting_carries_every_key_not_a_hand_written_allow_list():
    """The throwaway script carried 8 of 20 keys. An allow-list is how 12 keys vanish silently."""
    from api.webapp_analysis import adopt_full_result
    result = _result()
    result["a_key_nobody_has_thought_of_yet"] = {"v": 1}
    stored = (adopt_full_result({}, result).get("_full_result") or {})
    missing = sorted(set(result) - set(stored))
    assert not missing, f"adoption dropped {missing} — an allow-list silently loses new analysis"


def test_adopting_a_result_with_no_swot_does_not_claim_one():
    """Absence is not a pass. A result carrying no SWOT must leave has_swot alone, never set True."""
    from api.webapp_analysis import adopt_full_result
    result = _result()
    result.pop("swot")
    patch = adopt_full_result({}, result)
    assert patch.get("has_swot") is not True
    assert not patch.get("swot")


def test_the_result_is_matched_by_subject_url_not_by_filename(tmp_path, monkeypatch):
    """Al Fouad's run landed in `alfouad_full_result.json` while the brand key is
    `alfouadpharmacies_com` — a filename match finds nothing. The URL inside the file decides."""
    import json
    import os

    from api.routes.brands import _cli_result_for

    out = tmp_path / "outputs"
    out.mkdir()
    mine = dict(_result(), subject_url="https://alfouadpharmacies.com/")
    (out / "some_operator_typed_this_result.json").write_text(
        json.dumps(mine, ensure_ascii=False), encoding="utf-8")
    (out / "alfouadpharmacies_com_result.json").write_text(
        json.dumps({"subject_url": "https://someone-else.test"}, ensure_ascii=False),
        encoding="utf-8")
    (out / "broken_result.json").write_text("{not json", encoding="utf-8")

    monkeypatch.chdir(tmp_path)
    found, name = _cli_result_for("alfouadpharmacies_com")
    assert name == "some_operator_typed_this_result.json", (
        "matched on the filename instead of the subject_url inside the file")
    assert found.get("swot")
    assert os.path.isdir("outputs")

    assert _cli_result_for("a_brand_with_nothing_on_disk") == ({}, ""), (
        "a brand with no analysis must get an empty answer, never a neighbour's result")


def test_adopting_never_overwrites_a_swot_the_owner_already_approved():
    """`swot_approved` is the owner's signature. An import must not silently replace it."""
    from api.webapp_analysis import adopt_full_result
    brand = {"swot": {"strengths": [{"title": "hers"}]}, "has_swot": True, "swot_approved": True}
    patch = adopt_full_result(brand, _result())
    assert "swot" not in patch, "an approved SWOT was overwritten by an import"
