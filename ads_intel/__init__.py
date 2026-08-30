"""Competitor live-ads intelligence (roadmap Phase-2 #8) — Meta Ad Library, official API.

The pitch's insight: an ad that has been RUNNING FOR MONTHS is a proven winner — longevity
is the market's own split-test verdict. This package turns competitors' running ads into
(a) an ad-presence signal for the SWOT and (b) platform/format priors for the media plan.

Governance (same shape as reviews/ and social_intel/): providers PARSE saved snapshots only
(PD-4 — the network pull lives in scripts/pull_meta_ads.py behind an owner META_ADS_TOKEN);
fixtures are the hermetic backbone (PD-3); page names are PUBLIC business identities (not
personal PII) and stay verbatim.
"""
from .consumer import DEFAULT_SNAPSHOTS_DIR, ad_presence_for_refs, ref_slug
from .schemas import AdPresence, CompetitorAd, ad_presence

__all__ = ["CompetitorAd", "AdPresence", "ad_presence",
           "ad_presence_for_refs", "ref_slug", "DEFAULT_SNAPSHOTS_DIR"]
