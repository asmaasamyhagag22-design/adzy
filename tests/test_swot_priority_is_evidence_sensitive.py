# -*- coding: utf-8 -*-
"""The priority score must not reward thin evidence, delete real superiority, or fake an axis.

Three defects MEASURED on the shipped scorer, all of them the same failure the project has already
paid for once — a number that comes out of an empty pool and is then presented as confidence:

  1. SAMPLE SIZE INVERTED THE RANKING. One peer who has the thing scored `rank=1.00 importance=high
     focus=True`; three peers, one of whom has it, scored `rank=0.29 importance=low focus=False`.
     Weaker evidence produced a LOUDER verdict. This is `score_size_similarity` all over again —
     it returned 1.0 against a single candidate because the "peer median" was the subject's own
     review count, and the project's own fix was to return None rather than convert emptiness into
     certainty.

  2. EVERY NUMERIC SUPERIORITY WAS DELETED. Subject 50 against peers {1, 2} came back
     `importance=low impact=low drop=True` — removed from the SWOT by Kotler's removal rule — with
     the written basis "كل المنافسين المقيسين (2) عندهم Trust أيضاً". A true sentence answering a
     different question: the client asked "am I ahead?", the sentence answers "do they have any?".
     The cause is `_is_present`, which flattens a count to a boolean, so a peer with 1 signal and a
     subject with 50 are indistinguishable on the rarity axis.

  3. A `whitespace` OPPORTUNITY WAS ALWAYS DROPPED. `whitespace` means nobody in the measured set
     offers it — so prevalence is 0 (importance low) and the peer median is 0 (impact low), and
     the removal rule fires on every single one. Kotler's opportunity matrix, which is the reason
     this module exists, could never fire on a measured opportunity.

  4. `probability` WAS `importance` BY ASSIGNMENT. Two axes were reported to the client; one was
     measured. A declared axis that is a copy of another is worse than an absent one.

Hermetic: pure scoring, no network, no model. Universal (D-8): the fixtures name no country,
vertical or brand — only the generic dimension shape the scorer keys off.
"""
from __future__ import annotations

from competitor.matrix import Dimension, DimensionGap
from competitor.swot_priority import score_gap


def _gap(*, kind="bool", subject, peers, verdict, key="dim", label="Dim"):
    return DimensionGap(
        dimension=Dimension(key, label, "scraped", kind, "higher_better"),
        subject_value=subject,
        competitor_values=dict(peers),
        verdict=verdict,
        detail="",
    )


# --- 1) thin evidence must not outrank thick evidence -------------------------------------------

def test_a_single_peer_scores_lower_than_the_same_gap_against_three():
    """MEASURED before the fix: 1 peer -> rank 1.00 / high / focus=True, while 3 peers (1 of whom
    has it) -> rank 0.29 / low. The rank must move the other way: more evidence, more confidence."""
    thin = score_gap(_gap(subject=False, peers={"A": True}, verdict="behind"))
    thick = score_gap(_gap(subject=False, peers={"A": True, "B": True, "C": True},
                           verdict="behind"))
    assert thin.rank < thick.rank, (
        f"one peer scored {thin.rank} against three peers' {thick.rank} — "
        "weaker evidence produced a louder verdict")


def test_the_thin_basis_says_out_loud_that_the_pool_was_tiny():
    thin = score_gap(_gap(subject=False, peers={"A": True}, verdict="behind"))
    assert thin.peers_measured == 1
    assert "1" in thin.basis


def test_one_peer_alone_cannot_declare_a_dimension_table_stakes():
    """`focus` is the paper's "pay particular attention" flag. One observation cannot earn it."""
    thin = score_gap(_gap(subject=False, peers={"A": True}, verdict="behind"))
    assert thin.focus is False, "a single peer promoted a gap to top priority"


def test_three_peers_who_all_have_it_still_reach_focus():
    """The fix must dampen thin evidence WITHOUT muting real evidence."""
    thick = score_gap(_gap(subject=False, peers={"A": True, "B": True, "C": True},
                           verdict="behind"))
    assert thick.importance == "high"
    assert thick.focus is True


# --- 2) being far ahead on a COUNT is an advantage, not a deletion ------------------------------

def test_a_large_numeric_lead_is_not_deleted_by_the_removal_rule():
    """MEASURED before the fix: subject 50 vs peers {1, 2} -> drop=True. A 25x lead removed."""
    p = score_gap(_gap(kind="count", subject=50, peers={"A": 1, "B": 2}, verdict="ahead"))
    assert p.drop is False, f"a 25x lead was dropped: {p.basis}"
    assert p.importance in ("high", "medium"), p.importance


def test_the_written_basis_answers_the_question_that_was_asked():
    """"All measured competitors have Trust too" is true and irrelevant when the subject has 50
    and they have 1. The basis must compare the magnitudes it was asked about."""
    p = score_gap(_gap(kind="count", subject=50, peers={"A": 1, "B": 2}, verdict="ahead",
                       label="Trust"))
    assert "50" in p.basis, p.basis


def test_standing_level_on_a_count_everybody_matches_is_not_an_advantage():
    """The magnitude path must not turn every count into a strength — parity is parity.

    FIXTURE CORRECTED, with the reason: the first version of this test also asserted
    `importance in (low, medium)`, and that assertion was wrong. On the Performance-Importance
    matrix, a dimension every measured peer maintains IS high-importance — that is what table
    stakes means. What must not happen is that parity reads as an advantage, so the assertions
    that matter are `focus` (nothing to attend to) and `impact` (no distance to anything)."""
    p = score_gap(_gap(kind="count", subject=3, peers={"A": 3, "B": 3, "C": 3}, verdict="par"))
    assert p.focus is False
    assert p.impact == "low"
    assert p.performance == "parity"


def test_a_boolean_advantage_still_scores_by_rarity():
    """The bool path is the one an existing test already locked; it must not regress."""
    p = score_gap(_gap(subject=True, peers={"A": False, "B": False, "C": False}, verdict="ahead"))
    assert p.importance == "high"
    assert p.drop is False


# --- 3) whitespace is an opportunity by definition ----------------------------------------------

def test_a_whitespace_opportunity_survives_the_removal_rule():
    """`whitespace` = nobody measured offers it. Deleting it deletes the only kind of opportunity
    the matrix can actually measure."""
    p = score_gap(_gap(subject=False, peers={"A": False, "B": False, "C": False},
                       verdict="whitespace"))
    assert p.drop is False, f"every measured opportunity was being removed: {p.basis}"


def test_whitespace_says_why_it_is_an_opportunity():
    p = score_gap(_gap(subject=False, peers={"A": False, "B": False, "C": False},
                       verdict="whitespace", label="Loyalty programme"))
    assert p.basis.strip(), "a whitespace opportunity shipped with no written basis"


# --- 4) no axis may be a copy of another --------------------------------------------------------

def test_probability_is_not_a_silent_duplicate_of_importance():
    """Two axes were shown to the client and one was measured. Either probability is derived from
    its own evidence or it stays unknown — a copy dressed as a second opinion is not allowed."""
    gaps = [
        _gap(subject=False, peers={"A": True, "B": True, "C": True}, verdict="behind"),
        _gap(kind="count", subject=50, peers={"A": 1, "B": 2}, verdict="ahead"),
        _gap(subject=False, peers={"A": False, "B": False}, verdict="whitespace"),
    ]
    scored = [score_gap(g) for g in gaps]
    assert not all(p.probability == p.importance for p in scored), (
        "probability was importance on every factor — a declared axis that measures nothing")


# --- the no-peer honesty that already worked must keep working ----------------------------------

def test_no_peers_stays_unknown_and_is_never_dropped():
    p = score_gap(_gap(subject=True, peers={}, verdict="n/a"))
    assert p.importance == "unknown"
    assert p.drop is False
    assert p.focus is False
    assert p.peers_measured == 0
