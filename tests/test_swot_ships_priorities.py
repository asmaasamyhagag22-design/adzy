# -*- coding: utf-8 -*-
"""The priorities must reach the client, and reordering must not silently corrupt TOWS.

Companion to tests/test_swot_is_prioritised.py (which locks the scoring itself). This file locks
the WIRING, because two things stood between a correct score and a client who benefits from it:

  1. `api/routes/swot.py::_items` serialises a hand-written allowlist of exactly four keys, so any
     new field is dropped on the way out — the same "produced, serialised, never shown" failure
     already caught this week in the image path.

  2. `tows.py::swot_item_ids` derives S1/W2/O1/T3 from LIST ORDER. Sorting the quadrants by
     priority — the entire point of the change — would therefore re-point every TOWS strategy and
     priority action at a DIFFERENT factor, silently and with no error. So each item now carries a
     stable `id` assigned at synthesis, and `swot_item_ids` reads it. That removes a latent trap
     for any future reordering, not just this one.

Hermetic."""
from __future__ import annotations

from competitor.matrix import ComparativeGapMatrix, Dimension, DimensionGap, MatrixColumn
from competitor.swot import SWOT, SWOTItem, synthesize_swot
from competitor.tows import swot_item_ids


def _matrix(gaps):
    subject = MatrixColumn(name="Subject", is_subject=True)
    peers = [MatrixColumn(name=n, is_subject=False) for n in ("A", "B", "C")]
    return ComparativeGapMatrix(columns=[subject, *peers], gaps=gaps)


def _gap(key, label, verdict, subject, peers, kind="bool"):
    return DimensionGap(dimension=Dimension(key, label, "scraped", kind, "higher_better"),
                        subject_value=subject, competitor_values=dict(peers),
                        verdict=verdict, detail=f"{label}: {verdict}")


def _swot():
    return synthesize_swot(_matrix([
        # behind on something every peer has -> must outrank everything
        _gap("whatsapp", "WhatsApp", "behind", False, {"A": True, "B": True, "C": True}),
        # behind on something almost nobody has -> low priority
        _gap("online_booking", "Booking", "behind", False, {"A": False, "B": False, "C": True}),
        # ahead on something -> a real, measured strength
        _gap("trust_count", "Trust signals", "ahead", 9, {"A": 2, "B": 1, "C": 2}, kind="count"),
    ]))


# --- the score is attached, and it is the measured one ----------------------------------------

def test_every_item_carries_a_priority():
    swot = _swot()
    for quadrant in (swot.strengths, swot.weaknesses, swot.opportunities, swot.threats):
        for item in quadrant:
            assert getattr(item, "priority", None) is not None, f"unscored item: {item.text}"


def test_the_weakness_everyone_else_covers_ranks_first():
    weaknesses = _swot().weaknesses
    assert weaknesses, "the fixture should produce weaknesses"
    assert "WhatsApp" in weaknesses[0].text or "whatsapp" in weaknesses[0].text.lower(), \
        f"the flat list is back — top weakness is {weaknesses[0].text!r}"


def test_the_top_weakness_is_flagged_as_the_focus():
    top = _swot().weaknesses[0]
    assert top.priority.focus is True
    assert top.priority.basis.strip(), "the client cannot check a score with no stated basis"


# --- reordering must not corrupt the TOWS anchors ----------------------------------------------

def test_ids_follow_the_item_not_its_position():
    swot = _swot()
    before = {k: v.text for k, v in swot_item_ids(swot).items()}
    swot.weaknesses.reverse()
    after = {k: v.text for k, v in swot_item_ids(swot).items()}
    assert before == after, ("ids are positional: reordering re-points every TOWS strategy at a "
                             "different factor, silently")


def test_every_item_has_a_distinct_stable_id():
    swot = _swot()
    ids = [i.id for q in (swot.strengths, swot.weaknesses, swot.opportunities, swot.threats)
           for i in q]
    assert all(ids), "an item without an id falls back to position — the trap this test closes"
    assert len(ids) == len(set(ids)), f"duplicate ids: {ids}"


# --- website mechanics leave the strategic quadrants -------------------------------------------

def test_site_mechanics_without_a_comparison_go_to_their_own_section():
    """The owner's call: keep the information, move it out of the strategy. A standalone SWOT
    (no peers at all) must not present 'Active social presence (3 channels)' as a strength."""
    swot = synthesize_swot(ComparativeGapMatrix(
        columns=[MatrixColumn(name="Subject", is_subject=True)], gaps=[]),
        profile={"name": {"value": "X"}, "source_url": {"value": "https://x.example/"},
                 "contact_channels": {"whatsapp": {"value": ""}},
                 "social_presence": [{"platform": "instagram"}]})
    assert hasattr(swot, "site_readiness"), "no place for the site facts to go"
    joined = " ".join(i.text for i in swot.strengths + swot.weaknesses).lower()
    for site_phrase in ("calls-to-action", "on-site", "channels)"):
        assert site_phrase not in joined, (
            f"a website mechanic is still presented as strategy: {site_phrase!r}")


# --- what was removed is disclosed, never silent ------------------------------------------------

def test_dropped_factors_are_counted_in_the_notes():
    """Kotler's removal rule is applied, but a silent removal reads as 'there was nothing there'.

    CORRECTED TWICE while writing this file, and the second correction is the interesting one.
    (1) The first fixture used verdict="par", which `synthesize_swot` never turns into an item at
    all — it measured the absence of a generator, not the removal rule. (2) The replacement used
    `ahead` where NO peer has the capability — which the rarity fix then correctly scored as HIGH
    importance, because being the only one with something is a differentiator.

    The genuine low-low case for a strength is the opposite: you are ahead on something EVERY
    competitor also offers. True, cited, and worth nothing strategically — exactly what Kotler's
    rule is for."""
    swot = synthesize_swot(_matrix([
        _gap("whatsapp", "WhatsApp", "ahead", True, {"A": True, "B": True, "C": True}),
    ]))
    assert any("استُبعد" in n for n in swot.notes), swot.notes
    assert not swot.strengths, "the low-low factor is still presented as a strength"
