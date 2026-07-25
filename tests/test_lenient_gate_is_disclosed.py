# -*- coding: utf-8 -*-
"""P-24: the ledger's subject gate runs LENIENT without a judge — and never says so.

`grounding/ledger.py:659` states it plainly in its own docstring: "No judge -> lenient (the
project's number-only grounding)". The disjoint-subject path — the one that stops "we are the
best" being certified by evidence that happens to contain the word "best" about something else —
degrades to accept-by-default whenever `subject_judge` is None.

That is a defensible design (a blocked or offline caller must not break a grounded run) and an
indefensible silence. `EvidenceLedger.from_profile(profile)` with no judge is what
`dashboard/run.py:553` and every `grounding/measure_*.py` builds, and the SWOT path only gets a
judge when a live model caller exists — offline, that path silently reverts to the lenient gate
too.

WHY IT MATTERS TODAY specifically: every SWOT item and every social-comment theme written this
session passes through this same gate on its way to the client. If the gate is unenforced, the
guarantee those items are shipped under is not the guarantee the client is told about.

The fix here is DISCLOSURE, not enforcement — buying a judge for every run is the owner's spend
decision, not one to make silently. The ledger states which mode it ran in, and the SWOT carries
that statement to the client with the rest of its notes.

Hermetic: no model, no network — the offline path is exactly the case under test.
"""
from __future__ import annotations

from competitor.matrix import ComparativeGapMatrix, MatrixColumn
from competitor.swot import synthesize_swot
from grounding.ledger import EvidenceLedger

_PROFILE = {
    "name": "Example",
    "category": "services",
    "value_propositions": ["Fast delivery", "Trusted by many"],
    "offerings": [{"name": "Thing"}],
    "source_url": "https://example.test/",
}


# --- the ledger states which mode it ran in ------------------------------------------------------

def test_a_ledger_built_with_no_judge_reports_a_lenient_gate():
    led = EvidenceLedger.from_profile(_PROFILE)
    assert led.strict_subject_gate is False, (
        "the ledger cannot say whether its subject gate was actually enforced")


def test_a_ledger_built_with_a_judge_reports_a_strict_gate():
    led = EvidenceLedger.from_profile(_PROFILE, subject_judge=lambda c, e, t: True)
    assert led.strict_subject_gate is True


# --- and the SWOT carries that statement to the client -------------------------------------------

def test_the_swot_discloses_that_the_gate_ran_unenforced(monkeypatch):
    """Offline is the default for this path: `default_caller(strong=False)` returns None, so
    `make_subject_judge(None)` returns None, so the gate is lenient. The client must be told."""
    import business_profile.llm as llm
    monkeypatch.setattr(llm, "default_caller", lambda **kw: None, raising=False)
    swot = synthesize_swot(
        ComparativeGapMatrix(columns=[MatrixColumn("You", {})], gaps=[]), profile=_PROFILE)
    joined = " ".join(swot.notes or [])
    assert "غير مُفعَّلة" in joined or "بلا حَكَم" in joined, (
        f"the lenient gate shipped in silence: {swot.notes}")


def test_the_disclosure_names_what_is_weakened_not_just_that_something_is():
    """"A check was skipped" is an apology. Naming WHICH check, and what it would have caught,
    is what lets an owner decide whether to pay for it."""
    import business_profile.llm as llm
    import pytest
    mp = pytest.MonkeyPatch()
    mp.setattr(llm, "default_caller", lambda **kw: None, raising=False)
    try:
        swot = synthesize_swot(
            ComparativeGapMatrix(columns=[MatrixColumn("You", {})], gaps=[]), profile=_PROFILE)
    finally:
        mp.undo()
    joined = " ".join(swot.notes or [])
    assert "تفضيل" in joined or "الأفضل" in joined, joined


def test_a_swot_with_no_profile_is_not_accused_of_a_weak_gate():
    """No profile means no ledger was built at all — there is no gate to report on, and inventing
    a caveat is the same overclaim pointing the other way."""
    swot = synthesize_swot(ComparativeGapMatrix(columns=[MatrixColumn("You", {})], gaps=[]))
    joined = " ".join(swot.notes or [])
    assert "غير مُفعَّلة" not in joined
