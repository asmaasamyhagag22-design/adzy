"""The spend tripwire is a RULE, not a warning — so it gets a test.

Origin (2026-07-15): `python -m benchmark.grade_u1` was run believing it offline. It is not —
it makes one live Gemini call per labelled URL. The false premise then propagated into a fan-out
brief and ~5 agents repeated it: ~72 billed calls, ~$0.59 (logs/spend.md). Nothing in the code
objected. These tests pin the control that now does.
"""
from __future__ import annotations

import pytest

from spend_tripwire import refuse_if_no_spend, spend_is_forbidden


def test_pytest_context_alone_forbids_spend(monkeypatch):
    # even with BASEERA_NO_SPEND unset, running under pytest must forbid spend by itself
    monkeypatch.delenv("BASEERA_NO_SPEND", raising=False)
    monkeypatch.setenv("PYTEST_CURRENT_TEST", "test_x (call)")
    forbidden, why = spend_is_forbidden()
    assert forbidden and "pytest" in why


def test_explicit_no_spend_flag_forbids_spend(monkeypatch):
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.setenv("BASEERA_NO_SPEND", "1")
    forbidden, why = spend_is_forbidden()
    assert forbidden and "BASEERA_NO_SPEND" in why


def test_refuse_raises_systemexit_naming_the_cost_and_the_free_alternative(monkeypatch):
    monkeypatch.setenv("BASEERA_NO_SPEND", "1")
    with pytest.raises(SystemExit) as e:
        refuse_if_no_spend("one live Gemini call per labelled URL",
                           alternative="inject a caller instead")
    msg = str(e.value)
    assert "SPENDS REAL MONEY" in msg                 # the operator sees WHAT they are buying
    assert "one live Gemini call per labelled URL" in msg
    assert "inject a caller instead" in msg           # ...and the free way to get the same answer


def test_a_clean_shell_is_allowed_to_spend(monkeypatch):
    # the tripwire must not brick the real CLI for a human who means it
    monkeypatch.delenv("BASEERA_NO_SPEND", raising=False)
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    assert spend_is_forbidden() == (False, "")
    refuse_if_no_spend("anything")                    # does not raise


def test_every_paid_entrypoint_calls_the_tripwire():
    """CS-92: the old test only checked 2 of the paid mains; 5 others (poster/strategy/reel/
    business_profile) spent with NO tripwire. This asserts EVERY paid entrypoint invokes it."""
    import importlib
    import inspect

    paid_mains = [
        "benchmark.grade_u1", "trends.__main__", "poster.__main__",
        "strategy.__main__", "reel.__main__", "business_profile.__main__",
    ]
    missing = []
    for name in paid_mains:
        mod = importlib.import_module(name)
        src = inspect.getsource(mod.main)
        if "refuse_if_no_spend" not in src:
            missing.append(name)
    assert not missing, f"paid mains missing the spend tripwire: {missing}"


def test_the_tripwire_actually_fires_from_a_paid_main(monkeypatch, tmp_path):
    """BEHAVIOUR (not a source grep): drive strategy.main() down its PAID (LLM) path under a
    no-spend context and prove it raises SystemExit BEFORE building a caller — the wire fires."""
    import sys

    import strategy.__main__ as sm

    prof = tmp_path / "p.json"
    prof.write_text('{"name": {"value": "Acme"}, "offerings": []}', encoding="utf-8")
    monkeypatch.setenv("BASEERA_NO_SPEND", "1")
    monkeypatch.setattr(sys, "argv", ["strategy", str(prof), "--days", "7"])  # NO --no-llm -> paid
    with pytest.raises(SystemExit) as e:
        sm.main()
    assert "SPENDS REAL MONEY" in str(e.value) and "--no-llm" in str(e.value)


def test_the_free_path_of_a_paid_main_is_not_blocked(monkeypatch, tmp_path):
    """The tripwire must guard only the PAID path — strategy --no-llm spends nothing, so it must
    still run under a no-spend context (never over-broadly brick the deterministic fallback)."""
    import sys

    import strategy.__main__ as sm

    prof = tmp_path / "p.json"
    prof.write_text('{"name": {"value": "Acme"}, "offerings": [{"name": {"value": "Widget"}}]}',
                    encoding="utf-8")
    out = tmp_path / "plan.json"
    monkeypatch.setenv("BASEERA_NO_SPEND", "1")
    monkeypatch.setattr(sys, "argv",
                        ["strategy", str(prof), "--no-llm", "--days", "7", "--out", str(out)])
    assert sm.main() == 0                                  # free path runs, no SystemExit
    assert out.is_file()
