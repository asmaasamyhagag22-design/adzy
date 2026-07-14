"""The decision ladder — a PURE function over performance snapshots (PD-1: deterministic, no I/O).

Rule order (v2.2 §6-W6), first match wins:
  1. GUARDRAILS — too little runtime or signal to judge at all -> NO_DECISION.
  2. FATIGUE — high frequency AND a real CTR fall -> ROTATE_CREATIVE (a creative problem; may fire
     even mid-learning, because rotating creative is safe and doesn't touch budget).
  3. LEARNING-PHASE guardrail (T-4) — below ~50 conversions, NEVER kill or scale -> WATCH.
  4. THE LADDER (learning exited, cost target known) — CPL at/below target -> SCALE (<= step cap);
     CPL far above target -> KILL_AND_REPLACE; CPL somewhat above -> REDUCE_BUDGET; else KEEP.
"""
from __future__ import annotations

from typing import List

from media_plan.schemas import _COST_PER_RESULT_METRICS

from decision_engine.schemas import CampaignPolicy, Decision, DecisionKind, PerformanceSnapshot

_KILL_CPL_MULTIPLE = 1.5     # CPL beyond 1.5x target after learning = a loser, replace it


def _ctr_drop_pct(snaps: List[PerformanceSnapshot]) -> float:
    """CTR fall of the most-recent third vs the earliest third, as a percentage (0 if not enough
    data or the baseline is flat/zero). Robust to short series."""
    n = len(snaps)
    if n < 3:
        return 0.0
    k = max(1, n // 3)
    base = [s.ctr for s in snaps[:k]]
    recent = [s.ctr for s in snaps[-k:]]
    b = sum(base) / len(base)
    r = sum(recent) / len(recent)
    if b <= 0:
        return 0.0
    return max(0.0, (b - r) / b * 100.0)


def evaluate(policy: CampaignPolicy, snapshots: List[PerformanceSnapshot]) -> Decision:
    """Deterministic decision for one ad set from its snapshot series. Never raises on ordinary
    input; never moves budget or publishes (PD-7)."""
    snaps = sorted(snapshots, key=lambda s: s.date)
    if not snaps:
        return Decision(kind=DecisionKind.NO_DECISION, triggered_rules=["no_data"],
                        reasons=["no performance snapshots to evaluate"])

    days = len({s.date for s in snaps})
    hours = days * 24
    total_spend = sum(s.spend for s in snaps)
    total_conv = sum(s.conversions for s in snaps)
    overall_cpl = (total_spend / total_conv) if total_conv else None
    latest_freq = snaps[-1].frequency

    kpi = policy.kpi
    target = kpi.target_value
    is_cost = kpi.metric.strip().lower() in _COST_PER_RESULT_METRICS

    # 1) GUARDRAILS — not enough to say anything yet.
    if hours < policy.min_hours:
        return Decision(kind=DecisionKind.NO_DECISION, triggered_rules=["min_hours"],
                        reasons=[f"only {hours}h of runtime (< {policy.min_hours}h minimum)"])
    if total_conv < policy.min_conversions:
        return Decision(kind=DecisionKind.NO_DECISION, triggered_rules=["min_conversions"],
                        reasons=[f"{total_conv} conversions (< {policy.min_conversions} minimum read)"])
    if is_cost and target is not None and total_spend < policy.min_spend_multiple * target:
        need = policy.min_spend_multiple * target
        return Decision(kind=DecisionKind.NO_DECISION, triggered_rules=["min_spend"],
                        reasons=[f"spent {total_spend:.0f} (< {need:.0f} = {policy.min_spend_multiple}x target)"])

    # 2) FATIGUE — creative rotation is budget-safe, so it may fire before the learning gate.
    ctr_drop = _ctr_drop_pct(snaps)
    if latest_freq > policy.fatigue_frequency and ctr_drop > policy.fatigue_ctr_drop_pct:
        return Decision(kind=DecisionKind.ROTATE_CREATIVE,
                        triggered_rules=["fatigue_frequency", "ctr_drop"],
                        reasons=[f"frequency {latest_freq:.1f} > {policy.fatigue_frequency} and "
                                 f"CTR fell {ctr_drop:.0f}% (> {policy.fatigue_ctr_drop_pct:.0f}%) — creative fatigue"])

    # 3) LEARNING-PHASE guardrail (T-4) — never kill/scale before ~50 conversions.
    if total_conv < policy.learning_phase_conversions:
        return Decision(kind=DecisionKind.WATCH, triggered_rules=["learning_phase_not_exited"],
                        reasons=[f"{total_conv} conversions (< {policy.learning_phase_conversions} "
                                 f"learning-phase exit) — hold, do not kill or scale yet"])

    # 4) THE LADDER — learning exited; judge against the cost target.
    if is_cost and target is not None and overall_cpl is not None:
        if overall_cpl <= target:
            return Decision(kind=DecisionKind.SCALE, triggered_rules=["cpl_at_or_below_target"],
                            reasons=[f"CPL {overall_cpl:.1f} <= target {target:.1f} after learning — "
                                     f"scale budget by <= {policy.scale_step_max_pct:.0f}%"])
        if overall_cpl > _KILL_CPL_MULTIPLE * target:
            return Decision(kind=DecisionKind.KILL_AND_REPLACE, triggered_rules=["cpl_far_above_target"],
                            reasons=[f"CPL {overall_cpl:.1f} > {_KILL_CPL_MULTIPLE}x target "
                                     f"{target:.1f} after learning — kill and replace"])
        return Decision(kind=DecisionKind.REDUCE_BUDGET, triggered_rules=["cpl_above_target"],
                        reasons=[f"CPL {overall_cpl:.1f} above target {target:.1f} but within "
                                 f"{_KILL_CPL_MULTIPLE}x — trim budget and watch"])

    # Non-cost target (reach/roas/ctr) or no numeric target: nothing crossed a threshold.
    return Decision(kind=DecisionKind.KEEP, triggered_rules=["no_trigger"],
                    reasons=["learning exited and no rule crossed a threshold — keep running"])
