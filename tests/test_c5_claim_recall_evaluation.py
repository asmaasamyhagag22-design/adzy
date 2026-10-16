# -*- coding: utf-8 -*-
"""C5: the claim-recall evaluation assets stay runnable, and stay honest.

These tests do NOT assert a score. Pinning "held-out recall == 2.6%" would turn a measurement into
a contract and make improving the extractor look like a regression. What they pin is that the
evaluation still MEASURES something real:

  * the corpus loads and every case is labelled
  * the held-out split genuinely avoids the lexicon (otherwise it measures the list against itself)
  * the harness runs offline and produces the summary fields
  * the direct split still passes, so a drop there is a real extractor regression
  * the blind spot is still materially worse on held-out than on direct

F-02B remains blocked: the fix is an independent semantic proposition extractor feeding the
existing EvidenceLedger, and the configured architecture requires Gemini credentials this
environment does not have. Nothing here substitutes a provider or spends.
"""
from __future__ import annotations

import json

import pytest

from grounding.ledger import extract_claims
from grounding.measure_claim_recall import CORPUS, evaluate, load_cases, summarise


@pytest.fixture(scope="module")
def rows():
    return evaluate(load_cases())["rows"]


def test_corpus_is_present_and_well_formed():
    data = json.loads(CORPUS.read_text(encoding="utf-8"))
    cases = data["cases"]
    assert len(cases) >= 40, "the corpus is too small to say anything"
    ids = [c["id"] for c in cases]
    assert len(ids) == len(set(ids)), "duplicate case ids"
    for c in cases:
        assert c["split"] in ("direct", "held_out")
        assert isinstance(c["hard_claim"], bool)
        assert c["text"].strip(), f"{c['id']} has no text"
        assert c["proposition"].strip(), f"{c['id']} states no proposition"


def test_the_corpus_carries_both_splits_and_real_negatives(rows):
    hard = [r for r in rows if r["hard_claim"]]
    assert sum(1 for r in hard if r["split"] == "direct") >= 10
    assert sum(1 for r in hard if r["split"] == "held_out") >= 20
    assert sum(1 for r in rows if not r["hard_claim"]) >= 5, (
        "without non-claims the false-positive rate is unmeasurable")


def test_held_out_cases_are_paraphrases_not_keyword_swaps(rows):
    """The held-out split must not smuggle in a lexicon member.

    This is the guard that keeps the measurement meaningful: a 'paraphrase' that still contains a
    listed word would be caught for the wrong reason and would quietly inflate the score.
    """
    leaked = []
    for r in rows:
        if r["split"] != "held_out" or not r["hard_claim"]:
            continue
        kinds = {k for k in r["kinds"] if k != "number"}
        if kinds:
            leaked.append((r["id"], r["text"], sorted(kinds)))
    # One incidental match is tolerated and named, not hidden: "in the FIRST two years" uses
    # `first` as an ordinal, not as a ranking claim, so the extractor catches it for the wrong
    # reason. Anything beyond that means the split has been contaminated.
    assert len(leaked) <= 1, f"held-out cases contain lexicon members: {leaked}"


def test_the_harness_runs_offline_and_reports_the_expected_fields(rows):
    s = summarise(rows)
    for key in ("overall_recall", "direct_recall", "held_out_recall", "held_out_escape",
                "false_positive_rate", "by_category"):
        assert key in s, f"the summary lost `{key}`"
    assert 0.0 <= s["held_out_recall"] <= 100.0
    assert s["by_category"], "no per-category breakdown"


def test_the_direct_split_is_fully_covered(rows):
    """A drop here is a genuine extractor regression, not a corpus artefact."""
    direct = [r for r in rows if r["hard_claim"] and r["split"] == "direct"]
    missed = [r["text"] for r in direct if not r["detected"]]
    assert not missed, f"the extractor stopped catching its OWN lexicon cases: {missed}"


def test_the_extractor_does_not_invent_claims_in_ordinary_copy(rows):
    """Precision is the closed lexicon's real strength; losing it would be the worse trade."""
    false_positives = [r["text"] for r in rows if not r["hard_claim"] and r["detected"]]
    assert not false_positives, f"non-claims were flagged as hard claims: {false_positives}"


def test_the_semantic_blind_spot_is_still_material(rows):
    """F-02B's premise. If this ever fails, the debt is paid and C5 should be re-stated.

    Deliberately a wide margin rather than an exact number: the claim being pinned is
    "paraphrases escape far more than lexicon hits", not any particular percentage.
    """
    s = summarise(rows)
    assert s["direct_recall"] - s["held_out_recall"] > 40.0, (
        f"held-out recall ({s['held_out_recall']:.1f}%) has caught up with direct "
        f"({s['direct_recall']:.1f}%) — re-open F-02B's status, the blind spot may be closed")


def test_a_paraphrased_hard_claim_reaches_the_gate_as_nothing():
    """The mechanism, stated once in one line: no claim extracted means nothing to audit."""
    assert extract_claims("We are the best pharmacy in Jeddah"), "lexicon case must be caught"
    assert extract_claims("No pharmacy in Jeddah looks after you the way we do") == [], (
        "if this now yields a claim the extractor gained semantics — update C5's conclusion")
