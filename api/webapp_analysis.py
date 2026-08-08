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
# TREND — a stage of its own (owner's ruling, 2026-07-26)
#
# «يبقا الترند ميظهرش معاه سوات، دا في تاب لوحدها · وبعدين السوات بناءً عليها»
#
# Trends used to exist ONLY as an attribute hung on the SWOT object after synthesis, which meant a
# brand with no SWOT had no market signals at all (measured: pepsi_com, 0 trends, nothing had ever
# fetched them) and every SWOT regeneration re-bought the same Serper results. They are their own
# evidence now, fetched once and read by whoever needs them.
#
# `swot.trends` is still written, as a MIRROR: six brands are stored today with the trends only in
# that place, and a reader that forgot them would blank a tab that works.
# =====================================================================

def trend_rows(trends) -> list:
    """Raw trend objects -> the serializable rows the UI reads (title, url, matched terms)."""
    rows = []
    for t in (trends or []):
        title = str(getattr(t, "title", "") or (t.get("title") if isinstance(t, dict) else "")).strip()
        if not title:
            continue
        url = str(getattr(t, "url", "") or (t.get("url") if isinstance(t, dict) else "")).strip()
        terms = getattr(t, "matched_terms", None)
        if terms is None and isinstance(t, dict):
            terms = t.get("matched_terms")
        rows.append({"title": title, "url": url,
                     "matched_terms": [str(x) for x in (terms or ()) if x]})
    return rows


def trends_patch(rows: list) -> dict:
    """The patch that records a trend fetch on the brand.

    `has_trends` is True even for an EMPTY result, and that is the point: "we looked and this
    market has no on-topic coverage" and "nobody has looked" are different facts, and a Trend tab
    that cannot tell them apart shows an empty page with no explanation for either.
    """
    return {"trends": list(rows or []), "has_trends": True}


def trends_of(brand: dict) -> list:
    """The brand's market signals — its own key first, the SWOT mirror second.

    The fallback is not politeness: every brand stored before 2026-07-26 has them only under
    `swot.trends`, and dropping that path would empty a tab that works today.
    """
    if not isinstance(brand, dict):
        return []
    own = brand.get("trends")
    if isinstance(own, list) and own:
        return own
    mirrored = (brand.get("swot") or {}).get("trends")
    return list(mirrored or []) if isinstance(mirrored, list) else []


def market_signals_for(brand: dict) -> list:
    """The brand's stored trend rows, filtered, in the shape `synthesize_swot(trends=…)` reads.

    THE FILTER HAS TO RUN HERE, not only where trends are fetched. `trends.sources` learned to
    reject shopping results — a place to BUY the category is not a statement ABOUT it — but every
    row stored before that runs through this path unfiltered, and each one becomes an Opportunity.

    MEASURED 2026-08-02 over the stored brands: of 22 "market signals", **3** were market signals.
    The rest were an Amazon product page, a Carrefour category, «Dried Fruits» twice as a bare
    label, two Instagram posts and a Facebook video — and they were the Opportunities column of a
    client's SWOT. What survives is Statista and two market-research reports, which is what the
    quadrant is for.

    Rows are read, never re-fetched: fetching is a paid Serper call and the market has not moved
    because we filtered it.
    """
    from types import SimpleNamespace
    try:
        from trends.sources import is_commerce_result
    except Exception:  # noqa: BLE001 — no filter available -> hand them all over, never drop blind
        def is_commerce_result(_t, _u):
            return False
    out = []
    for r in (trends_of(brand) or []):
        if not isinstance(r, dict):
            continue
        title, url = str(r.get("title") or ""), str(r.get("url") or "")
        if not title or is_commerce_result(title, url):
            continue
        out.append(SimpleNamespace(title=title, url=url,
                                   matched_terms=tuple(r.get("matched_terms") or ())))
    return out


def fetch_trends_for_brand(brand: dict) -> list:
    """Fetch this brand's market signals, standalone — no SWOT required."""
    return trend_rows(_trends_for(_profile_of(brand)))


# =====================================================================
# THE OWNER'S OWN NUMBERS
#
# `media_plan_v2.engine` has always accepted `capacity`, `monthly_budget` and `margin`, and always
# announced their absence in `open_inputs` — the plan panel literally prints "Still needed from
# you: طاقة الاستيعاب الفعلية / الميزانية الشهرية". There was nowhere to type them. The war-room
# had `dashboard/owner_inputs.py` for exactly this, quoting her 2026-07-20 ask «حط مكان يحطها فيه»,
# and it was deleted with that dashboard on 2026-07-26. Her question — "لو انا محدد ميزانيه اكتبها
# فين؟ انا قاعده ف الديفولت مقدرش اغير فيه" — is that gap, seen from the other side.
# =====================================================================

def sanitize_owner_inputs(data: dict) -> dict:
    """Keep what is usable, DROP what is not — never clamp.

    A 500% margin clamped to 95% would put a number on her screen that she never typed and that
    answers a different question. Out of range means "not answered", which the plan already knows
    how to say.
    """
    out: dict = {}
    src = data if isinstance(data, dict) else {}
    try:
        v = float(src.get("profit_margin_pct"))
        if 1 <= v <= 95:
            out["profit_margin_pct"] = round(v, 1)
    except (TypeError, ValueError):
        pass
    try:
        v = int(float(src.get("capacity_per_day")))
        if 1 <= v <= 100000:
            out["capacity_per_day"] = v
    except (TypeError, ValueError):
        pass
    try:
        v = float(src.get("monthly_budget"))
        if v > 0:
            out["monthly_budget"] = round(v, 2)
    except (TypeError, ValueError):
        pass
    return out


# =====================================================================
# SWOT
# =====================================================================

def _compute_swot(profile: dict, trends: Optional[list] = None):
    """The api/routes/swot.py recipe, in-process. Degrades to standalone with no Places/web keys.

    `trends` lets the caller hand in market signals that were ALREADY fetched (the standalone Trend
    stage). None means fetch them here, which is what every pre-2026-07-26 caller did — and what
    made every SWOT regeneration spend a fresh paid Serper call for data that had not changed.
    """
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

    # WHY THE MARKET LOOKED EMPTY. `route_discovery` already writes the reason a lane returned
    # nothing — which engine ran, what it was asked, and whether the search was REFUSED rather than
    # unproductive — and this path dropped every one of those notes.
    #
    # MEASURED LIVE on abuauf.com: the Serper monthly cap was spent, so every search returned [],
    # so the SWOT shipped "Standalone Strategic Analysis (No Competitors Found)". Two completely
    # different facts — "her market has no comparable business" and "we stopped searching" — and
    # the client was shown the first. Carrying these notes is what makes that distinguishable.
    _discovery_notes = [str(n).strip() for n in (getattr(result, "notes", None) or [])
                        if str(n).strip()]

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
    themes = list(themes) + _customer_voice_themes(profile)

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

    if trends is None:
        try:
            trends = _trends_for(profile)
        except Exception:  # noqa: BLE001 — a trend source must never break a grounded SWOT
            logger.exception("trend fetch failed; continuing without trends")
            trends = []
    # THE TREND TAB'S ACTUAL CONTENT. These are fetched, paid for and fed into the SWOT as
    # Opportunities/Threats — and the LIST was dropped here, so the one tab named after them
    # rendered SWOT bookkeeping instead ("quadrant counts", "benchmark quality"). Attached to the
    # SWOT object so it travels through the same serializer as everything else.
    _trend_rows = []
    for t in (trends or []):
        title = str(getattr(t, "title", "") or "").strip()
        url = str(getattr(t, "url", "") or "").strip()
        if not title:
            continue
        _trend_rows.append({
            "title": title,
            "url": url,
            "matched_terms": [str(x) for x in (getattr(t, "matched_terms", ()) or ()) if x],
        })

    swot = synthesize_swot(matrix, themes=themes, profile=profile,
                           unique_insights=insights, trends=trends,
                           ad_presence=ad_pres)
    swot.market_trends = _trend_rows
    # Only when the discovery lane came back empty: with peers on the board the notes are
    # bookkeeping, and a client does not need the plumbing narrated at them. With no peers they
    # are the ONLY explanation of the analysis they are looking at.
    if not peers and _discovery_notes:
        swot.notes.extend(_discovery_notes)
    return swot, peers


def _customer_voice_themes(profile: dict) -> list:
    """EVERY source of "what a real customer said about THIS brand", behind one door.

    Two holes closed here, and they were the same defect wearing two faces:

      * `reviews/absa.py` — the only producer of `is_own_brand=True` themes from reviews — was
        called from `competitor/full_run.py` and NOWHERE else. `grep absa` over this module and
        `api/routes/swot.py` returned zero. A brand with 200 Google reviews therefore got, in the
        dashboard, zero strengths and zero weaknesses out of them: `ReviewThemeExtractor` reads
        PEER reviews only (`themes.py:116` iterates `competitors`), which route to O/T.
      * `synthesize_swot_standalone` passed no `themes=` at all — on the path its own docstring
        calls "the most likely path for a brand whose automatic discovery finds nothing".

    ONE COLLECTOR, so a source can never again be wired into one caller and forgotten in the other.
    That is the whole reason this is a function and not two more inline try/excepts.

    SPEND: nothing here starts billing on a brand we have no data for. Social themes are read off
    disk (deterministic, no model). The review path returns [] before it builds a caller when the
    brand has no saved voice file, and `extract_aspect_themes` returns [] on a null caller.

    Never raises: a voice source that fails contributes nothing and the SWOT builds without it."""
    out = list(_social_themes_for(profile))
    # Reviews printed on the brand's OWN site. Extracted by the scraper and, until this line,
    # with nowhere to go — the exact shape of the two holes this collector was built to close.
    try:
        from competitor.social_themes import themes_from_testimonials
        out += list(themes_from_testimonials((profile or {}).get("testimonials")) or [])
    except Exception:  # noqa: BLE001 — customer voice must never break a grounded SWOT
        logger.exception("on-site testimonials unavailable; continuing without them")
    try:
        from competitor.full_run import _own_voice_themes
        url = str((profile or {}).get("source_url") or "").strip()
        if url:
            out += list(_own_voice_themes(url, profile, lambda *_a, **_k: None) or [])
    except Exception:  # noqa: BLE001 — customer voice must never break a grounded SWOT
        logger.exception("own-voice themes unavailable; continuing without them")
    return out


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
        from social_intel.snapshot import default_snapshot_dir
        path = default_snapshot_dir() / f"{slug}_ig.json"
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

    # THE CUSTOMER'S VOICE BELONGS HERE MOST OF ALL. This path used to call `synthesize_swot` with
    # no `themes=` whatsoever, so a brand with no discoverable peers — by this function's own
    # description, the commonest case — lost every comment and every review theme it had. That is
    # exactly backwards: with no peers there are no comparison gaps either, so customer voice is
    # the ONLY evidence left that is about the business rather than about the website.
    themes = _customer_voice_themes(profile)

    if not peers:
        matrix = build_matrix(profile, [], subject_name="You")
        return synthesize_swot(matrix, themes=themes, profile=profile), []
    matrix = build_matrix(profile, peers, subject_name="You", scrape_fn=lite_peer_dims)
    return synthesize_swot(matrix, themes=themes, profile=profile), peers


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


def _headline_strength(strengths: list) -> str:
    """The ONE line the client reads first — never a single customer's comment.

    MEASURED on nanocare, and caught by an adversarial audit of a written record:
    `main_advantage` was «شكرا جدا فرحتوني 💃🇪🇬» ("thank you so much, you made me happy"), quoted
    from one Instagram comment, above a national food-safety registration sitting six lines down.

    A customer's words are EVIDENCE FOR a strength, not the strength itself: "someone thanked us"
    is not a company advantage, and the priority scorer ranks customer voice above own-site claims
    (0.27 vs 0.12) on purpose — a real customer beats a self-description. That ordering is right
    for the column and wrong for the headline, because the headline is a claim ABOUT THE BUSINESS.

    So the headline skips customer-voice lines when any other strength exists, and falls back to
    the top line when customer voice is all there is — an empty headline would be a worse lie.
    """
    if not strengths:
        return ""
    for row in strengths:
        if "customer voice" not in str(row.get("reasoning") or "").lower():
            return str(row.get("title") or "")
    return str(strengths[0].get("title") or "")


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
        # The market signals themselves, each with the source that makes it checkable. The Trend
        # tab had no trends to show because this key did not exist.
        "trends": list(getattr(swot, "market_trends", []) or []),
        "mode": getattr(swot, "mode", "competitive"),
        "notes": list(getattr(swot, "notes", []) or []),
        "strategic_summary": {
            "main_advantage": _headline_strength(quad["strengths"]),
            "top_opportunity": quad["opportunities"][0]["title"] if quad["opportunities"] else "",
            "top_confirmed_threat": quad["threats"][0]["title"] if quad["threats"] else "",
        },
        "strategic_context": {
            "quadrant_counts": counts,
            "benchmark_quality": "competitive" if getattr(swot, "mode", "") == "competitive" else "standalone",
        },
    }


def generate_swot(brand: dict) -> dict:
    """Returns the patch to apply to the stored brand: swot + _swot_raw + has_swot.

    THE SWOT BUILDS ON THE TREND STAGE (owner's ruling: «السوات بناءً عليها»). When the brand
    already carries fetched signals, they are handed in instead of re-bought — a SWOT regeneration
    used to spend a fresh Serper call for data that had not changed. A brand with no trend stage
    yet keeps the old behaviour (the SWOT fetches for itself), so nothing is ever blocked on a
    stage the user has not run.
    """
    handed = market_signals_for(brand) or None
    swot, _peers = _compute_swot(_profile_of(brand), trends=handed)
    # THE COMPETITORS WERE DISCOVERED AND THROWN AWAY ON THIS LINE. `_peers` was bound and never
    # used, so a brand analysed through the DASHBOARD had `competitors == []` forever — the whole
    # discovery pass ran, cost what it costs, and its result never left this function. Only the
    # CLI path (`adopt_full_result`) ever wrote that field, so the panel could show a count of
    # zero and nothing else.
    #
    # Stored under a separate key rather than overwriting `competitors`: that field is the OWNER's
    # registry, which she curates by hand through /api/brands/{dk}/competitors, and a generated
    # list must not silently replace a chosen one.
    out = {
        "swot": _swot_to_frontend(swot),
        "_swot_raw": _swot_raw_dict(swot),   # asdict alone erased the trends it had just computed
        "has_swot": True,
    }
    try:
        found = []
        for p in (_peers or []):
            name = str(getattr(p, "name", "") or (p.get("name") if isinstance(p, dict) else ""))
            url = str(getattr(p, "url", "") or (p.get("url") if isinstance(p, dict) else ""))
            if name or url:
                found.append({"name": name, "url": url})
        if found:
            out["discovered_competitors"] = found
    except Exception:  # noqa: BLE001 — the SWOT is the deliverable; the peer list is a bonus
        pass
    return out


# =====================================================================
# STRATEGY (TOWS -> marketing_strategy)
# =====================================================================

def _swot_raw_dict(swot) -> dict:
    """The stored `_swot_raw`, WITH the parts `dataclasses.asdict` cannot see.

    `market_trends` is attached dynamically (`_compute_swot`, above) because it is not a field on
    the SWOT dataclass. `asdict` therefore never captured it, so every SWOT this app stored had
    its trends erased at the moment of writing — and the rebuild below could not put back what was
    never recorded. The Trend tab then read empty and looked like a brand with no market signals.
    """
    import dataclasses
    raw = dataclasses.asdict(swot)
    trends = list(getattr(swot, "market_trends", []) or [])
    if trends:
        raw["market_trends"] = trends
    return raw


def _swot_from_raw(raw: dict):
    """Rebuild a SWOT dataclass from the stored _swot_raw so build_tows can consume it.

    Carries `site_readiness` and `market_trends` too. It used to drop both: site mechanics were
    MOVED out of the strategic quadrants on the owner's ruling and `_swot_to_frontend` carries them
    precisely so the move is honest rather than a deletion — and then this rebuild deleted them.
    """
    from competitor.swot import Priority, SWOT, SWOTItem

    def _one(i: dict):
        item = SWOTItem(text=i.get("text", ""), citation=list(i.get("citation", []) or []),
                        evidence=i.get("evidence", ""),
                        claim_strength=i.get("claim_strength", "internally_supported"))
        # ID AND PRIORITY TRAVEL TOO. This rebuilt four fields, so every path through it — the
        # CLI→dashboard bridge `adopt_full_result`, the strategy builder, the plan builder — threw
        # away the ranking the analysis had just computed AND the stable handle TOWS anchors on.
        # `SWOTItem.id` exists precisely because TOWS used to derive S1/W2/O1 from list POSITION;
        # dropping the id here re-creates that bug one layer up, silently, on adoption.
        item.id = str(i.get("id") or "")
        item.peers_compared = int(i.get("peers_compared") or 0)
        p = i.get("priority")
        if isinstance(p, dict):
            try:
                fields = getattr(Priority, "__dataclass_fields__", {})
                item.priority = Priority(**{k: v for k, v in p.items() if k in fields})
            except Exception:  # noqa: BLE001 — a priority that will not rebuild is dropped, not faked
                item.priority = None
        return item

    def _mk(items):
        return [_one(i) for i in (items or []) if isinstance(i, dict)]

    swot = SWOT(
        strengths=_mk(raw.get("strengths")), weaknesses=_mk(raw.get("weaknesses")),
        opportunities=_mk(raw.get("opportunities")), threats=_mk(raw.get("threats")),
        notes=list(raw.get("notes", []) or []), mode=raw.get("mode", "competitive"),
        site_readiness=_mk(raw.get("site_readiness")))
    swot.market_trends = list(raw.get("market_trends") or [])
    return swot


def adopt_full_result(brand: dict, result: dict) -> dict:
    """Bring a CLI full-run `<slug>_result.json` into the webapp brand — the ONE front end.

    THE DEFECT THIS CLOSES (measured 2026-07-26, Al Fouad Pharmacies): the CLI computed a real
    competitive SWOT — 11 strengths, 6 opportunities, 1 threat, 2 site-readiness items — wrote it
    to `outputs/alfouad_full_result.json`, and the stored brand said `has_swot: false, swot: null`.
    A `grep` for `_full_result` across every .py in the repo returns ZERO writers: the partial copy
    in the store was left by a throwaway script that listed 8 keys by hand, so the other 12 —
    swot, trends and competitors among them — were never on the wire.

    NO ALLOW-LIST. `_full_result` takes the whole result verbatim, so an analysis key added next
    month arrives without anyone remembering to widen a list. That hand-written list IS the bug.

    NON-DESTRUCTIVE. An owner-approved SWOT is her signature; an import never overwrites it. And a
    result with no SWOT leaves `has_swot` untouched rather than asserting one — absence is not a
    pass.

    Returns the PATCH to apply to the stored brand (never writes).
    """
    result = dict(result or {})
    patch: dict = {"_full_result": result}

    comps = result.get("competitors")
    if isinstance(comps, list) and comps:
        patch["competitors"] = comps

    raw = result.get("swot")
    if not isinstance(raw, dict) or not raw:
        return patch                     # nothing to claim
    if (brand or {}).get("swot_approved"):
        return patch                     # her signature wins

    raw = dict(raw)
    # The CLI keeps market signals at the TOP level of the result; the webapp reads them off the
    # SWOT. Same facts, different address — carried across here rather than lost in the gap.
    if not raw.get("market_trends") and result.get("trends"):
        raw["market_trends"] = list(result["trends"])
    swot = _swot_from_raw(raw)
    patch["swot"] = _swot_to_frontend(swot)
    patch["_swot_raw"] = _swot_raw_dict(swot)
    patch["has_swot"] = True
    return patch


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


def social_performance_for(profile: dict) -> dict:
    """What the brand's OWN social content actually achieves, from the snapshot already on disk.

    THE NUMBER WAS ALREADY THERE (owner, 2026-08-01: «اكتر حاجه جايبه لايك»). `like_count` is
    declared in the social schema, populated by every provider, and — measured with a repo-wide
    grep before this was written — read by nothing. `_social_themes_for` above loads the very same
    signals one function away and passes them to theme extraction alone, so the engagement rode
    all the way into the analysis and was dropped at the last step.

    Deterministic and free: the snapshot is read off disk, ranking is arithmetic, no model and no
    billing. Returns {} for a brand with no snapshot — the honest "we have not collected it".
    """
    try:
        from scripts.pull_instagram_apify import signals_from_apify_items
        from social_intel.performance import creative_lessons, top_performers
        from social_intel.snapshot import load_ig_snapshot, snapshot_path_for

        path = snapshot_path_for(profile)
        if path is None or not path.is_file():
            return {}
        # Accepts BOTH snapshot shapes. Two different pullers write this filename — Apify's
        # `{"items": …}` and the Graph API's `{"media": …}` — and every reader here understood
        # only the first, so a Graph-shaped file read as "this brand has no social presence".
        rows, meta = load_ig_snapshot(path)
        if not rows:
            return {}
        # LABEL THE ROWS. Without `is_own_brand`, `performance.py` files the brand's OWN posts
        # under `peers` and the lessons layer calls them "a competitor's top post".
        rep = top_performers(signals_from_apify_items(
            rows, is_own_brand=True, brand_ref=str(meta.get("username") or "")))
        if not rep.checked:
            # Say WHY rather than returning an empty dict that reads as "nothing works here".
            return {"checked": False, "reason": rep.reason}
        return {
            "checked": True,
            "own": rep.own.model_dump() if rep.own else None,
            "peers": [p.model_dump() for p in rep.peers],
            "lessons": creative_lessons(rep),
        }
    except Exception as exc:  # noqa: BLE001 — engagement is a bonus, never a dependency
        # SAY WHY. This used to `return {}`, which the caller renders as "we never collected this
        # brand's social" — indistinguishable from a snapshot that IS on disk and an import that
        # blew up reading it. MEASURED 2026-08-01: the function returned `checked: True` with 15
        # ranked posts when called directly, and `{}` through the route, and the swallow was the
        # only reason that difference had nowhere to show. An error is a THIRD state.
        logger.exception("social_performance_for failed")
        return {"checked": False, "reason": f"could not read the snapshot ({type(exc).__name__}: "
                                            f"{str(exc)[:160]})"}
