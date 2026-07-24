# -*- coding: utf-8 -*-
"""Her number must take effect the moment she saves it — not after a re-analysis.

The builder consumes `monthly_budget` when a plan is BUILT, but plans are built during a full
analysis (minutes, and model calls). If typing a budget in the vault only mattered at the next
analysis, the vault would feel dead exactly the way the owner reported.

So the deck re-applies her saved number to the ALREADY-BUILT plan at render time. This is
deterministic arithmetic through the same validated schema — same learning-floor rule, no model
call, no spend. Hermetic."""
from __future__ import annotations

from dashboard.war_room import _apply_saved_budget

_PLAN = {"objectives": [
    {"objective": "OUTCOME_AWARENESS", "destination": "website", "funnel_stage": "tofu",
     "rationale": "r", "budget_allocation_pct": 30.0, "num_ad_sets": 1,
     "kpi_target": {"metric": "cpm", "target_value": None, "unit": ""}},
    {"objective": "OUTCOME_LEADS", "destination": "whatsapp", "funnel_stage": "bofu",
     "rationale": "r", "budget_allocation_pct": 70.0, "num_ad_sets": 1,
     "kpi_target": {"metric": "cost_per_lead", "target_value": None, "unit": ""}},
]}


def test_saved_budget_is_spent_across_the_objectives():
    out = _apply_saved_budget(_PLAN, {"monthly_budget": 30000})
    got = [o["test_budget"] for o in out["objectives"]]
    assert got == [9000.0, 21000.0]                       # 30/70, summing to HER number
    assert all("ميزانيتك" in o["budget_note"] for o in out["objectives"])


def test_no_budget_leaves_the_plan_untouched():
    assert _apply_saved_budget(_PLAN, {}) is _PLAN
    assert _apply_saved_budget(_PLAN, {"monthly_budget": 0}) is _PLAN
    assert _apply_saved_budget(_PLAN, {"monthly_budget": "abc"}) is _PLAN


def test_a_plan_already_carrying_a_budget_is_not_double_applied():
    priced = {"objectives": [dict(_PLAN["objectives"][0], test_budget=9000.0)]}
    assert _apply_saved_budget(priced, {"monthly_budget": 50000}) is priced


def test_a_malformed_plan_never_breaks_the_page():
    for bad in (None, {}, {"objectives": "nope"}, {"objectives": [{"junk": 1}]}):
        _apply_saved_budget(bad, {"monthly_budget": 1000})   # must not raise
