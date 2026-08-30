"""SWOT phrasing (competitor/brand_signals.phrase_dimension + _standalone_from_subject).

Origin (owner): even the mechanical lines read like a raw attribute dump — "Number of CTAs: 3",
"Online booking: not detected on site". Slice 2 re-phrases the SAME scrape-grounded cell into
strategist language ("Clear conversion paths (3 calls-to-action)", "No WhatsApp contact channel")
without touching its citation / evidence / claim_strength. Hermetic: no network, no LLM.
"""
from __future__ import annotations

from competitor.brand_signals import phrase_dimension
from competitor.matrix import build_matrix
from competitor.swot import synthesize_swot


def test_phrase_dimension_is_strategist_language_not_a_raw_dump():
    # counts -> a phrase with the number, never "Number of CTAs: 3"
    assert phrase_dimension("cta_count", "Number of CTAs", "count", 3, True) == "Clear conversion paths (3 calls-to-action)"
    assert phrase_dimension("social_count", "Social links", "count", 20, True) == "Active social presence (20 channels)"
    # absent booleans -> a real weakness sentence, never "WhatsApp: not detected on site"
    assert phrase_dimension("whatsapp", "WhatsApp contact", "bool", False, False) == "No WhatsApp contact channel"
    assert phrase_dimension("bilingual", "Bilingual (AR + EN)", "bool", False, False) == "Single-language site (missing Arabic/English)"
    # unknown dimension -> clean label fallback, still not a "label: value" dump for bools
    assert phrase_dimension("new_dim", "New Thing", "bool", True, True) == "New Thing present on-site"


def _profile():
    return {
        "name": {"value": "Brandy"},
        "existing_ctas": [{"value": "Shop now"}, {"value": "Book a call"}, {"value": "Subscribe"}],
        "social_presence": [{"platform": "instagram", "url": "https://instagram.com/x"},
                            {"platform": "facebook", "url": "https://facebook.com/x"}],
    }


def test_standalone_swot_has_no_raw_attribute_dumps():
    """UPDATED 2026-07-25: own-site items now ship in `swot.site_readiness` rather than the
    strategic quadrants (owner's ruling after Pickton & Wright — an un-compared site fact is not a
    strength or a weakness). The assertion that matters here is unchanged: whatever ships is
    written in strategist phrasing, never as a raw attribute dump."""
    m = build_matrix(_profile(), [])
    sw = synthesize_swot(m)                      # profile=None -> pure mechanical (standalone)
    all_items = sw.strengths + sw.weaknesses + sw.site_readiness
    texts = [i.text for i in all_items]
    assert texts, "expected some own-site items"
    # NONE of the mocked raw-dump shapes survive
    assert not any(t.startswith("Number of CTAs:") for t in texts)
    assert not any(t.endswith(": not detected on site") for t in texts)
    assert not any(t.endswith(": none detected") for t in texts)
    # a re-phrased conversion-path strength IS present, and the cell stays scrape-grounded
    conv = [i for i in all_items if "conversion paths" in i.text.lower()]
    assert conv and conv[0].citation[0] == "your scraped site"
    assert conv[0].claim_strength == "internally_supported"
