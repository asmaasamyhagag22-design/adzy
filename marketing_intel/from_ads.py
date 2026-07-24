# -*- coding: utf-8 -*-
"""ش3 — competitor-ad feed for the evidence-bound extractor. A LIGHT adapter (not SocialSignal:
the extractor duck-reads only .source_url/.signal_kind/.text, and a business page has no author to
hash). The moat is unchanged: every extracted item must quote an ad text verbatim, and the evidence
rebinds to the MATCHED ad's public snapshot_url."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, List

from .extractor import extract_marketing_intel
from .schemas import MarketingIntelligence


@dataclass(frozen=True)
class AdSignal:
    source_url: str
    text: str
    signal_kind: str = "post"


def signals_from_ads(ads) -> List[AdSignal]:
    """CompetitorAd list -> extractor signals. Empty ad_texts are skipped (nothing to quote)."""
    out: List[AdSignal] = []
    for ad in (ads or []):
        text = str(getattr(ad, "ad_text", "") or "").strip()
        if not text:
            continue
        out.append(AdSignal(source_url=str(getattr(ad, "snapshot_url", "") or ""), text=text))
    return out


def extract_competitor_intel(ads, caller: Any = None, *, brand_ref: str = "") -> MarketingIntelligence:
    """Offers/USPs/pain-points/message-angles from ONE competitor's public ad texts — competitor
    mode, same verbatim moat. Honest empty when no caller or no quotable texts."""
    return extract_marketing_intel(signals_from_ads(ads), caller, brand_ref=brand_ref,
                                   mode="competitor")
