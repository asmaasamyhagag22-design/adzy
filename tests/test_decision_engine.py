"""U3 Decision Engine (decision_engine.engine.evaluate) — pure, deterministic, hermetic.

Rule order (v2.2 §6-W6): guardrails -> fatigue -> learning-phase floor -> the ladder. The
learning-phase guardrail (T-4) is the safety invariant: no KILL/SCALE before ~50 conversions.
"""
from __future__ import annotations

from datetime import date, timedelta

from decision_engine import CampaignPolicy, Decision, DecisionKind, PerformanceSnapshot, evaluate
from media_plan.schemas import KPITarget


def _policy(**kw) -> CampaignPolicy:
    kw.setdefault("kpi", KPITarget(metric="cost_per_lead", target_value=50.0, unit="EGP"))
    kw.setdefault("test_budget", 1000.0)
    return CampaignPolicy(**kw)


def _series(days, *, conv_per_day, cpl, ctr=0.03, freq=1.5, spend_per_conv=None):
    """A flat daily series with controlled totals (spend derived from cpl unless overridden)."""
    out = []
    for d in range(days):
        spc = spend_per_conv if spend_per_conv is not None else cpl
        out.append(PerformanceSnapshot(
            campaign_ref="c", ad_ref="a", date=date(2026, 1, 1) + timedelta(days=d),
            spend=round(spc * conv_per_day, 2), impressions=10000, clicks=int(10000 * ctr),
            ctr=ctr, frequency=freq, conversions=conv_per_day, cpl=cpl))
    return out


def test_empty_snapshots_is_no_decision():
    assert evaluate(_policy(), []).kind is DecisionKind.NO_DECISION


def test_guardrail_min_hours():
    d = evaluate(_policy(), _series(2, conv_per_day=40, cpl=40))   # 48h < 72h
    assert d.kind is DecisionKind.NO_DECISION and "min_hours" in d.triggered_rules


def test_guardrail_min_conversions():
    d = evaluate(_policy(), _series(4, conv_per_day=1, cpl=40))    # 4 conv < 5
    assert d.kind is DecisionKind.NO_DECISION and "min_conversions" in d.triggered_rules


def test_guardrail_min_spend():
    # 6 conversions clears the conversion floor, but total spend (6*10=60) < 3*target(150).
    d = evaluate(_policy(), _series(6, conv_per_day=1, cpl=10))
    assert d.kind is DecisionKind.NO_DECISION and "min_spend" in d.triggered_rules


def test_learning_phase_guardrail_holds_below_50_conversions():
    # 30 conversions, CPL below target — but learning not exited => WATCH, never SCALE.
    d = evaluate(_policy(), _series(6, conv_per_day=5, cpl=40))
    assert d.kind is DecisionKind.WATCH and "learning_phase_not_exited" in d.triggered_rules


def test_ladder_scale_when_cpl_at_or_below_target_post_learning():
    d = evaluate(_policy(), _series(14, conv_per_day=6, cpl=40))   # 84 conv, CPL 40 <= 50
    assert d.kind is DecisionKind.SCALE and "cpl_at_or_below_target" in d.triggered_rules
    assert any("20%" in r for r in d.reasons)                      # step cap surfaced


def test_ladder_kill_when_cpl_far_above_target():
    d = evaluate(_policy(), _series(14, conv_per_day=6, cpl=150))  # 84 conv, CPL 150 > 1.5*50
    assert d.kind is DecisionKind.KILL_AND_REPLACE


def test_ladder_reduce_when_cpl_moderately_above_target():
    d = evaluate(_policy(), _series(14, conv_per_day=6, cpl=65))   # 84 conv, 50 < 65 <= 75
    assert d.kind is DecisionKind.REDUCE_BUDGET


def test_fatigue_rotates_creative_even_with_good_conversions():
    # high frequency + a real CTR fall (0.04 -> 0.015) across the run, plenty of conversions.
    days = 12
    series = []
    for d in range(days):
        ramp = d / (days - 1)
        series.append(PerformanceSnapshot(
            campaign_ref="c", ad_ref="a", date=date(2026, 1, 1) + timedelta(days=d),
            spend=240, impressions=10000, clicks=int(10000 * (0.04 - 0.028 * ramp)),
            ctr=round(0.04 - 0.028 * ramp, 5), frequency=1.0 + 4.0 * ramp,
            conversions=6, cpl=40))
    d = evaluate(_policy(), series)
    assert d.kind is DecisionKind.ROTATE_CREATIVE and "ctr_drop" in d.triggered_rules


def test_non_cost_metric_keeps_when_no_threshold_crossed():
    pol = _policy(kpi=KPITarget(metric="reach", target_value=None, unit="count"))
    d = evaluate(pol, _series(14, conv_per_day=6, cpl=40))
    assert d.kind is DecisionKind.KEEP                             # no cost target -> ladder can't fire


def test_decision_is_strict_and_serializable():
    d = evaluate(_policy(), _series(14, conv_per_day=6, cpl=40))
    assert isinstance(d, Decision) and Decision.model_validate(d.model_dump()).kind is d.kind
