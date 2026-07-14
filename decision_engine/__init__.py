"""U3 — Decision Engine + Simulator (v2.2 §5, §6-W6).

A guardrailed decision ladder over campaign PERFORMANCE snapshots (simulated or CSV-imported — no
live ad account needed). `evaluate(policy, snapshots) -> Decision` is a PURE function (PD-1): no I/O,
no LLM, no network — the decision is deterministic code, never a model's opinion. The learning-phase
guardrail (T-4) is the safety invariant: never KILL or SCALE an ad set before it has exited Meta's
~50-conversion learning phase.
"""
from decision_engine.engine import evaluate
from decision_engine.schemas import (
    CampaignPolicy,
    Decision,
    DecisionKind,
    PerformanceSnapshot,
)
from decision_engine.simulator import simulate

__all__ = [
    "PerformanceSnapshot", "CampaignPolicy", "DecisionKind", "Decision",
    "evaluate", "simulate",
]
