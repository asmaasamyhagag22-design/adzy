"""Evidence Ledger — a WEB-tier claim is proof only when its source is REPUTABLE.

The moat is *provable* grounding. A hard claim backed only by a search snippet from an
aggregator / listicle / junk-SEO host (g2.com, similarweb.com, crunchbase.com …) is NOT
proof — anyone can seed those. This gate used to live only in poster/pick_angle, so the
SAME junk snippet could still "verify" a claim in the concept / calendar / TOWS / reel
paths. The reputability filter now runs at ledger-BUILD time (in `from_profile.add`), so
every gate that resolves against the ledger inherits it.

Contract pinned here (no live LLM / network):
  * a web fact sourced only by an aggregator host  -> UNSOURCED (dropped at build)
  * the same web fact sourced by a reputable outlet -> SOURCED, tier == "web"
  * brand-tier evidence (the brand's OWN site) is ALWAYS kept, url or not
  * a web fact with NO url is kept (can't judge a missing host -> don't over-drop)
  * the standalone `is_reputable_web_source(url)` predicate itself
"""
from __future__ import annotations

from grounding import EvidenceLedger, is_reputable_web_source


def _profile():
    """Minimal brand profile — the distinctive number below is NOT in it, so any match on
    it must come from the web-tier research fact (isolating the reputability behaviour)."""
    return {
        "name": {"value": "Digilians",
                 "evidence": [{"block_id": "b1", "page_url": "https://brand.example/",
                               "quote": "Digilians", "extractor": "llm"}],
                 "confidence": "high"},
        "description": {"value": "We train 5000 youth every year in AI and cybersecurity.",
                        "evidence": [{"block_id": "b1", "page_url": "https://brand.example/about",
                                      "quote": "train 5000 youth every year in AI and cybersecurity",
                                      "extractor": "llm"}],
                        "confidence": "high"},
        "value_propositions": [],
        "offerings": [],
        "trust_signals": [],
    }


def _research(url: str) -> dict:
    """A single web-tier research fact carrying a distinctive claim, sourced at `url`."""
    return {"facts": [{"text": "Digilians won 3200 industry awards worldwide.",
                       "source_url": url}]}


_CLAIM = "We won 3200 industry awards worldwide"


# --- the standalone predicate ----------------------------------------

def test_predicate_rejects_aggregator_and_accepts_media():
    assert is_reputable_web_source("https://www.g2.com/products/x") is False
    assert is_reputable_web_source("https://similarweb.com/website/x") is False
    assert is_reputable_web_source("https://www.crunchbase.com/organization/x") is False
    # A real news/media outlet is reputable EVIDENCE (not a competitor) -> kept.
    assert is_reputable_web_source("https://www.reuters.com/business/x") is True
    assert is_reputable_web_source("https://brand.example/about") is True


def test_predicate_rejects_non_http():
    assert is_reputable_web_source("") is False
    assert is_reputable_web_source("ftp://x") is False
    assert is_reputable_web_source(None) is False  # type: ignore[arg-type]


# --- ledger-build filtering ------------------------------------------

def test_web_claim_from_aggregator_is_unsourced():
    led = EvidenceLedger.from_profile(_profile(), research=_research("https://www.g2.com/x"))
    # The only evidence for "3200 awards" is a junk-host snippet -> the claim can't resolve.
    assert led.resolve_claim(_CLAIM) is None


def test_web_claim_from_reputable_source_resolves():
    led = EvidenceLedger.from_profile(
        _profile(), research=_research("https://www.reuters.com/business/digilians"))
    res = led.resolve_claim(_CLAIM)
    assert res is not None
    assert res.tier == "web"
    assert res.source_url == "https://www.reuters.com/business/digilians"


def test_web_claim_with_no_url_is_NOT_evidence():
    """P6 #1 — a web "fact" with no source_url cannot resolve a claim.

    This test used to assert the opposite ("a missing host can't be judged junk; keep it"). That
    made the moat circular: poster/brand_research.py declares `source_url: str = ""` BY DEFAULT,
    so an LLM-emitted fact with no source entered the ledger as evidence, resolved a hard claim,
    and the client's Ad Compliance Sheet printed "Verified against a sourced web result" with
    source_url: None. The model's own output became the proof of the model's own claim.
    If we cannot show the client a source, it is not evidence. Brand-tier is unaffected.
    """
    led = EvidenceLedger.from_profile(_profile(), research=_research(""))
    assert led.resolve_claim(_CLAIM) is None


def test_brand_tier_evidence_is_always_kept():
    # Brand-tier claims never route through the web reputability gate.
    led = EvidenceLedger.from_profile(_profile())
    res = led.resolve_claim("We train 5000 youth every year")
    assert res is not None
    assert res.tier == "brand"
