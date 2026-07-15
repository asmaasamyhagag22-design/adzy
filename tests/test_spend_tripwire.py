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


def test_the_paid_entrypoints_actually_call_the_tripwire():
    """The rule is only real if the paid mains invoke it. Static check — importing is enough;
    we must never CALL these mains (that is the whole point)."""
    import inspect

    import benchmark.grade_u1 as g
    import trends.__main__ as t
    for mod in (g, t):
        src = inspect.getsource(mod.main)
        assert "refuse_if_no_spend" in src, f"{mod.__name__}.main() does not trip the spend wire"
