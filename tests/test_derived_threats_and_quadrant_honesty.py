# -*- coding: utf-8 -*-
"""An empty Threats quadrant must be filled from real evidence — or explain itself.

MEASURED (2026-07-21): Volume Qatar shipped `threats: []`. All four generators need evidence it
did not have — no scrapable peer (so the whole scraped-dimension matrix returns "n/a"), a Places
rating band of 0.645 that nothing clears, no peer complaints, no ads. And the five Qatar market
signals it DID have all became Opportunities, because `_TREND_THREAT_KEYS` is a 16-word ENGLISH
list ("ban", "shortage", "lawsuit"…) that "Qatar Catering Services Market Size" cannot match.

Worse, the honest note that would have explained the gap never fired: `swot.py:190` computes
`competitive_empty` across the WHOLE swot, so one incidental review-count strength silenced it.

Owner's ruling (2026-07-21): derive threats from weaker evidence AND label each one. The label
reuses the existing epistemic ladder — `claim_strength="directional_not_validated"` — rather than a
new field, so every surface that already renders claim strength keeps working. Hermetic."""
from __future__ import annotations

from competitor.swot import SWOT, SWOTItem, disclose_empty_quadrants, derived_threats


class _Trend:
    def __init__(self, title, url="https://x.example/a"):
        self.title = title
        self.url = url


def _weakness(text):
    return SWOTItem(text=text, citation=["own-site"], evidence="cta_count 3 vs 17",
                    claim_strength="directional_not_validated")


# ---- derived threats ------------------------------------------------------------------

def test_a_growing_category_is_a_competitive_threat():
    out = derived_threats(trends=[_Trend("Qatar Catering Services Market Size & Forecast 2026")],
                          weaknesses=[], maturity=None)
    assert out, "a market-growth signal implies new entrants"
    t = out[0]
    assert t.claim_strength == "directional_not_validated"
    assert "Qatar Catering Services Market Size" in " ".join(t.citation + [t.evidence])


def test_every_derived_threat_says_it_is_inferred():
    out = derived_threats(trends=[_Trend("Coffee shop boom in Doha")], weaknesses=[],
                          maturity=None)
    assert all("استنتاج" in t.text or "إشارة سوق" in t.text for t in out), [t.text for t in out]


def test_an_own_site_weakness_is_never_re_labelled_as_a_threat():
    """CORRECTED after the suite caught it: the first version of this test asserted the opposite —
    that a weakness becomes a threat. Implementing that broke the pre-existing
    `test_scraped_behind_standalone_emits_no_phantom_threat`, which was RIGHT: in standalone mode
    it turned "No online booking path on-site" into a threat.

    A threat is EXTERNAL and a weakness is INTERNAL — that is the framework, not a preference.
    Re-labelling your own gap as a threat is double-counting dressed as insight, and it inflates
    a quadrant with things the client already read one box to the left."""
    out = derived_threats(trends=[], weaknesses=[_weakness("Fewer CTAs than peers")],
                          maturity=None)
    assert out == []


def test_a_nascent_brand_carries_an_awareness_threat():
    class _M:
        level = "nascent"
        disclosure = "براند ناشئ — يحتاج بناء وعي"
    out = derived_threats(trends=[], weaknesses=[], maturity=_M())
    assert any("وعي" in t.text or "ناشئ" in t.text for t in out)


def test_no_evidence_means_no_invented_threat():
    assert derived_threats(trends=[], weaknesses=[], maturity=None) == []


# ---- per-quadrant disclosure ----------------------------------------------------------

def test_an_empty_quadrant_gets_its_own_note_even_when_others_are_full():
    swot = SWOT(strengths=[_weakness("Your review volume is above the peer average")],
                weaknesses=[], opportunities=[], threats=[])
    disclose_empty_quadrants(swot, n_peers=1, scrapable_peers=0)
    joined = " ".join(swot.notes)
    assert "Threats" in joined or "التهديدات" in joined
    assert "0" in joined or "قابل للفحص" in joined      # names WHAT was missing


def test_a_full_swot_gets_no_noise():
    swot = SWOT(strengths=[_weakness("a")], weaknesses=[_weakness("b")],
                opportunities=[_weakness("c")], threats=[_weakness("d")])
    disclose_empty_quadrants(swot, n_peers=4, scrapable_peers=4)
    assert swot.notes == []
