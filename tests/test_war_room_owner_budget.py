# -*- coding: utf-8 -*-
"""Her budget must be VISIBLE where she reads the plan — p1's tag and p5's cells.

Owner: the vault took her monthly budget and the deck still said "الميزانية: مطلوب رقمك". The
builder now spends it (tests/test_owner_budget_is_consumed.py); this asserts the deck reads the
whole plan's budget (all objectives, not just the first) and labels it as HERS. Hermetic."""
from __future__ import annotations

from dashboard.war_room import _page_cells, _plan_test_budget


def _mp(*budgets):
    return {"objectives": [{"test_budget": b, "budget_allocation_pct": 100.0 / len(budgets),
                            "budget_note": "ميزانيتك الشهرية"} for b in budgets]}


def test_plan_budget_sums_every_objective_not_just_the_first():
    # A funnel plan splits her month across two objectives; the test cells share the WHOLE month.
    assert _plan_test_budget(_mp(9000.0, 21000.0)) == 30000.0
    assert _plan_test_budget(_mp(30000.0)) == 30000.0
    assert _plan_test_budget(_mp(None)) is None                 # honest unknown, not 0
    assert _plan_test_budget({}) is None


def test_cells_show_real_money_when_her_number_exists():
    v2 = {"test_cells": [{"name": "c1", "axis": "audience", "hypothesis": "h",
                          "budget_share": 0.4, "targeting_note": "t", "grounded_in": "g"}]}
    html = _page_cells(v2, has_poster=False, has_reel=False, test_budget=30000.0, currency="جنيه")
    assert "12,000" in html or "12٬000" in html or "12000" in html   # 40% of her month
    # without it, the honest ask stays
    html2 = _page_cells(v2, has_poster=False, has_reel=False, test_budget=None, currency="جنيه")
    assert "بعد إدخال الميزانية" in html2
