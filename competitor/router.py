"""Adaptive multi-engine discovery router.

Classifies the business (business_type.classify_business_type), then routes to
the right engine:

    LOCAL       -> Places          (discovery.discover_competitors)
    ECOMMERCE   -> web engine      (pluggable; NullWebDiscoveryEngine by default)
    REACH       -> web engine      (education / professional / B2B / agency — competes by
                   category not proximity, so Places-proximity is the wrong relevance model;
                   the SERP engine's LLM judge finds true category peers and drops the
                   universities/directories Places surfaced. MEASURED: ITI.)
    HYBRID      -> Places + web    (merged, deduped)
    UNKNOWN     -> web engine      (grounded SERP peers when one is wired; the Null
                   engine returns [] -> standalone, the old skip behavior. MEASURED
                   need: a B2B services company — no address, no cart — classified
                   UNKNOWN and got NO discovery at all, so its SWOT could never have
                   Opportunities/Threats.)

Contract: never raises, never returns None. On a skip, an engine failure, or an
empty result it returns a valid (possibly empty) PeerMatchResult — the SWOT layer
then degrades to a grounded standalone analysis. No competitor is ever fabricated:
the default web engine returns nothing rather than guessing peers from an LLM.
"""
from __future__ import annotations

import re
from typing import List, Optional, Protocol, runtime_checkable

from .business_type import BusinessType, classify_business_type
from .discovery import discover_competitors
from .models import CompetitorProfile, PeerMatchResult
from .peer_match import TARGET_PEERS


@runtime_checkable
class WebDiscoveryEngine(Protocol):
    """Pluggable web-based competitor discovery for ECOMMERCE / HYBRID brands.

    A real implementation (SERP / keyword / brand-similarity) MUST keep every
    returned peer evidence-grounded (sourced URL), exactly like the Places path.
    Until one is wired, NullWebDiscoveryEngine is used.
    """
    name: str

    def discover(self, profile, manifest=None) -> List[CompetitorProfile]: ...


class NullWebDiscoveryEngine:
    """Default web engine: none wired. Returns no peers (never fabricated)."""
    name = "null-web"

    def discover(self, profile, manifest=None) -> List[CompetitorProfile]:
        return []


# ---------------------------------------------------------------------------

def _empty(notes: List[str], considered: int = 0, passed: int = 0) -> PeerMatchResult:
    return PeerMatchResult([], True, considered, passed, list(notes), [])


def _domain(url: Optional[str]) -> Optional[str]:
    if not url:
        return None
    m = re.search(r"https?://([^/]+)", url.lower())
    host = (m.group(1) if m else url.lower()).removeprefix("www.")
    return host or None


def _dedup(competitors: List[CompetitorProfile]) -> List[CompetitorProfile]:
    """First-occurrence-wins across engines, keyed on place_id, else website
    domain, else normalized name."""
    seen = set()
    out: List[CompetitorProfile] = []
    for c in competitors:
        cand = getattr(c, "candidate", None)
        key = (getattr(cand, "place_id", None)
               or _domain(getattr(cand, "website", None))
               or (getattr(cand, "name", "") or "").strip().lower())
        if not key or key in seen:
            if not key:
                out.append(c)          # unkeyable -> keep (don't drop evidence)
            continue
        seen.add(key)
        out.append(c)
    return out


def _safe_web(engine: WebDiscoveryEngine, profile, manifest) -> List[CompetitorProfile]:
    try:
        return list(engine.discover(profile, manifest) or [])
    except Exception as exc:                          # never let an engine crash the run
        _safe_web.last_error = repr(exc)
        return []


def _places(profile, client, base_note: str, **kwargs) -> PeerMatchResult:
    if client is None:
        return _empty([base_note, "Places engine selected but no places_client supplied -> skipped."])
    try:
        res = discover_competitors(profile, client, **kwargs)
    except Exception as exc:                          # PlacesError etc. -> degrade safely
        return _empty([base_note, f"Places discovery failed safely: {exc!r}"])
    res.notes.insert(0, base_note)
    return res


def route_discovery(
    profile,
    *,
    places_client=None,
    manifest=None,
    web_engine: Optional[WebDiscoveryEngine] = None,
    **discovery_kwargs,
) -> PeerMatchResult:
    """Route to the engine(s) appropriate for the business type. See module docs."""
    web_engine = web_engine or NullWebDiscoveryEngine()
    cls = classify_business_type(profile, manifest)
    bt = cls.business_type
    base = f"business_type={bt.value} :: {cls.reasoning}"

    if bt is BusinessType.UNKNOWN:
        # No physical/ecom signal to route on, but the SERP web engine can still
        # find grounded category peers (e.g. a B2B services company). The Null
        # engine yields [] -> the SWOT degrades to standalone exactly as before.
        peers = _dedup(_safe_web(web_engine, profile, manifest))
        tail = (f"{len(peers)} web peer(s)" if peers
                else f"no peers (web engine '{web_engine.name}' wired no results); standalone analysis.")
        return PeerMatchResult(peers, len(peers) < TARGET_PEERS, 0, 0,
                               [base, f"UNKNOWN type -> web engine '{web_engine.name}': {tail}"], [])

    if bt is BusinessType.LOCAL:
        res = _places(profile, places_client, base, **discovery_kwargs)
        if not res.thin_peer_set:
            return res
        # MEASURED (Volume Qatar, 2026-07-21): LOCAL used to return here unconditionally, so a
        # café in The Pearl was locked to Places alone and shipped with ONE peer — a catering
        # company. nanocare's saved note reads "HYBRID merge: 0 Places + 4 web -> 4 after dedup":
        # every competitor it has came from the web engine. `thin_peer_set` was already computed
        # and acted on by nobody. A thin result now widens through the SAME merge the HYBRID
        # branch uses; a healthy one returns above, so working brands are unchanged.
        web_peers = _safe_web(web_engine, profile, manifest)
        if not web_peers:
            return res
        merged = _dedup(list(res.competitors) + web_peers)
        notes = list(res.notes) + [
            f"thin local tier ({len(res.competitors)} peer(s)) -> widened to web engine "
            f"'{web_engine.name}': +{len(web_peers)} -> {len(merged)} after dedup."
        ]
        return PeerMatchResult(merged, len(merged) < TARGET_PEERS,
                               res.candidates_considered, res.candidates_passed_filters,
                               notes, res.audit)

    if bt in (BusinessType.ECOMMERCE, BusinessType.REACH):
        # Both compete by category, not proximity -> web only. REACH (education / B2B /
        # professional / agency) would get universities + directories from Places; the SERP
        # engine's relevance judge finds true category peers instead.
        peers = _dedup(_safe_web(web_engine, profile, manifest))
        tail = (f"{len(peers)} web peer(s)" if peers
                else f"no peers (web engine '{web_engine.name}' wired no results); standalone analysis.")
        return PeerMatchResult(peers, len(peers) < TARGET_PEERS, 0, 0,
                               [base, f"{bt.value.upper()} -> web engine '{web_engine.name}': {tail}"], [])

    # HYBRID -> Places + web, merged
    places_res = _places(profile, places_client, base, **discovery_kwargs)
    web_peers = _safe_web(web_engine, profile, manifest)
    merged = _dedup(list(places_res.competitors) + web_peers)
    notes = list(places_res.notes) + [
        f"HYBRID merge: {len(places_res.competitors)} Places + {len(web_peers)} web "
        f"-> {len(merged)} after dedup."
    ]
    return PeerMatchResult(merged, len(merged) < TARGET_PEERS,
                           places_res.candidates_considered,
                           places_res.candidates_passed_filters, notes, places_res.audit)
