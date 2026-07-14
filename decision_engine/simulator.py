"""Campaign simulator (v2.2 §6-W6): synthetic daily PerformanceSnapshot series for three archetypes —
`winner`, `loser`, `fatigue` — so the Decision Engine and the closed loop can be demonstrated with NO
live ad account. Deterministic: a seeded RNG (no global state, no wall-clock), so a given (scenario,
days, seed) always yields the same series."""
from __future__ import annotations

import random
from datetime import date, timedelta
from typing import List

from decision_engine.schemas import PerformanceSnapshot

_SCENARIOS = ("winner", "loser", "fatigue")
_EPOCH = date(2026, 1, 1)   # a fixed base date (no wall-clock — reproducible)


def simulate(scenario: str, *, days: int = 14, seed: int = 0, target_cpl: float = 50.0,
             campaign_ref: str = "sim_campaign", ad_ref: str = "sim_ad") -> List[PerformanceSnapshot]:
    """A daily snapshot series for one of `winner|loser|fatigue`. Tuned so, over `days`>=7, each
    archetype crosses the intended decision thresholds (winner->SCALE, loser->KILL_AND_REPLACE,
    fatigue->ROTATE_CREATIVE) when judged by `engine.evaluate` with a matching cost-per-lead policy."""
    if scenario not in _SCENARIOS:
        raise ValueError(f"unknown scenario {scenario!r}; expected one of {_SCENARIOS}")
    rng = random.Random(seed)
    out: List[PerformanceSnapshot] = []

    def jitter(x: float, pct: float = 0.08) -> float:
        return x * (1.0 + rng.uniform(-pct, pct))

    for d in range(days):
        day = _EPOCH + timedelta(days=d)
        if scenario == "winner":
            conv = max(1, int(round(jitter(6))))            # ~6/day -> >50 over 14 days (learning exits)
            cpl = jitter(target_cpl * 0.8)                  # below target -> SCALE
            ctr = jitter(0.030)                             # stable, healthy
            freq = jitter(1.4)                              # low -> no fatigue
        elif scenario == "loser":
            conv = max(1, int(round(jitter(5))))            # ~5/day -> >50 over 14 days (learning exits)
            cpl = jitter(target_cpl * 3.0)                  # far above target -> KILL_AND_REPLACE
            ctr = jitter(0.012)
            freq = jitter(1.8)
        else:  # fatigue — frequency climbs, CTR decays across the run
            ramp = d / max(1, days - 1)                     # 0 -> 1 across the run
            conv = max(1, int(round(jitter(6))))            # enough to pass the min-read guardrails
            cpl = jitter(target_cpl)
            ctr = jitter(0.032 * (1.0 - 0.55 * ramp))       # ~45% CTR fall start->end (> 25%)
            freq = jitter(1.2 + 4.0 * ramp)                 # ramps past 3.5 by the end

        spend = round(cpl * conv, 2)
        clicks = max(conv, int(round(jitter(conv * 12))))   # clicks >= conversions
        impressions = max(clicks, int(round(clicks / max(ctr, 1e-6))))
        out.append(PerformanceSnapshot(
            campaign_ref=campaign_ref, ad_ref=ad_ref, date=day, spend=spend,
            impressions=impressions, clicks=clicks, ctr=round(ctr, 5),
            frequency=round(freq, 3), conversions=conv, cpl=round(cpl, 2)))
    return out
