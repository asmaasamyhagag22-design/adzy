"""POST /api/swot/from-profile — competitor-grounded SWOT from a BusinessProfile.

Runs the adaptive discovery router -> comparative matrix -> SWOT synthesis on a
profile already produced by /api/run (the browser holds it after a run, so no
re-scrape and no job-store changes are needed here).

Degrades to a STANDALONE (profile-only) SWOT when no competitors are found —
never empty, never fabricated (matches the CLI behavior).

This path does not re-scrape competitor websites (too slow for a sync request),
so the competitor *scraped* dimensions stay UNKNOWN — the comparison leans on the
Places dimensions. To make those comparable the route now:
  1. looks up the SUBJECT's own Places listing (`find_subject_places`) so
     rating / review-volume gaps get real verdicts -> market-position THREATS;
  2. extracts grounded review THEMES from the peers' real Places reviews
     (complaints -> Threats; unmet needs -> Opportunities) — the customer voice;
  3. synthesizes a TOWS layer (strategies + priority actions) from the cited SWOT.
Each step degrades safely to today's behavior when its data/key is absent.
"""
from __future__ import annotations

import dataclasses
import logging
from typing import Any, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter()
logger = logging.getLogger(__name__)


class SwotFromProfileRequest(BaseModel):
    profile: dict[str, Any]


class SwotItemModel(BaseModel):
    text: str
    citation: List[str]
    evidence: str
    claim_strength: str = "internally_supported"


class CompetitorBrief(BaseModel):
    """WHO the compared competitors are — the owner must be able to see them."""
    name: str
    website: Optional[str] = None
    rating: Optional[float] = None
    review_count: Optional[int] = None
    source: str = "places"          # "places" | "web"
    why_selected: str = ""


class SwotResponse(BaseModel):
    mode: str  # "competitive" | "standalone"
    strengths: List[SwotItemModel]
    weaknesses: List[SwotItemModel]
    opportunities: List[SwotItemModel]
    threats: List[SwotItemModel]
    notes: List[str]
    competitor_count: int
    competitors: List[CompetitorBrief] = []
    # TOWS synthesis (strategies + ranked priority actions + posture), derived
    # from the cited SWOT items. None only if synthesis itself failed.
    tows: Optional[dict] = None


def _items(items) -> List[dict]:
    return [
        {"text": i.text, "citation": list(i.citation), "evidence": i.evidence,
         "claim_strength": getattr(i, "claim_strength", "internally_supported")}
        for i in items
    ]


@router.post("/swot/from-profile", response_model=SwotResponse)
def swot_from_profile(req: SwotFromProfileRequest) -> SwotResponse:
    try:
        from competitor import (
            ReviewThemeExtractor, PlacesClient, build_matrix, build_tows,
            find_subject_places, route_discovery, synthesize_swot,
        )
        from competitor.swot import unique_insight_texts
        from competitor.web_discovery import default_web_engine

        profile = req.profile

        # Places client is only used for LOCAL/HYBRID routing; if the key is
        # missing or the business is ECOMMERCE/UNKNOWN, discovery degrades safely.
        try:
            client = PlacesClient()
        except Exception:
            client = None

        # ECOMMERCE/HYBRID -> live SERP peer discovery when a search key is set
        # (SERPER_API_KEY/...); None otherwise -> router uses NullWebDiscoveryEngine.
        result = route_discovery(profile, places_client=client,
                                 web_engine=default_web_engine())

        # The subject's OWN Places listing: without it every Places gap is "n/a"
        # and Threats can never fire on this path (the measured root cause of the
        # always-empty Threats quadrant). Grounded match only; None on no-match.
        subject_places = find_subject_places(profile, client) if client else None

        # LIGHT peer dimensions: web/SERP peers carry no Places data, and the full
        # scraper is minutes/peer — so fetch each peer's homepage over plain HTTP
        # and derive the cheap comparable dims (social/CTA/WhatsApp/booking).
        # Failures leave that peer's cells UNKNOWN; never fabricated.
        from competitor.lite_scrape import lite_peer_dims

        # REGISTRY (P6 #11a): SWOT computes against the owner-APPROVED baseline, never the raw
        # live pool — `approved_competitors` says exactly that. This route used to skip the
        # registry entirely, so the DASHBOARD (the paid deliverable) re-ran discovery on every
        # call and the run-to-run competitor drift the owner caught on 2026-07-12 stayed live for
        # the client, while the CLI was stable. Never blocks the deliverable: on any registry
        # failure we fall back to the raw pool, loudly.
        peers = result.competitors
        try:
            from datetime import datetime, timezone

            from competitor.registry import brand_ref_from_profile, stable_competitors
            peers, _rdiff = stable_competitors(
                brand_ref_from_profile(profile), result.competitors,
                now=datetime.now(timezone.utc).date().isoformat())
        except Exception as exc:  # noqa: BLE001 — the registry never blocks a run
            logger.warning("competitor registry skipped (%s) -> raw discovery pool",
                           type(exc).__name__)
            peers = result.competitors

        matrix = build_matrix(profile, peers, subject_name="You",
                              subject_places=subject_places,
                              scrape_fn=lite_peer_dims)

        # Customer voice: grounded review themes from the peers' real Places
        # reviews (never raises; [] without a Gemini caller or enough reviews).
        themes = []
        try:
            extractor = ReviewThemeExtractor()
            themes = extractor(result.competitors)
        except Exception:
            themes = []

        # On-topic market trends -> brand-level Opportunities/Threats (best-effort; [] on failure).
        trends: list = []
        try:
            from trends import keywords_from_profile, top_trends_bounded
            kws = keywords_from_profile(profile or {})
            if kws:
                trends = top_trends_bounded(kws, timeout_s=12.0, require_match=True, top_k=6)
        except Exception:
            trends = []

        swot = synthesize_swot(matrix, themes=themes,
                               unique_insights=unique_insight_texts(profile),
                               profile=profile, trends=trends)
        if subject_places is not None:
            swot.notes.append(
                f"subject Places listing matched: {subject_places.name} "
                f"(rating={subject_places.rating}, reviews={subject_places.review_count})")
        # Surface WHY discovery found what it found (was swallowed before — an
        # empty/standalone result was undiagnosable from the response).
        swot.notes.extend(f"discovery: {n}" for n in (getattr(result, "notes", None) or []))

        # TOWS synthesis (team BI-platform layer, Ledger-gated). Deterministic
        # without an LLM; Gemini crafts the strategy text when available.
        tows_dict = None
        try:
            caller = None
            try:
                from business_profile.llm import default_caller
                caller = default_caller(strong=False)
            except Exception:
                caller = None
            tows = build_tows(swot, caller=caller, profile=profile)
            tows_dict = dataclasses.asdict(tows)
        except Exception:
            tows_dict = None

        competitors = []
        for c in result.competitors:
            cand = getattr(c, "candidate", None)
            sel = getattr(c, "selection", None)
            if cand is None:
                continue
            competitors.append(CompetitorBrief(
                name=str(getattr(cand, "name", "") or "?"),
                website=getattr(cand, "website", None),
                rating=getattr(cand, "rating", None),
                review_count=getattr(cand, "review_count", None),
                source="web" if getattr(cand, "rating", None) is None
                              and not getattr(cand, "formatted_address", None) else "places",
                why_selected=str(getattr(sel, "why_selected", "") or ""),
            ))

        return SwotResponse(
            mode=swot.mode,
            strengths=_items(swot.strengths),
            weaknesses=_items(swot.weaknesses),
            opportunities=_items(swot.opportunities),
            threats=_items(swot.threats),
            notes=list(swot.notes),
            competitor_count=len(result.competitors),
            competitors=competitors,
            tows=tows_dict,
        )
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=500, detail=f"SWOT generation failed: {exc}"
        ) from exc
