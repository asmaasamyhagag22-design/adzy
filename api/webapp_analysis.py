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

    # A WEB ENGINE, like api/routes/swot.py already passes. Without one the router falls to
    # NullWebDiscoveryEngine, so every ECOMMERCE/REACH brand gets ZERO competitors here — and
    # nanocare's own stored run reads "0 Places + 4 web", meaning all of its peers came from
    # exactly this engine. With no peers there is nothing to compare and no SWOT worth the name.
    web_engine = None
    try:
        from competitor.web_discovery import default_web_engine
        web_engine = default_web_engine()
    except Exception:  # noqa: BLE001
        web_engine = None

    try:
        result = route_discovery(profile, places_client=client, web_engine=web_engine)
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

    # CUSTOMER COMMENTS — the owner's point: "كان كومنت اكتر كان ادق واحسن". Places reviews reach
    # 29% of runs; social reaches 82%. A theme built from a real comment is the only SWOT item that
    # quotes a customer instead of describing a website, and the comments were already being
    # collected, analysed and dropped. They join the SAME theme list, so `synthesize_swot` routes
    # them with the mapping it already has: own complaint -> Weakness, peer complaint -> Opportunity.
    themes = list(themes) + _social_themes_for(profile)

    # THE SAME SOURCES THE CLI FEEDS IT. `competitor/full_run.py` passes unique_insights, trends
    # and ad_presence; this path passed themes and profile only, so the SWOT the owner looks at in
    # the dashboard was built from strictly less evidence than the one the CLI writes to disk —
    # and nothing disclosed the difference. Each source is wrapped exactly like the CLI wraps it:
    # a source that fails contributes nothing and never breaks the analysis.
    insights = []
    try:
        from competitor.swot import unique_insight_texts
        insights = unique_insight_texts(profile)
    except Exception:  # noqa: BLE001
        insights = []

    ad_pres = []
    try:
        from ads_intel import ad_presence_for_refs
        from competitor.full_run import competitor_ref_slugs
        ad_pres = ad_presence_for_refs(competitor_ref_slugs(peers))
    except Exception:  # noqa: BLE001 — ads-intel must never break a run
        ad_pres = []

    trends = _trends_for(profile)

    return synthesize_swot(matrix, themes=themes, profile=profile,
                           unique_insights=insights, trends=trends,
                           ad_presence=ad_pres), peers


def _social_themes_for(profile: dict) -> list:
    """Themes from ALREADY-SAVED social snapshots. Deterministic, no network, no model, no spend.

    Reads only what is on disk: pulling fresh comments is a separate, spend-gated decision
    (`social_intel.ig_autopull`), and an analysis must never start billing on its own. Returns []
    for a brand with no snapshot, which is simply the honest "we have not collected its comments"."""
    try:
        from pathlib import Path
        import json as _json

        from competitor.social_themes import themes_from_signals
        from scripts.pull_instagram_apify import signals_from_apify_items

        from api.brand_translate import domain_key_from_url
        src = (profile or {}).get("source_url")
        if isinstance(src, dict):
            src = src.get("value")
        slug = domain_key_from_url(str(src or ""))
        path = Path("runs/social_snapshots") / f"{slug}_ig.json"
        if not path.is_file():
            return []
        data = _json.loads(path.read_text(encoding="utf-8"))
        # This snapshot is the BRAND's own account (`ig_username_from_profile` reads only the
        # subject), so its comments are own-brand voice: praise becomes a Strength and a complaint
        # a Weakness. Without saying so they defaulted to peer voice, and a customer thanking THIS
        # brand would have been filed as a rival's advantage.
        return themes_from_signals(signals_from_apify_items(data.get("items") or []),
                                   is_own_brand=True)
    except Exception:  # noqa: BLE001 — customer voice is a bonus, never a dependency
        return []


def _trends_for(profile: dict) -> list:
    """Live market signals, bounded. Mirrors competitor/full_run.py.

    The window is deliberately SHORTER than the CLI's 12s: this runs inside a user-facing job with
    no timeout of its own, and `top_trends_bounded` abandons its worker thread rather than killing
    it — so a long wall means an orphaned thread and a Serper call whose paid result is discarded.
    Returns [] on anything at all; a market signal is a bonus, never a dependency."""
    try:
        from poster.locale import search_locale
        from trends.engine import keywords_from_profile, top_trends_bounded
        kws = keywords_from_profile(profile)
        if not kws:
            return []
        gl, hl = search_locale(profile)
        return top_trends_bounded(kws, timeout_s=8.0, require_match=True, top_k=6,
                                  gl=gl, hl=hl)
    except Exception:  # noqa: BLE001
        return []


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
    """SWOTItem -> frontend {title, reasoning, evidence_ref, id, priority}.

    THE PRIORITY AND THE ID USED TO STOP HERE. `api/routes/swot.py` had already been taught to
    carry them; this second serializer — the one the dashboard actually calls — still shipped five
    keys, so every ranking decision the analysis makes was computed, stored and then dropped one
    step before the screen. Same defect the images path had that morning: produced, serialized,
    never shown.

    The id is what `build_tows` anchors its plan steps on; without it on the wire the plan
    renumbers by list position and points at a different factor than the one it cites."""
    out = []
    for it in items or []:
        cites = list(getattr(it, "citation", []) or [])
        row = {
            "title": getattr(it, "text", ""),
            "reasoning": getattr(it, "evidence", "") or "",
            "evidence_ref": cites[0] if cites else "",
            "citation": cites,
            "source_theme": getattr(it, "evidence", "") or "",
            "claim_strength": getattr(it, "claim_strength", "internally_supported"),
            "id": getattr(it, "id", "") or "",
        }
        p = getattr(it, "priority", None)
        if p is not None:
            row["priority"] = {
                "importance": p.importance, "performance": p.performance,
                "impact": p.impact, "probability": p.probability,
                "basis": p.basis, "rank": p.rank, "focus": p.focus,
                "peers_measured": p.peers_measured,
            }
        out.append(row)
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
        # SITE READINESS IS A MOVE, NOT A DELETION. Site mechanics were pulled out of the strategic
        # quadrants on the owner's ruling — "facts about the website, not about the business" — but
        # the destination was never serialized, so for the client they simply disappeared (measured
        # on nanocare: 4 items). A quadrant they were moved OUT of is only honest if the place they
        # were moved TO is on the wire.
        "site_readiness": _items(getattr(swot, "site_readiness", []) or []),
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


def _strategy_readiness_notes(brand: dict, profile: dict) -> list[str]:
    """P-11: say out loud when the system's own gate judged this plan's evidence insufficient.

    `business_profile/readiness.py` computes `ready_for_strategy` and names the blockers, and
    NOTHING read it — 0 hits in `dashboard/run.py` and 0 here. Measured on the stored nanocare
    profile: `ready_for_strategy: false`, `strategy_blockers: ["strategy_evidence_below_threshold"]`
    — and a full posture, TOWS matrix and ranked action plan shipped with no mention of either.

    The war room already learned this lesson for the SWOT half of the same verdict after Volume
    Qatar shipped on thin evidence in silence; this is the strategy half, on the owner's same
    ruling: the plan still ships, and the caveat ships with it.

    ABSENT IS NOT FALSE. A profile from an older run carries no readiness block at all, and
    inventing a failure for it would be the same overclaim pointing the other way."""
    for source in (brand, profile or {}):
        r = source.get("readiness") if isinstance(source, dict) else None
        if not isinstance(r, dict) or "ready_for_strategy" not in r:
            continue
        if r.get("ready_for_strategy") is not False:
            return []
        blockers = [str(b).strip() for b in (r.get("strategy_blockers") or []) if str(b).strip()]
        why = f" ({', '.join(blockers)})" if blockers else ""
        return ["الخطة دي اتبنت على أدلة اعتبرها النظام نفسه تحت حد الكفاية"
                f"{why} — تُقرأ كاتجاه مبدئي لا كخطة مُثبتة. الأدلة الناقصة أعلاه هي اللي "
                "تحوّلها لخطة كاملة."]
    return []


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
            "notes": _strategy_readiness_notes(brand, profile),
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
