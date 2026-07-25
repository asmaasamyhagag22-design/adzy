# -*- coding: utf-8 -*-
"""P-11: the strategy readiness verdict is computed, stored — and read by nobody.

RECORDED LONG AGO, VERIFIED AGAIN TODAY. `grep ready_for_strategy` over the two paths that ship a
strategy to a client returns ZERO:

    dashboard/run.py           0 hits
    api/webapp_analysis.py     0 hits

`business_profile/readiness.py` computes it, names the blockers, and stores them; the war room
learned to surface the SWOT half of the same verdict (`ready_for_swot` + `swot_blockers`) after
Volume Qatar shipped an analysis on thin evidence with no caveat. The STRATEGY half was never
wired, so the identical failure survives one level down.

MEASURED on the stored nanocare profile, `outputs/nanocareegypt_com_profile.json`:

    readiness.ready_for_strategy : False
    readiness.strategy_blockers  : ["strategy_evidence_below_threshold"]

and `generate_strategy` ships a posture, a TOWS matrix and a ranked action plan with no mention of
it anywhere. A plan the system itself judged unsupported, presented as a plan.

The owner's ruling on the SWOT half applies unchanged here: it still ships — something beats
nothing — but the client sees the caveat and its named cause. Hermetic: a dict in, a dict out.
"""
from __future__ import annotations

from api.webapp_analysis import generate_strategy


def _brand(**over):
    b = {
        "domain_key": "example_com",
        "organization_name": "Example",
        "tagline": "We do things",
        "description": "A business.",
        "category": "services",
        "offerings": [{"name": "Thing"}],
        "tone_of_voice": "friendly",
        "key_messages": ["Fast", "Local"],
    }
    b.update(over)
    return b


def _not_ready():
    return _brand(readiness={"ready_for_strategy": False,
                             "strategy_blockers": ["strategy_evidence_below_threshold"]})


def test_a_strategy_built_on_evidence_the_system_rejected_says_so():
    out = generate_strategy(_not_ready())
    strat = out["marketing_strategy"]
    notes = strat.get("notes") or []
    assert notes, "the plan shipped with no note that the system judged its evidence insufficient"


def test_the_named_blocker_travels_with_the_caveat():
    """"Not ready" without the reason is an apology, not information."""
    out = generate_strategy(_not_ready())
    notes = " ".join(out["marketing_strategy"].get("notes") or [])
    assert "strategy_evidence_below_threshold" in notes, notes


def test_the_plan_still_ships():
    """The owner's ruling: something beats nothing. The caveat is added, not substituted."""
    out = generate_strategy(_not_ready())
    assert out.get("has_strategy") is True
    assert out["marketing_strategy"].get("strategic_posture")


def test_a_ready_profile_gets_no_spurious_caveat():
    out = generate_strategy(_brand(readiness={"ready_for_strategy": True,
                                              "strategy_blockers": []}))
    notes = " ".join(out["marketing_strategy"].get("notes") or [])
    assert "strategy_evidence_below_threshold" not in notes
    assert "غير" not in notes or not notes.strip()


def test_a_profile_with_no_readiness_block_at_all_is_not_accused():
    """Absent is not false. A profile from an older run carries no readiness dict, and inventing a
    failure for it would be the same overclaim in the other direction."""
    out = generate_strategy(_brand())
    assert not (out["marketing_strategy"].get("notes") or [])


def test_the_frontend_renders_the_strategy_notes():
    from pathlib import Path
    html = (Path(__file__).resolve().parents[1] / "frontend" / "index.html").read_text(
        encoding="utf-8")
    assert "strategy.notes" in html, "the strategy caveat has no renderer — P-11 all over again"
