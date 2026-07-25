# -*- coding: utf-8 -*-
"""Social presence as a COMPARISON, not a description.

The owner named the SWOT's evidence sources: Google Places reviews, social media, competitors. Two
of the three were connected. Social — measured as the widest of them (82% of stored profiles carry
`social_presence`, against 29% with any Places review) — reached the SWOT ZERO times.

The shape here is deliberately not a new generator with new judgement. A platform either side is on
is exactly what `competitor/matrix.DimensionGap` already models, so each platform becomes one gap
and `swot_priority.score_gap` scores it with the arithmetic already in place:

  * behind on a platform most peers use   -> high importance, `focus`  ("they're on TikTok, you're not")
  * ahead on one almost nobody uses       -> a real differentiator (rarity, not prevalence)
  * low on both axes                      -> dropped by Kotler's rule

TWO MEASURED TRAPS this file also guards:
  1. The Evidence Ledger DROPS a line carrying an unsourced 2+ digit number or a superlative —
     `"Active social presence (17 channels)"` and `"Leading social presence"` both fail it. Numbers
     belong in `evidence`, never in the client-facing text.
  2. `swot._is_site_mechanic` moves any item citing a scraped dimension key out of the strategic
     quadrants. A social item must therefore be anchored to a real gap, which is what this produces.

Hermetic."""
from __future__ import annotations

from competitor.matrix import ComparativeGapMatrix, MatrixColumn
from competitor.social_gaps import social_gaps
from competitor.swot_priority import score_gap


def _matrix(subject_platforms, peer_platforms: dict):
    subject = MatrixColumn(name="You", is_subject=True,
                           values={"social_platforms": subject_platforms})
    peers = [MatrixColumn(name=n, is_subject=False, values={"social_platforms": p})
             for n, p in peer_platforms.items()]
    return ComparativeGapMatrix(columns=[subject, *peers], gaps=[])


def _profile(platforms):
    return {"name": {"value": "X"},
            "social_presence": [{"platform": p, "url": f"https://{p}.com/x"} for p in platforms]}


def _by_key(gaps):
    return {g.dimension.key: g for g in gaps}


# --- the gap the owner asked for ---------------------------------------------------------------

def test_a_platform_the_peers_use_and_the_brand_does_not_becomes_a_behind_gap():
    gaps = social_gaps(_profile(["facebook", "instagram"]),
                       _matrix(["facebook", "instagram"],
                               {"A": ["facebook", "tiktok"], "B": ["instagram", "tiktok"],
                                "C": ["tiktok"]}))
    tiktok = _by_key(gaps)["social_tiktok"]
    assert tiktok.subject_value is False
    assert tiktok.verdict == "behind"
    assert set(tiktok.competitor_values.values()) == {True}


def test_that_gap_scores_as_a_high_priority_weakness():
    """The whole point: the existing scorer turns it into 'attend to this'."""
    gaps = social_gaps(_profile(["facebook"]),
                       _matrix(["facebook"], {"A": ["tiktok"], "B": ["tiktok"], "C": ["tiktok"]}))
    p = score_gap(_by_key(gaps)["social_tiktok"])
    assert p.importance == "high" and p.performance == "behind" and p.focus is True
    assert "3" in p.basis, p.basis


def test_a_platform_only_the_brand_uses_is_a_differentiator_not_a_weakness():
    gaps = social_gaps(_profile(["facebook", "pinterest"]),
                       _matrix(["facebook", "pinterest"], {"A": ["facebook"], "B": ["facebook"]}))
    p = score_gap(_by_key(gaps)["social_pinterest"])
    assert p.performance == "ahead"
    assert p.importance == "high", "being the only one there is the definition of an advantage"


def test_a_platform_everyone_including_the_brand_uses_is_not_a_finding():
    gaps = social_gaps(_profile(["facebook"]),
                       _matrix(["facebook"], {"A": ["facebook"], "B": ["facebook"]}))
    p = score_gap(_by_key(gaps)["social_facebook"])
    assert p.performance in ("parity", "ahead")
    assert p.focus is False


# --- honesty --------------------------------------------------------------------------------

def test_a_peer_that_could_not_be_read_is_not_counted_as_absent():
    """A JS-only peer returns UNKNOWN dims. Reading that as 'they have no TikTok' would invent a
    gap out of a failed fetch — the same class as reporting a false zero."""
    m = _matrix(["facebook"], {"A": ["tiktok"]})
    m.columns.append(MatrixColumn(name="Unreadable", is_subject=False, values={}))
    gap = _by_key(social_gaps(_profile(["facebook"]), m))["social_tiktok"]
    assert "Unreadable" not in gap.competitor_values
    assert gap.competitor_values == {"A": True}


def test_no_peers_measured_produces_no_gaps_at_all():
    """With nothing to compare against there is no strength or weakness — the paper's rule."""
    assert social_gaps(_profile(["facebook", "tiktok"]), _matrix(["facebook", "tiktok"], {})) == []


def test_a_brand_with_no_socials_still_sees_what_the_market_uses():
    gaps = social_gaps(_profile([]), _matrix([], {"A": ["instagram"], "B": ["instagram"]}))
    ig = _by_key(gaps)["social_instagram"]
    assert ig.subject_value is False and ig.verdict == "behind"


def test_the_detail_carries_no_bare_number_that_the_ledger_would_drop():
    """MEASURED: the Ledger rejects an unsourced 2+ digit number or a superlative."""
    gaps = social_gaps(_profile(["facebook"]),
                       _matrix(["facebook"], {chr(65 + i): ["tiktok"] for i in range(12)}))
    detail = _by_key(gaps)["social_tiktok"].detail
    import re
    assert not re.search(r"\d{2,}", detail), f"a 2+ digit number would be dropped: {detail!r}"
    for banned in ("leading", "best", "first", "top"):
        assert banned not in detail.lower(), detail


def test_the_platform_name_appears_in_the_detail_so_it_is_actionable():
    gaps = social_gaps(_profile(["facebook"]), _matrix(["facebook"], {"A": ["tiktok"]}))
    assert "tiktok" in _by_key(gaps)["social_tiktok"].detail.lower()


def test_an_unknown_platform_is_carried_through_untouched():
    """D-8: platforms are discovered from the data, never from a hardcoded list."""
    gaps = social_gaps(_profile([]), _matrix([], {"A": ["mastodon"], "B": ["mastodon"]}))
    assert "social_mastodon" in _by_key(gaps)
