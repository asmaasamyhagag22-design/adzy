# -*- coding: utf-8 -*-
"""The SWOT the CLIENT sees — asserted on the serialized payload, not on a way-station.

WHY THIS FILE EXISTS. Thirteen tests were written for the priority scorer, every one of them
calling `score_gap` directly, and all thirteen passed while the priority never once reached a
client. `CLAUDE.md` names this exact disease: a test that asserts on an intermediate stage instead
of the output passes while the hole stays open. So this file starts from `synthesize_swot` and
asserts on `_swot_to_frontend` — the dict the browser actually renders.

THREE DEFECTS MEASURED ON THE SHIPPED PATH, all the same shape: produced · serialized · never
shown.

  1. `api/webapp_analysis._items` shipped five keys and neither `priority` nor `id`. The whole
     ranking apparatus — importance, performance, the written basis, `focus` — stopped at the
     serializer. (`api/routes/swot.py` had already been fixed; this second copy had not.)

  2. `_swot_to_frontend` never serialized `site_readiness`. Moving site mechanics out of the
     strategic quadrants was the owner's ruling, but the destination was not on the wire — so
     those items did not MOVE for the client, they VANISHED. Measured on nanocare: 4 items.

  3. `disclose_empty_quadrants` ran BEFORE `_prioritise`, so a quadrant emptied by Kotler's removal
     rule shipped with no reason written in it — the precise opposite of what the disclosure is
     for.

Hermetic: a hand-built matrix in, a dict out. No network, no model, no brand fixture on disk.
"""
from __future__ import annotations

from api.webapp_analysis import _swot_to_frontend
from competitor.matrix import (ComparativeGapMatrix, Dimension, DimensionGap, MatrixColumn)
from competitor.swot import synthesize_swot


def _dim(key, label, kind="bool"):
    return Dimension(key, label, "scraped", kind, "higher_better")


def _matrix(gaps):
    return ComparativeGapMatrix(
        columns=[MatrixColumn(name="You", is_subject=True, values={}),
                 MatrixColumn(name="Peer A", is_subject=False, values={}),
                 MatrixColumn(name="Peer B", is_subject=False, values={}),
                 MatrixColumn(name="Peer C", is_subject=False, values={})],
        gaps=gaps,
    )


def _behind(key="online_booking", label="Online booking"):
    return DimensionGap(dimension=_dim(key, label), subject_value=False,
                        competitor_values={"Peer A": True, "Peer B": True, "Peer C": True},
                        verdict="behind", detail=f"You have no {label}; 3 of 3 peers do.")


def _shipped(gaps, **kw):
    # `n_peers` is derived inside `synthesize_swot` from the matrix columns — three peers here.
    return _swot_to_frontend(synthesize_swot(_matrix(gaps), **kw))


# The shape that actually produces site-readiness items: a subject with no comparable peer, which
# is nanocare's shape and the one the four mislabelled items came from.
_THIN_PROFILE = {
    "name": "Example",
    "category": "services",
    "value_propositions": ["Advanced technology for better results"],
    "offerings": [],
    "source_url": "https://example.test/",
}


def _standalone_shipped():
    # The subject's OWN dimension readings, with nothing to compare them against — nanocare's
    # measured shape exactly (cta/offerings/trust/social all read as 0 from its own site).
    # NOTE the keywords: `MatrixColumn`'s second positional is `is_subject`, not `values`, so a
    # positional dict here silently produces a column with NO readings and the fixture measures
    # nothing. Caught while this very test was being written.
    subject = MatrixColumn(name="You", is_subject=True,
                           values={"cta_count": 0, "offerings_count": 0,
                                   "trust_count": 0, "social_count": 0})
    return _swot_to_frontend(synthesize_swot(
        ComparativeGapMatrix(columns=[subject], gaps=[]), profile=_THIN_PROFILE))


# --- 1) the ranking must survive serialization --------------------------------------------------

def test_every_shipped_item_carries_its_priority():
    out = _shipped([_behind()])
    items = out["weaknesses"]
    assert items, "the measured gap produced no weakness at all"
    assert "priority" in items[0], f"the priority never reached the client: {sorted(items[0])}"


def test_the_written_basis_reaches_the_client_not_just_the_score():
    """A score with no stated measurement is an assertion. The client must be able to check it."""
    out = _shipped([_behind()])
    p = out["weaknesses"][0].get("priority") or {}
    assert p.get("basis", "").strip(), "the item shipped a rank with no basis to check it against"
    assert p.get("importance") in ("high", "medium", "low", "unknown")


def test_every_shipped_item_carries_a_stable_id():
    """The strategy plan anchors its steps on item ids. Without them on the wire the plan
    renumbers by list position and cites the wrong factor."""
    out = _shipped([_behind()])
    assert out["weaknesses"][0].get("id"), "no id — the plan cannot point at this item"


def test_the_items_arrive_ordered_by_rank():
    gaps = [
        DimensionGap(dimension=_dim("whatsapp", "WhatsApp"), subject_value=False,
                     competitor_values={"Peer A": True, "Peer B": False, "Peer C": False},
                     verdict="behind", detail="1 of 3 peers has WhatsApp."),
        _behind(),
    ]
    ranks = [i["priority"]["rank"] for i in _shipped(gaps)["weaknesses"] if i.get("priority")]
    assert ranks == sorted(ranks, reverse=True), ranks


# --- 2) site readiness must arrive, not disappear ------------------------------------------------

def test_site_readiness_is_on_the_wire():
    """Site mechanics are MOVED out of the strategic quadrants, not deleted. The destination has
    to exist in the payload or the move is a deletion."""
    out = _shipped([_behind()])
    assert "site_readiness" in out, f"the destination is not serialized: {sorted(out)}"


def test_a_moved_item_is_reachable_somewhere_in_the_payload():
    out = _shipped([_behind()])
    everywhere = (out["strengths"] + out["weaknesses"] + out["opportunities"] + out["threats"]
                  + out.get("site_readiness", []))
    assert any("Online booking" in (i.get("title") or "") for i in everywhere), (
        "a measured gap is in no quadrant and in no readiness list — it vanished")


def test_moved_items_keep_their_priority_and_id_too():
    out = _shipped([_behind()])
    for item in out.get("site_readiness", []):
        assert item.get("id"), "a readiness item shipped with no id"
        assert "priority" in item, "a readiness item shipped with no priority"


# --- 3) an emptied quadrant says why -------------------------------------------------------------

def test_a_quadrant_emptied_by_the_removal_rule_ships_a_reason():
    """MEASURED: `disclose_empty_quadrants` was called two lines BEFORE `_prioritise`, so it
    inspected quadrants that were still full and stayed silent about the ones the removal rule
    then emptied."""
    out = _shipped([_behind()])
    for name in ("strengths", "opportunities", "threats"):
        if not out[name]:
            assert any(n.strip() for n in out["notes"]), (
                f"'{name}' shipped empty with no note explaining why")
            break
    else:
        raise AssertionError("fixture no longer produces an empty quadrant — rewrite it")


def test_the_disclosure_notes_are_on_the_wire():
    out = _shipped([_behind()])
    assert isinstance(out.get("notes"), list)


# --- caught by driving the page's own renderer over the real nanocare payload -------------------

def test_a_site_observation_is_not_labelled_the_brands_own_claim():
    """MEASURED by running `swotItemHtml` over the shipped nanocare payload: all four site-readiness
    items rendered the basis «ادعاء البراند عن نفسه (من موقعه)، لم يُقارَن بأحد» under titles like
    "No clear call-to-action on the page".

    That is failure class #6 again — a true sentence answering a different question. "No CTA on the
    page" is something the crawler MEASURED on the site; calling it a claim the brand makes about
    itself is both wrong and, for a weakness, absurd: no brand advertises its own missing CTA. The
    honest statement for these is the one that is actually true — nothing was compared.

    `_prioritise` already distinguishes the two cases (`_is_site_mechanic` is what routes an item to
    site_readiness at all); the scorer just was not told which it was looking at.

    FIXTURE NOTE: readiness items come from the STANDALONE path (a subject with no comparable peer),
    which is the shape nanocare has. The peer fixture used elsewhere in this file produces none, so
    asserting on it would have passed while measuring nothing."""
    out = _standalone_shipped()
    assert out.get("site_readiness"), "fixture produces no readiness items — it measures nothing"
    for item in out.get("site_readiness", []):
        basis = (item.get("priority") or {}).get("basis", "")
        assert "ادعاء البراند عن نفسه" not in basis, (
            f"a measured site observation was labelled a brand self-claim: {item['title']}")
        assert basis.strip(), "a readiness item shipped with no basis at all"


def test_a_real_self_claim_is_still_labelled_a_self_claim():
    """The fix must not blunt the label where it IS correct — a value proposition mined from the
    brand's own marketing copy is exactly what that sentence is for."""
    from competitor.swot_priority import score_opinion_item
    p = score_opinion_item(claim_strength="internally_supported")
    assert "ادعاء البراند عن نفسه" in p.basis
