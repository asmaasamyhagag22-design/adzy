"""Ads-intel schemas (strict, PD-5) + the deterministic ad-presence aggregation."""
from __future__ import annotations

from datetime import date, datetime, timezone
from typing import List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


class CompetitorAd(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: Literal["meta_ad_library", "fixture"]
    ad_id: str
    page_name: str                                  # PUBLIC business identity — verbatim
    page_id: str = ""                               # 1.1: exact advertiser id when the API returns it
    brand_ref: str = ""                             # which competitor this belongs to (slug)
    platforms: List[str] = Field(default_factory=list)   # facebook / instagram / messenger...
    started_at: Optional[date] = None
    is_active: bool = True
    creative_format: str = ""                       # video / image / carousel / "" (unknown)
    ad_text: str = ""                               # the ad's public creative text (may be "")
    # ش2 additive: the link-card fields (documented ads_archive fields) — the CTA truth source
    link_captions: List[str] = Field(default_factory=list)
    link_titles: List[str] = Field(default_factory=list)
    snapshot_url: str = ""                          # the Ad Library permalink (public)
    scraped_at: datetime


class AdPresence(BaseModel):
    """Deterministic aggregation of ONE competitor's running ads — the SWOT/media-plan
    signal. No model involved; longevity math is code."""
    model_config = ConfigDict(extra="forbid")

    brand_ref: str = ""
    page_name: str = ""
    active_count: int = 0
    longest_days: int = 0                           # the market's own split-test verdict
    proven_winners: int = 0                         # active >= threshold days
    formats: dict = Field(default_factory=dict)     # format -> count
    platforms: dict = Field(default_factory=dict)   # platform -> count
    # ش2 additive facts (deterministic, facts.py): the 30d winner tier + iteration fatigue
    winners_30d: int = 0
    # 1.1 honesty: ads pulled by the broad keyword query but NOT the competitor's (dropped)
    filtered_out: int = 0
    fatigue_groups: int = 0
    top_cta: str = ""
    top_format: str = ""


_PROVEN_DAYS = 60      # an ad still running after 2 months has earned its budget


def ad_presence(ads: List[CompetitorAd], *, today: Optional[date] = None) -> AdPresence:
    """Aggregate one competitor's ads. Pure; empty input -> honest zeros."""
    ads = list(ads or [])
    if not ads:
        return AdPresence()
    ref_day = today or datetime.now(timezone.utc).date()
    out = AdPresence(brand_ref=ads[0].brand_ref, page_name=ads[0].page_name)
    for ad in ads:
        if not ad.is_active:
            continue
        out.active_count += 1
        if ad.started_at:
            days = max(0, (ref_day - ad.started_at).days)
            out.longest_days = max(out.longest_days, days)
            if days >= _PROVEN_DAYS:
                out.proven_winners += 1
        if ad.creative_format:
            out.formats[ad.creative_format] = out.formats.get(ad.creative_format, 0) + 1
        for pl in ad.platforms:
            out.platforms[pl] = out.platforms.get(pl, 0) + 1
    # ش2: the deterministic facts layer (winner-30d / fatigue / dominant CTA+format)
    from .facts import presence_facts
    for k, v in presence_facts(ads, today=ref_day).items():
        setattr(out, k, v)
    return out
