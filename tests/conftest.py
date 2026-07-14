"""Shared test fixtures.

U7 telemetry writes runs/<run_id>/telemetry.jsonl per pipeline run; tests must not
pollute the repo root, so every test gets an isolated TELEMETRY_ROOT under tmp.
"""
import pytest


@pytest.fixture(autouse=True)
def _telemetry_tmp_root(tmp_path, monkeypatch):
    monkeypatch.setenv("TELEMETRY_ROOT", str(tmp_path / "telemetry_runs"))
    monkeypatch.delenv("TELEMETRY_RUN_ID", raising=False)


@pytest.fixture(autouse=True)
def _vertex_spend_tmp_ledger(tmp_path, monkeypatch):
    """Isolate the Vertex spend ledger + a generous cap so no test writes to the real
    runs/vertex_spend.jsonl or gets a render refused. Tests that exercise the guard itself pass
    their own ledger/cap and are unaffected."""
    import vertex_spend
    monkeypatch.setattr(vertex_spend, "DEFAULT_LEDGER", tmp_path / "vertex_spend.jsonl")
    monkeypatch.setenv("VERTEX_MONTHLY_CAP_USD", "100000")
