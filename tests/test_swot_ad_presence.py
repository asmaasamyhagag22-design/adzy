"""U2 ad-presence -> SWOT Threats bridge — flows the instant META_ADS_TOKEN lands; default None is
regression-safe (v2.2 §5). Hermetic; no network."""
from __future__ import annotations

from ads_intel.schemas import AdPresence
from competitor.brand_signals import threats_from_ad_presence
from competitor.matrix import build_matrix
from competitor.swot import synthesize_swot


def _aps():
    return [AdPresence(page_name="Rival Clinic", active_count=5, proven_winners=2, longest_days=90),
            AdPresence(page_name="SmallCo", active_count=1, proven_winners=0, longest_days=10)]


def test_ad_presence_becomes_graded_threats():
    th = threats_from_ad_presence(_aps())
    assert len(th) == 2
    winner = next(t for t in th if "Rival" in t.text)
    assert winner.claim_strength == "validated" and "Meta Ad Library" in winner.citation
    active = next(t for t in th if "SmallCo" in t.text)
    assert active.claim_strength == "directional_not_validated"


def test_empty_ad_presence_is_no_op():
    assert threats_from_ad_presence(None) == [] and threats_from_ad_presence([]) == []


def test_synthesize_swot_default_is_unchanged_and_ads_add_threats():
    m = build_matrix({"name": {"value": "X"}}, [])
    base = synthesize_swot(m, profile={"name": {"value": "X"}})
    with_ads = synthesize_swot(m, profile={"name": {"value": "X"}}, ad_presence=_aps())
    # default (ad_presence=None) is byte-identical threats; ads add exactly 2
    assert len(with_ads.threats) == len(base.threats) + 2
    assert any("proven-winner" in t.text for t in with_ads.threats)
