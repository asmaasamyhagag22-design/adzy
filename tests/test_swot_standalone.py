"""Standalone SWOT degrade — the subject's own dimensions still produce a cited, non-empty read.

When discovery finds 0 peers (or the comparison is too thin to clear the evidence bar),
synthesize_swot falls back to a profile-only analysis built from the subject's OWN scraped
dimensions. Grounded: every item is cited and UNKNOWN (None) cells are skipped. Competitive mode
is left unchanged.

UPDATED 2026-07-25, on the owner's ruling after Pickton & Wright (1998). These items are website
mechanics — "Active social presence (1 channels)", "No online booking path". Stated with no
comparison they are not strengths or weaknesses at all: "strengths and weaknesses are only such in
comparison to the competition ... relative and not absolute" (p.103). They now ship in
`swot.site_readiness` instead of the strategic quadrants, and the SWOT says out loud that a
competitor is needed to turn them into analysis.

Every assertion below keeps its ORIGINAL intent — never empty, always cited, UNKNOWN cells skipped,
deduped counts — and simply follows the items to where they now live. The one thing that changed is
which box they sit in, which is exactly what the ruling changed.
"""
from __future__ import annotations

from competitor.matrix import (
    ComparativeGapMatrix, MatrixColumn, DimensionGap, Dimension,
)
from competitor.swot import synthesize_swot


def _subject():
    # real thehairaddict deduped dims; shows_reviews/trust_count are UNKNOWN
    return MatrixColumn(name="You", is_subject=True, values={
        "online_booking": False, "whatsapp": True, "shows_reviews": None,
        "cta_count": 2, "offerings_count": 10, "bilingual": False,
        "trust_count": None, "social_count": 1,
    })


def test_zero_peers_degrades_to_standalone():
    swot = synthesize_swot(ComparativeGapMatrix(columns=[_subject()], gaps=[]), themes=[])
    assert swot.mode == "standalone"
    # strategist phrasing (Slice 2) survives — now in the site-readiness section, not the quadrants
    assert any("WhatsApp" in s.text for s in swot.site_readiness)
    assert any("booking" in w.text.lower() for w in swot.site_readiness)
    assert any(n.startswith("Standalone Strategic Analysis (No Competitors") for n in swot.notes)


def test_an_empty_strategic_swot_names_its_cause_and_the_fix():
    """The correct answer to "no comparison" is an empty strategic SWOT — delivered as a blank
    page it reads like a broken product, so it must say why and what to do."""
    swot = synthesize_swot(ComparativeGapMatrix(columns=[_subject()], gaps=[]), themes=[])
    assert not swot.strengths and not swot.weaknesses
    assert any("أضيفي منافساً" in n for n in swot.notes), swot.notes


def test_standalone_is_never_empty_and_always_cited():
    swot = synthesize_swot(ComparativeGapMatrix(columns=[_subject()], gaps=[]), themes=[])
    assert swot.site_readiness                        # never empty — the client always gets a read
    for item in swot.site_readiness:
        assert item.citation                          # every item traceable


def test_standalone_skips_unknown_cells_and_uses_deduped_counts():
    swot = synthesize_swot(ComparativeGapMatrix(columns=[_subject()], gaps=[]), themes=[])
    cited_keys = {c for it in swot.site_readiness for c in it.citation}
    assert "shows_reviews" not in cited_keys          # UNKNOWN -> excluded (rule #4)
    assert "trust_count" not in cited_keys
    # deduped count (1), not inflated — now in strategist phrasing (Slice 2)
    assert any("social presence (1 channels)" in s.text for s in swot.site_readiness)


def test_competitive_mode_unchanged_when_gaps_exist():
    subj = _subject()
    peer = MatrixColumn(name="Peer", is_subject=False, values={"whatsapp": False})
    gap = DimensionGap(
        Dimension("whatsapp", "WhatsApp contact", "scraped", "bool", "higher_better"),
        subject_value=True, competitor_values={"Peer": False},
        verdict="ahead", detail="You have WhatsApp; Peer does not",
    )
    swot = synthesize_swot(ComparativeGapMatrix(columns=[subj, peer], gaps=[gap]), themes=[])
    assert swot.mode == "competitive"
    assert len(swot.strengths) == 1
    assert swot.strengths[0].text == "You have WhatsApp; Peer does not"


def test_scraped_behind_with_peers_yields_weakness_and_threat():
    # A web/SERP peer outperforming you on a SITE dimension is both an internal
    # weakness AND an external threat — this is what fills Threats for ECOMMERCE.
    subj = _subject()
    peer = MatrixColumn(name="Rival", is_subject=False, values={"whatsapp": True})
    gap = DimensionGap(
        Dimension("whatsapp", "WhatsApp contact", "scraped", "bool", "higher_better"),
        subject_value=False, competitor_values={"Rival": True},
        verdict="behind", detail="Rival has WhatsApp; you do not",
    )
    swot = synthesize_swot(ComparativeGapMatrix(columns=[subj, peer], gaps=[gap]), themes=[])
    assert swot.mode == "competitive"
    assert len(swot.weaknesses) == 1                          # internal lens kept
    assert len(swot.threats) == 1                             # NEW external lens
    assert "WhatsApp contact" in swot.threats[0].text
    assert any("Rival" in c for c in swot.threats[0].citation)  # grounded in the peer


def test_scraped_behind_standalone_emits_no_phantom_threat():
    # 0 peers -> standalone -> the competitive threat must NOT fire.
    subj = _subject()
    swot = synthesize_swot(ComparativeGapMatrix(columns=[subj], gaps=[]), themes=[])
    assert swot.mode == "standalone"
    assert swot.threats == []


def test_unique_insights_become_grounded_strengths():
    # profile.other_unique_insights -> cited strengths, appended AFTER the standalone
    # degrade so they don't suppress the 0-peer fallback.
    swot = synthesize_swot(
        ComparativeGapMatrix(columns=[_subject()], gaps=[]), themes=[],
        unique_insights=["The only ISO-certified lab in Cairo", "", "The only ISO-certified lab in Cairo"],
    )
    edges = [s for s in swot.strengths if s.text == "The only ISO-certified lab in Cairo"]
    assert len(edges) == 1                       # added once (deduped), empty skipped
    assert edges[0].citation == ["your profile"]
    assert swot.mode == "standalone"             # 0-peer fallback still fired
