# -*- coding: utf-8 -*-
"""A pool of one must not hand out a perfect size score.

MEASURED (Volume Qatar, 2026-07-21): the single surviving candidate — LEE-TOP Catering Services,
not a café — scored 0.73 against a 0.45 floor, and the arithmetic shows why:

    sub_vertical    0.4933   (matched only on the generic Places type "food")
    proximity       None     (weight redistributed to the rest)
    size_similarity 1.0      <- the pool median WAS its own 11 reviews
    language        1.0      (free)

`score_size_similarity` compares the candidate to the subject's own review count, else to the pool
median. `crit.review_count_self` is read from `review_count`, a field that does not exist on
`BusinessProfile` (the same phantom-field class as the address bug), so the median is the ONLY path
that ever runs — and a median over one candidate is that candidate. The emptier the pool, the
higher the survivor scores.

A number derived from nothing must disappear (None → weight redistributed), not become confidence."""
from __future__ import annotations

from competitor.peer_match import SIZE_NEUTRAL, score_size_similarity


class _Cand:
    def __init__(self, reviews):
        self.review_count = reviews


class _Crit:
    def __init__(self, own=None):
        self.review_count_self = own


def test_a_median_over_one_candidate_is_not_evidence():
    # the classic self-reference: median == the candidate's own count
    assert score_size_similarity(_Cand(11), _Crit(), pool_median_reviews=11, pool_size=1) is None


def test_a_real_pool_still_scores():
    s = score_size_similarity(_Cand(100), _Crit(), pool_median_reviews=100, pool_size=5)
    assert s == 1.0                                  # genuinely the same size as its market


def test_the_subjects_own_count_beats_any_pool_size():
    # when we know the SUBJECT's reviews, the comparison is real however small the pool
    assert score_size_similarity(_Cand(50), _Crit(own=50), pool_median_reviews=None,
                                 pool_size=1) == 1.0


def test_divergent_sizes_still_decay():
    s = score_size_similarity(_Cand(5), _Crit(own=5000), pool_median_reviews=None, pool_size=1)
    assert 0.0 < s < 0.3


def test_missing_counts_stay_neutral_not_none():
    # no reviews at all is "unknown", the pre-existing neutral — unchanged behaviour
    assert score_size_similarity(_Cand(0), _Crit(own=10), pool_median_reviews=None,
                                 pool_size=5) == SIZE_NEUTRAL
