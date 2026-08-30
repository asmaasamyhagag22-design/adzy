# -*- coding: utf-8 -*-
"""A SWOT must be prioritised and relative — not a flat list of absolute facts.

SOURCE: Pickton & Wright (1998), "What's SWOT in strategic analysis?", Strategic Change 7, 101-109
(the owner's paper). Its Table 1 names three limitation classes, and our shipped SWOT has all three.

MEASURED, from the stored nanocare SWOT:
  * NINE strengths, flat, every one equally weighted. Paper: "factors which are given equal
    importance"; "weak opportunities may appear to balance strong threats".
  * FIVE of them are the brand's OWN marketing copy re-served as analysis
    (`value_propositions`, claim_strength=internally_supported). Paper: "factors represent
    opinions not fact" — and here the opinion is the subject's own about itself.
  * The rest are website mechanics — "Clear conversion paths (1 CTA)", "Active social presence
    (3 channels)", "No WhatsApp contact channel". Paper: "factors described broadly: lack of
    specificity" — broad compared to WHAT? Nothing in the output says.
  * Not one item compares the brand to a competitor. Paper (citing Porter, Ansoff, Johnson &
    Scholes): strengths and weaknesses "are only such in comparison to the competition ... They
    are, thus, relative and not absolute."

THE CURES THE PAPER PRESCRIBES, implemented here deterministically — never by an LLM, exactly like
the existing `claim_strength` ladder:
  * Opportunity/Threat matrices (Kotler): score probability of occurrence x likely impact. A factor
    scoring low on both "could even be removed entirely from the SWOT analysis due to its lack of
    relevance".
  * Performance-Importance matrix for S/W: importance x performance, "considered relative to the
    firm's competitors". Attention goes to high-importance + low-performance.

Everything scored here is MEASURED off the peer set that is already in the matrix — prevalence
among competitors and the size of the gap. Where there are no peers, the score is `unknown` and
says so, rather than inventing a number. Universal (D-8): nothing keys off a vertical, country or
brand. Hermetic."""
from __future__ import annotations

import pytest

from competitor.matrix import ComparativeGapMatrix, Dimension, DimensionGap, MatrixColumn
from competitor.swot_priority import Priority, score_gap, score_opinion_item


def _dim(key="whatsapp", kind="bool", label="WhatsApp contact"):
    return Dimension(key, label, "scraped", kind, "higher_better")


def _gap(*, verdict, subject, peers, kind="bool"):
    return DimensionGap(dimension=_dim(kind=kind), subject_value=subject,
                        competitor_values=dict(peers), verdict=verdict, detail="d")


# --- importance is MEASURED market prevalence, not an opinion ---------------------------------

def test_a_dimension_almost_every_peer_has_is_high_importance():
    p = score_gap(_gap(verdict="behind", subject=False,
                       peers={"a": True, "b": True, "c": True, "d": True}))
    assert p.importance == "high"
    assert "4" in p.basis, f"the basis must name the measurement: {p.basis!r}"


def test_a_dimension_almost_no_peer_has_is_low_importance():
    p = score_gap(_gap(verdict="behind", subject=False,
                       peers={"a": False, "b": False, "c": False, "d": True}))
    assert p.importance == "low"


def test_with_no_peers_importance_is_unknown_and_says_so():
    """The paper's core claim: without comparison there IS no strength or weakness."""
    p = score_gap(_gap(verdict="behind", subject=False, peers={}))
    assert p.importance == "unknown"
    assert p.basis.strip(), "an unmeasurable score must still explain itself"
    assert not p.focus, "an unmeasured factor cannot be a priority"


# --- the Performance-Importance matrix ---------------------------------------------------------

def test_important_and_behind_is_the_focus_quadrant():
    p = score_gap(_gap(verdict="behind", subject=False,
                       peers={"a": True, "b": True, "c": True}))
    assert p.performance == "behind" and p.importance == "high"
    assert p.focus is True, "the paper's 'pay particular attention here' cell"


def test_important_and_ahead_is_a_real_strength_but_not_the_focus():
    p = score_gap(_gap(verdict="ahead", subject=True,
                       peers={"a": False, "b": False, "c": False}))
    assert p.performance == "ahead"
    assert p.focus is False


def test_being_the_only_one_with_a_capability_is_a_HIGH_importance_strength():
    """Prevalence and rarity are opposite signals and the axis flips with the verdict.

    Found by an existing test, not by review: the first version scored importance as prevalence for
    every verdict, so "You have WhatsApp; Peer does not" — a sole differentiator — came out
    low-importance and was DELETED by the removal rule. Being the only one who offers something is
    the definition of competitive advantage, which the paper names as the firm's task."""
    alone = score_gap(_gap(verdict="ahead", subject=True,
                           peers={"a": False, "b": False, "c": False}))
    assert alone.importance == "high", "a sole differentiator must not be scored as trivial"
    assert alone.drop is False, "the removal rule would delete the brand's best asset"


def test_being_ahead_on_something_everyone_has_is_a_weak_strength():
    common = score_gap(_gap(verdict="ahead", subject=True,
                            peers={"a": True, "b": True, "c": True}))
    alone = score_gap(_gap(verdict="ahead", subject=True,
                           peers={"a": False, "b": False, "c": False}))
    assert alone.rank > common.rank


def test_missing_what_nobody_offers_is_still_a_weak_weakness():
    """The other side of the same axis — prevalence still governs when the subject is behind."""
    p = score_gap(_gap(verdict="behind", subject=False,
                       peers={"a": False, "b": False, "c": False}))
    assert p.importance == "low"


def test_behind_on_something_nobody_cares_about_outranks_nothing():
    strong = score_gap(_gap(verdict="behind", subject=False,
                            peers={"a": True, "b": True, "c": True}))
    weak = score_gap(_gap(verdict="behind", subject=False,
                          peers={"a": False, "b": False, "c": True}))
    assert strong.rank > weak.rank, "a flat list is exactly what the paper calls inadequate"


# --- impact is the SIZE of the gap, measured -------------------------------------------------

def test_a_wide_count_gap_has_more_impact_than_a_narrow_one():
    wide = score_gap(_gap(verdict="behind", subject=1, kind="count",
                          peers={"a": 20, "b": 22, "c": 18}))
    narrow = score_gap(_gap(verdict="behind", subject=9, kind="count",
                            peers={"a": 10, "b": 11, "c": 10}))
    assert wide.rank > narrow.rank
    assert wide.impact == "high"


# --- the brand's own marketing copy is an opinion, not a measured strength ---------------------

def test_the_brands_own_claim_can_never_be_top_priority():
    """`internally_supported` means: the subject said it about itself. Paper: opinions, not fact."""
    p = score_opinion_item(claim_strength="internally_supported")
    assert p.importance in ("low", "unknown")
    assert p.focus is False
    assert p.rank < 0.5


def test_an_externally_validated_claim_outranks_a_self_claim():
    own = score_opinion_item(claim_strength="internally_supported")
    checked = score_opinion_item(claim_strength="validated")
    assert checked.rank > own.rank


def test_the_self_claim_basis_names_the_problem_in_the_owners_language():
    p = score_opinion_item(claim_strength="internally_supported")
    assert any("؀" <= ch <= "ۿ" for ch in p.basis), p.basis


# --- Kotler's rule: a factor low on both axes leaves the analysis ------------------------------

def test_a_factor_low_on_both_axes_is_marked_for_removal():
    """CORRECTED while writing this file: the first fixture used peers {a:1, b:0, c:1}, which is
    2-of-3 prevalence — HIGH importance, i.e. the opposite of the case under test. The scorer was
    right and the fixture was wrong. A genuinely low-low factor: almost no peer has it, and the
    subject is not behind on it either."""
    p = score_gap(_gap(verdict="par", subject=1, kind="count",
                       peers={"a": 1, "b": 0, "c": 0, "d": 0}))
    assert p.importance == "low" and p.impact in ("low", "unknown")
    assert p.drop is True, "the paper: such factors 'could even be removed entirely'"


def test_a_factor_high_on_either_axis_is_never_dropped():
    for g in (_gap(verdict="behind", subject=False, peers={"a": True, "b": True, "c": True}),
              _gap(verdict="behind", subject=0, kind="count", peers={"a": 30, "b": 28, "c": 31})):
        assert score_gap(g).drop is False


# --- shape ------------------------------------------------------------------------------------

def test_the_score_is_a_priority_with_every_axis_present():
    p = score_gap(_gap(verdict="behind", subject=False, peers={"a": True}))
    assert isinstance(p, Priority)
    for axis in ("importance", "performance", "impact", "probability"):
        assert getattr(p, axis), axis
    assert 0.0 <= p.rank <= 1.0
