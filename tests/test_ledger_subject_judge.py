# -*- coding: utf-8 -*-
"""C2 residual: the optional semantic same-subject judge for the Evidence Ledger.

The deterministic gate stays LENIENT (the project's designed number-only grounding) with NO
judge, so nothing regresses. An injected judge resolves the token-disjoint-subject ambiguity
— accepting synonyms / inflections ("5000 experts" <- "5000 youth", the Arabic pioneer
inflection) and rejecting fabrications ("100 gifts" <- "100 stores", "best coffee" <- "best
regards"). Any judge error -> lenient (never fabricate a rejection).

Hermetic: mock judge / mock caller, no network, no real LLM.
"""
from types import SimpleNamespace

from grounding import EvidenceLedger, make_subject_judge
from grounding.subject_judge import _window


def test_window_centers_on_token_so_far_proving_context_survives():
    # A blind head-slice (text[:400]) clips away the sentence that proves the match when the token
    # sits deep in a long block. _window centers a 400-char window on the token instead.
    short = "the best coffee in town"
    assert _window(short, "best") == short           # <=400 chars -> unchanged (old [:400] behaviour)
    far = ("x" * 900) + " served with the finest BEST brew imaginable " + ("y" * 900)
    w = _window(far, "BEST")
    assert "BEST brew" in w                            # token + its context preserved...
    assert len(w) <= 410 < len(far)                    # ...in a ~400-char window, not the whole block
    assert _window(("z" * 900), "absent")[:1] == "z"  # token not found -> head-slice fallback
    assert _window("", "best") == "" and _window(None, "best") == ""  # None / empty safe


def _num_sourced(led, copy):
    vs = [v for v in led.audit_text(copy) if v.claim.kind == "number"]
    return bool(vs) and all(v.sourced for v in vs)


def _group_sourced(led, copy, kind):
    vs = [v for v in led.audit_text(copy) if v.claim.kind == kind]
    return bool(vs) and all(v.sourced for v in vs)


def _led(desc=None, tagline=None, judge=None):
    prof = {"name": {"value": "X"}}
    if desc:
        prof["description"] = {"value": desc}
    if tagline:
        prof["tagline"] = {"value": tagline}
    return EvidenceLedger.from_profile(prof, subject_judge=judge)


# --- default (no judge): lenient, no regression -------------------------------------------
def test_no_judge_keeps_lenient_number_grounding():
    assert _num_sourced(_led("We have 100 stores across Egypt"), "Get 100 gifts")


import pytest


@pytest.mark.xfail(reason="CS-46 KNOWN OPEN HOLE: with no judge, a disjoint-subject superlative "
                          "('best coffee' <- 'Best regards') is still certified. The fix needs a "
                          "subject judge wired into the production ledger build — see "
                          "logs/OPEN_PROBLEMS.md. This asserts the CORRECT (not-certified) behavior "
                          "so it flips to a real pass the day CS-46 is fixed, WITHOUT the suite "
                          "certifying the fail-open in the meantime.",
                   strict=True)
def test_no_judge_does_not_certify_a_disjoint_subject_superlative():
    # measured: this fail-open certifies a fabricated superlative brand-tier on 12/18 real profiles.
    assert not _group_sourced(_led("Best regards from the team"), "The best coffee in town", "best")


# --- with a judge: catch fabrication, preserve synonym / inflection ------------------------
def test_judge_rejects_number_fabrication():
    j = lambda c, e, t: not ("gift" in c and "store" in e)   # noqa: E731
    assert not _num_sourced(_led("We have 100 stores", judge=j), "Get 100 gifts")


def test_judge_preserves_number_synonym():
    assert _num_sourced(_led("We train 5000 youth every year", judge=lambda c, e, t: True),
                        "Train 5000 experts")


def test_judge_rejects_superlative_fabrication():
    j = lambda c, e, t: not ("coffee" in c and "regard" in e)   # noqa: E731
    assert not _group_sourced(_led("Best regards from the team", judge=j),
                              "The best coffee", "best")


def test_judge_preserves_arabic_inflection():
    # روّاد التحول الرقمي  <-  الرواد الرقميون  (same 'first' group, different inflection)
    led = _led(tagline="الرواد الرقميون", judge=lambda c, e, t: True)
    assert _group_sourced(led, "روّاد التحول الرقمي", "first")


# --- judge error / None -> lenient (never fabricates a rejection) --------------------------
def test_judge_error_falls_back_to_lenient():
    def boom(c, e, t):
        raise RuntimeError("llm down")
    assert _num_sourced(_led("100 stores", judge=boom), "Get 100 gifts")


def test_judge_none_verdict_is_lenient():
    assert _num_sourced(_led("100 stores", judge=lambda c, e, t: None), "Get 100 gifts")


def test_judge_is_cached_one_call_per_pair():
    calls = {"n": 0}
    def counting(c, e, t):
        calls["n"] += 1
        return False
    led = _led("100 stores here, 100 stores there", judge=counting)
    _num_sourced(led, "Get 100 gifts")
    _num_sourced(led, "Get 100 gifts")           # repeat — should hit the cache
    assert calls["n"] <= 2                        # not one-per-entry-per-call


# --- make_subject_judge (real builder, mock caller) ---------------------------------------
def test_make_subject_judge_none_caller_returns_none():
    assert make_subject_judge(None) is None


def test_make_subject_judge_parses_response_and_builds_prompt():
    captured = {}

    def caller(system, user, model, group_name=None):
        captured["user"] = user
        captured["system"] = system
        captured["group"] = group_name
        return SimpleNamespace(same_subject=False), {}

    judge = make_subject_judge(caller)
    assert judge("The best coffee", "Best regards", "best") is False
    assert "best coffee" in captured["user"].lower()
    assert "best regards" in captured["user"].lower()
    assert "'best'" in captured["user"]
    assert captured["group"] == "ledger_subject_judge"


def test_make_subject_judge_caller_error_returns_none():
    def caller(system, user, model, group_name=None):
        raise RuntimeError("api down")
    assert make_subject_judge(caller)("a", "b", "x") is None


def test_make_subject_judge_non_bool_response_returns_none():
    def caller(system, user, model, group_name=None):
        return SimpleNamespace(same_subject="maybe"), {}
    assert make_subject_judge(caller)("a", "b", "x") is None
