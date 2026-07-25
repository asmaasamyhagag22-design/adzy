"""Analysis stages for the web app — SWOT, strategy (TOWS), marketing plan + content calendar.

Each function feeds the EXISTING pipeline (competitor.*, strategy.build_strategy,
media_plan.build_media_plan) a BusinessProfile dict and translates the result into the shape the
AdArchitect frontend reads off the stored brand (`b.swot`, `b.marketing_strategy`,
`b.marketing_plan`, `b.content_calendar`).

Everything degrades gracefully: no LLM caller / no Places key / a 403 (D-5) -> deterministic,
standalone output — never a hard failure. The raw BusinessProfile is stashed on the brand as
`_profile` by the scrape stage; if it's absent (e.g. a hand-seeded brand) the stages degrade to what
the brand projection carries.
"""
from __future__ import annotations

import dataclasses
import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)


def _caller(strong: bool):
    """A Gemini caller if creds exist, else None (every builder degrades on None). A caller that
    later 403s (D-5) is handled by the builders themselves — they never raise for that."""
    try:
        from business_profile.llm import default_caller
        return default_caller(strong=strong)
    except Exception:  # noqa: BLE001
        return None


def _profile_of(brand: dict) -> dict:
    return brand.get("_profile") or {}


# =====================================================================
# SWOT
# =====================================================================

def _compute_swot(profile: dict):
    """The api/routes/swot.py recipe, in-process. Degrades to standalone with no Places/web keys."""
    from competitor import (
        ReviewThemeExtractor, PlacesClient, build_matrix,
        find_subject_places, route_discovery, synthesize_swot,
    )
    from competitor.lite_scrape import lite_peer_dims
    from poster.locale import search_locale  # (gl, hl) from the brand's own evidence

    try:
        gl, hl = search_locale(profile)
    except Exception:  # noqa: BLE001
        gl, hl = "", ""
    try:
        client = PlacesClient(region_code=(gl or "").upper(), language_code=hl or "")
    except Exception:  # noqa: BLE001
        client = None

    try:
        result = route_discovery(profile, places_client=client)
    except Exception:  # noqa: BLE001
        logger.exception("route_discovery failed; standalone SWOT")
        return synthesize_swot_standalone(profile)

    peers = result.competitors
    try:
        from competitor.registry import brand_ref_from_profile, stable_competitors
        peers, _ = stable_competitors(brand_ref_from_profile(profile), result.competitors)
    except Exception:  # noqa: BLE001
        peers = result.competitors

    subject_places = None
    if client is not None:
        try:
            subject_places = find_subject_places(profile, client)
        except Exception:  # noqa: BLE001
            subject_places = None

    matrix = build_matrix(profile, peers, subject_name="You",
                          subject_places=subject_places, scrape_fn=lite_peer_dims)
    themes = []
    try:
        themes = ReviewThemeExtractor()(peers)
    except Exception:  # noqa: BLE001
        themes = []
    return synthesize_swot(matrix, themes=themes, profile=profile), peers


def synthesize_swot_standalone(profile: dict):
    """The no-discovery path — which must STILL honour competitors the owner named herself.

    This is the most likely path for a brand whose automatic discovery finds nothing, and it used
    to build the matrix with a hard-coded empty peer list. That made the manual-competitor feature
    useless exactly where it matters most: the client is told "add a competitor to get a real
    analysis", adds one, and the run that follows ignores it because discovery failed again."""
    from competitor import build_matrix, synthesize_swot
    from competitor.lite_scrape import lite_peer_dims

    peers = []
    try:
        from competitor.registry import brand_ref_from_profile, stable_competitors
        peers, _ = stable_competitors(brand_ref_from_profile(profile), [])
    except Exception:  # noqa: BLE001 — a missing registry must never break the analysis
        peers = []

    if not peers:
        matrix = build_matrix(profile, [], subject_name="You")
        return synthesize_swot(matrix, profile=profile), []
    matrix = build_matrix(profile, peers, subject_name="You", scrape_fn=lite_peer_dims)
    return synthesize_swot(matrix, profile=profile), peers


def _items(items) -> list[dict]:
    """SWOTItem -> frontend {title, reasoning, evidence_ref}."""
    out = []
    for it in items or []:
        cites = list(getattr(it, "citation", []) or [])
        out.append({
            "title": getattr(it, "text", ""),
            "reasoning": getattr(it, "evidence", "") or "",
            "evidence_ref": cites[0] if cites else "",
            "source_theme": getattr(it, "evidence", "") or "",
            "claim_strength": getattr(it, "claim_strength", "internally_supported"),
        })
    return out


def _swot_to_frontend(swot) -> dict:
    quad = {
        "strengths": _items(swot.strengths),
        "weaknesses": _items(swot.weaknesses),
        "opportunities": _items(swot.opportunities),
        "threats": _items(swot.threats),
    }
    counts = {k: len(v) for k, v in quad.items()}
    return {
        **quad,
        "mode": getattr(swot, "mode", "competitive"),
        "notes": list(getattr(swot, "notes", []) or []),
        "strategic_summary": {
            "main_advantage": quad["strengths"][0]["title"] if quad["strengths"] else "",
            "top_opportunity": quad["opportunities"][0]["title"] if quad["opportunities"] else "",
            "top_confirmed_threat": quad["threats"][0]["title"] if quad["threats"] else "",
        },
        "strategic_context": {
            "quadrant_counts": counts,
            "benchmark_quality": "competitive" if getattr(swot, "mode", "") == "competitive" else "standalone",
        },
    }


def generate_swot(brand: dict) -> dict:
    """Returns the patch to apply to the stored brand: swot + _swot_raw + has_swot."""
    swot, _peers = _compute_swot(_profile_of(brand))
    return {
        "swot": _swot_to_frontend(swot),
        "_swot_raw": dataclasses.asdict(swot),
        "has_swot": True,
    }


# =====================================================================
# STRATEGY (TOWS -> marketing_strategy)
# =====================================================================

def _swot_from_raw(raw: dict):
    """Rebuild a SWOT dataclass from the stored _swot_raw so build_tows can consume it."""
    from competitor.swot import SWOT, SWOTItem

    def _mk(items):
        return [SWOTItem(text=i.get("text", ""), citation=list(i.get("citation", []) or []),
                         evidence=i.get("evidence", ""),
                         claim_strength=i.get("claim_strength", "internally_supported"))
                for i in (items or [])]

    return SWOT(
        strengths=_mk(raw.get("strengths")), weaknesses=_mk(raw.get("weaknesses")),
        opportunities=_mk(raw.get("opportunities")), threats=_mk(raw.get("threats")),
        notes=list(raw.get("notes", []) or []), mode=raw.get("mode", "competitive"))


def generate_strategy(brand: dict) -> dict:
    from competitor import build_tows

    profile = _profile_of(brand)
    raw = brand.get("_swot_raw")
    swot = _swot_from_raw(raw) if raw else _compute_swot(profile)[0]
    caller = _caller(strong=True)
    try:
        tows = build_tows(swot, caller=caller, profile=profile)
    except Exception:  # noqa: BLE001
        logger.exception("build_tows failed; posture-only strategy")
        tows = None

    matrix: dict[str, list] = {"SO": [], "ST": [], "WO": [], "WT": []}
    priority = []
    posture, rationale = "balanced", ""
    if tows is not None:
        for s in tows.strategies:
            matrix.setdefault(s.type, []).append(
                {"title": s.title, "description": s.description, "claim_strength": s.claim_strength})
        priority = [{"priority": a.rank, "action": a.action, "rationale": a.rationale,
                     "horizon": a.horizon} for a in tows.priority_actions]
        posture = tows.posture
        rationale = (tows.notes[0] if tows.notes else "")

    # Channels/audience/pillars/goals grounded in the brand's own evidence.
    channels = list((brand.get("social_links") or {}).keys())
    if not channels:
        try:
            from strategy.builder import _platforms_for
            channels = _platforms_for(profile)
        except Exception:  # noqa: BLE001
            channels = []
    pillars = (brand.get("key_messages") or [])[:4]
    audience = [a for a in [brand.get("target_audience")] if a]

    return {
        "marketing_strategy": {
            "strategic_posture": posture,
            "posture_rationale": rationale,
            "positioning_statement": brand.get("tagline") or brand.get("description", ""),
            "value_proposition": pillars[0] if pillars else "",
            "tone_of_voice": brand.get("tone_of_voice", ""),
            "audience": audience,
            "content_pillars": pillars,
            "channels": channels,
            "goals": [],
            "tows_matrix": matrix,
            "priority_action_plan": priority,
            "campaign_brief_feed": [],
        },
        "has_strategy": True,
    }


# =====================================================================
# MARKETING PLAN + CONTENT CALENDAR
# =====================================================================

_PERIOD_DAYS = {"1_month": 30, "monthly": 30, "1_week": 7, "weekly": 7,
                "2_weeks": 14, "3_months": 90, "quarter": 90}


def _calendar_to_frontend(cal) -> dict:
    from datetime import date as _date
    entries = []
    for i, it in enumerate(getattr(cal, "items", []) or []):
        d = getattr(it, "date", "") or ""
        week = (getattr(it, "day_offset", i) // 7) + 1 if hasattr(it, "day_offset") else (i // 7) + 1
        day_name = ""
        try:
            day_name = _date.fromisoformat(d).strftime("%A")
        except Exception:  # noqa: BLE001
            day_name = ""
        entries.append({
            "week": week, "day": day_name, "date": d,
            "channel": getattr(it, "platform", ""),
            "topic": getattr(it, "topic", ""),
            "content_type": getattr(it, "content_type", ""),
            "hook": getattr(it, "hook", ""),
            "cta": getattr(it, "cta", ""),
            "funnel_stage": getattr(it, "funnel_stage", ""),
            "seasonal_theme": None,
            "poster": None,
        })
    return {"calendar": entries}


def _plan_to_frontend(media_plan, cal, brand: dict, objective: str) -> dict:
    """Synthesize a monthly campaign view from the media-plan objective + the calendar's real topics.
    Grounded, not fabricated: themes come from the calendar, CTA from the objective destination."""
    from collections import OrderedDict

    months = OrderedDict()
    for e in _calendar_to_frontend(cal)["calendar"]:
        key = (e["date"] or "")[:7] or "month-1"
        months.setdefault(key, []).append(e)

    obj_label = objective or (getattr(getattr(media_plan, "objectives", [None])[0], "objective", "")
                              if getattr(media_plan, "objectives", None) else "")
    campaigns = []
    for month, entries in list(months.items())[:6]:
        topics = [e["topic"] for e in entries if e["topic"]][:5]
        theme = topics[0] if topics else (brand.get("key_messages") or [""])[0]
        campaigns.append({
            "month": month,
            "campaign_name": theme[:60] or f"{brand.get('organization_name','Brand')} — {month}",
            "theme": theme,
            "marketing_message": (brand.get("key_messages") or [brand.get("tagline", "")])[0],
            "offer": {"headline": "", "details": ""},
            "call_to_action": entries[0]["cta"] if entries and entries[0]["cta"] else "Learn more",
            "hashtags": [f"#{(brand.get('organization_name') or 'brand').replace(' ', '')}"],
            "kpis": [obj_label] if obj_label else [],
        })
    return {"objective": obj_label, "campaigns": campaigns}


def generate_plan(brand: dict, *, planning_period: str = "1_month",
                  campaign_objective: str = "") -> dict:
    from strategy.builder import build_strategy

    profile = _profile_of(brand)
    days = _PERIOD_DAYS.get((planning_period or "").lower(), 30)
    caller = _caller(strong=True)

    media_plan = None
    try:
        from media_plan.builder import build_media_plan
        media_plan = build_media_plan(profile, caller=caller)
    except Exception:  # noqa: BLE001
        logger.exception("build_media_plan failed; calendar-only plan")

    swot = _swot_from_raw(brand["_swot_raw"]) if brand.get("_swot_raw") else None
    try:
        cal = build_strategy(profile, caller, days=days, swot=swot, media_plan=media_plan)
    except Exception:  # noqa: BLE001
        logger.exception("build_strategy failed")
        cal = None

    calendar = _calendar_to_frontend(cal) if cal is not None else {"calendar": []}
    plan = _plan_to_frontend(media_plan, cal, brand, campaign_objective) if cal is not None \
        else {"objective": campaign_objective, "campaigns": []}
    return {"marketing_plan": plan, "content_calendar": calendar, "has_plan": True}
