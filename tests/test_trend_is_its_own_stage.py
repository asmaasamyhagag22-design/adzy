# -*- coding: utf-8 -*-
"""Trend is a stage, not a by-product of the SWOT — and the owner's own numbers have a home.

THE OWNER'S RULING (2026-07-26): «يبقا الترند ميظهرش معاه سوات، دا في تاب لوحدها · وبعدين السوات
بناءً عليها · وبعدين الاستراتيجي بناءً عليهم والميديا بلان والكالندر». Trend first, standing alone;
SWOT built on it; strategy on both.

MEASURED before this file was written, across every stored brand:

    brand                  has_swot   swot.trends   top-level `trends`
    alfouadpharmacies_com    yes           6              ABSENT
    nahdionline_com          yes           5              ABSENT
    nanocareegypt_com        yes           6              ABSENT
    pepsi_com                 NO           0              ABSENT

Trends exist ONLY inside the SWOT, because `api/webapp_analysis.py:159` attaches them to the SWOT
object after synthesis (`swot.market_trends = _trend_rows`) so they travel through the same
serializer. Three consequences, all real:

  * NO SWOT MEANS NO TRENDS, EVER. pepsi_com has market signals available and a Trend tab that
    cannot show them, because nothing fetched them outside a SWOT run.
  * Regenerating a SWOT re-fetches and REPLACES the trends, so a paid Serper call is spent again
    for data that had not changed.
  * `market_trends` is not a field on the SWOT dataclass — it is set dynamically, so
    `dataclasses.asdict` never captured it. That is the bug fixed earlier today; the CAUSE is this
    piggy-backing.

AND THE OWNER'S NUMBERS. `media_plan_v2.engine` already accepts `capacity`, `monthly_budget` and
`margin`, and already announces their absence in `open_inputs` — the panel prints "Still needed
from you: طاقة الاستيعاب الفعلية / الميزانية الشهرية". There has never been anywhere to type them.
The war-room had `dashboard/owner_inputs.py` for exactly this (its docstring quotes her 2026-07-20
ask, «حط مكان يحطها فيه») and it was deleted with that dashboard. Her question — "لو انا محدد
ميزانيه اكتبها فين؟ انا قاعده ف الديفولت مقدرش اغير فيه" — is the gap made visible.

BACKWARD COMPATIBLE BY CONSTRUCTION: `swot.trends` stays as a mirror, and readers fall back to it,
so the six brands stored today keep working untouched.
"""
from __future__ import annotations

import pytest


# ---------------------------------------------------------------------
# 1 — trends live at the top level, and survive a SWOT that has none
# ---------------------------------------------------------------------

def test_trends_are_stored_on_the_brand_not_only_inside_the_swot():
    from api.webapp_analysis import trends_patch
    rows = [{"title": "T1", "url": "https://a.test", "matched_terms": ["x"]}]
    patch = trends_patch(rows)
    assert patch.get("trends") == rows, "the brand must carry its own trends key"
    assert patch.get("has_trends") is True


def test_an_empty_fetch_is_recorded_as_checked_not_as_missing():
    """A brand whose market genuinely has no on-topic coverage must be distinguishable from a
    brand nobody has looked at — otherwise the Trend tab cannot tell 'none found' from 'not run'."""
    from api.webapp_analysis import trends_patch
    patch = trends_patch([])
    assert patch.get("trends") == []
    assert patch.get("has_trends") is True, (
        "an empty result is still a result; has_trends False would mean 'never fetched'")


def test_reading_trends_prefers_the_brand_and_falls_back_to_the_swot():
    """The six brands stored today have them only under swot — they must keep working."""
    from api.webapp_analysis import trends_of
    legacy = {"swot": {"trends": [{"title": "old"}]}}
    assert [t["title"] for t in trends_of(legacy)] == ["old"]

    modern = {"trends": [{"title": "new"}], "swot": {"trends": [{"title": "old"}]}}
    assert [t["title"] for t in trends_of(modern)] == ["new"], "the brand key must win"

    assert trends_of({}) == []


def test_the_swot_reuses_trends_already_fetched_instead_of_paying_again():
    """The SWOT stage must accept trends it is given. Re-fetching on every regeneration spends a
    paid Serper call for data that has not changed."""
    import inspect

    from api.webapp_analysis import _compute_swot
    params = inspect.signature(_compute_swot).parameters
    assert "trends" in params, (
        "_compute_swot cannot be handed existing trends, so every SWOT run re-fetches")
    assert params["trends"].default is None, "trends must be optional (None = fetch them)"


# ---------------------------------------------------------------------
# 2 — the owner's numbers have somewhere to live
# ---------------------------------------------------------------------

def test_owner_numbers_are_validated_not_silently_clamped():
    """Out-of-range values are DROPPED. Clamping 500% margin to 95% would answer a question the
    owner did not ask, with a number she never typed."""
    from api.webapp_analysis import sanitize_owner_inputs as san
    assert san({"profit_margin_pct": 40, "capacity_per_day": 25,
                "monthly_budget": 15000}) == {"profit_margin_pct": 40.0,
                                              "capacity_per_day": 25,
                                              "monthly_budget": 15000.0}
    assert "profit_margin_pct" not in san({"profit_margin_pct": 500})
    assert "profit_margin_pct" not in san({"profit_margin_pct": 0})
    assert "capacity_per_day" not in san({"capacity_per_day": -3})
    assert "monthly_budget" not in san({"monthly_budget": 0})
    assert san({"capacity_per_day": "twelve"}) == {}
    assert san({}) == {}


def test_a_typed_capacity_changes_the_plan_from_a_default_to_her_number():
    """The whole point: `capacity_source` must flip from `config_default` to `human`, and the
    "replace it" note must disappear."""
    from media_plan_v2.reality_check import build_reality_check
    profile = {"category": "ecommerce", "identity": {"name": "X"}}

    default = build_reality_check(profile)
    assert default.capacity_source == "config_default"

    hers = build_reality_check(profile, capacity=25)
    assert hers.capacity_source == "human"
    assert hers.max_daily_leads == 25
    assert hers.daily_spend_ceiling == round(25 * hers.kill_switch.max_cpl, 2), (
        "the spend ceiling must be recomputed from her number, not left on the default")


def test_the_open_inputs_list_shrinks_once_she_has_answered():
    """`open_inputs` is the panel's "Still needed from you". Answering must remove the item — a
    list that keeps asking for what it already has is noise the user learns to ignore."""
    from media_plan_v2.engine import build_media_plan_v2
    profile = {"category": "ecommerce", "identity": {"name": "X"}}

    before = build_media_plan_v2(profile)
    after = build_media_plan_v2(profile, capacity=25, monthly_budget=15000.0)
    assert any("طاقة الاستيعاب" in s for s in before.open_inputs)
    assert not any("طاقة الاستيعاب" in s for s in after.open_inputs)
    assert not any("الميزانية" in s for s in after.open_inputs)


@pytest.mark.parametrize("field,element", [
    ("monthly_budget", "owner-budget"),
    ("capacity_per_day", "owner-capacity"),
    ("profit_margin_pct", "owner-margin"),
])
def test_the_ui_has_a_field_for_each_number_the_plan_asks_for(field, element):
    """It is not enough for the API to accept them. She asked "اكتبها فين" — there has to be a box."""
    from pathlib import Path
    spa = (Path(__file__).resolve().parents[1] / "frontend" / "index.html").read_text(
        encoding="utf-8", errors="replace")
    assert f'id="{element}"' in spa, f"no input for {field}"
    assert "saveOwnerInputs" in spa, "nothing sends the typed numbers anywhere"
