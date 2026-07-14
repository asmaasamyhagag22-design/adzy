"""U3 campaign simulator — the three archetypes reach their intended decisions, deterministically
(v2.2 §6-W6 gate). Hermetic, no network."""
from __future__ import annotations

import pytest

from decision_engine import CampaignPolicy, DecisionKind, evaluate, simulate
from decision_engine.csv_import import dump_snapshots_csv, load_snapshots_csv
from media_plan.schemas import KPITarget


def _policy():
    return CampaignPolicy(kpi=KPITarget(metric="cost_per_lead", target_value=50.0, unit="EGP"),
                          test_budget=1000.0)


@pytest.mark.parametrize("scenario,expected", [
    ("winner", DecisionKind.SCALE),
    ("loser", DecisionKind.KILL_AND_REPLACE),
    ("fatigue", DecisionKind.ROTATE_CREATIVE),
])
def test_each_scenario_reaches_its_decision(scenario, expected):
    d = evaluate(_policy(), simulate(scenario, days=14, seed=7, target_cpl=50.0))
    assert d.kind is expected, (scenario, d.kind, d.reasons)


def test_simulator_is_deterministic():
    a = simulate("winner", days=10, seed=3)
    b = simulate("winner", days=10, seed=3)
    assert [s.model_dump() for s in a] == [s.model_dump() for s in b]
    c = simulate("winner", days=10, seed=4)
    assert [s.model_dump() for s in a] != [s.model_dump() for s in c]   # a different seed differs


def test_simulator_series_length_and_shape():
    s = simulate("loser", days=9, seed=1)
    assert len(s) == 9 and all(x.impressions >= x.clicks >= x.conversions for x in s)


def test_unknown_scenario_raises():
    with pytest.raises(ValueError):
        simulate("magic", days=5)


def test_csv_round_trip_preserves_snapshots():
    snaps = simulate("loser", days=4, seed=2)
    back = load_snapshots_csv(dump_snapshots_csv(snaps))
    assert [s.model_dump() for s in snaps] == [s.model_dump() for s in back]
