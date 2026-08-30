# -*- coding: utf-8 -*-
"""ش1 — السحب الذكي التكيفي: batch sampling over the Ad Library with a CONFIDENCE early-stop.

Per competitor: pull ONE batch (20) → parse → deterministic facts (ش2) → evidence-bound intel
(ش3, one cheap call) → "enough evidence?" (≥3 offers OR ≥5 pain points OR ≥1 winner ad) → stop
(save the API budget) or pull the next batch, up to a HARD CAP (200/run). The pulls ledger is
checked BEFORE every fetch (fail-closed); every failure degrades to what was already collected.

Owner ruling (2026-07-19): FULL-AUTO when tokens exist — guarded by these caps + BASEERA_NO_SPEND
(pytest can never pull) + the ADS_AUTOPULL_DISABLED escape hatch.
"""
from __future__ import annotations

import json
import os

from config_env import load_env

load_env()   # a token in .env must be visible here, not only to a parent
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Optional

from .consumer import DEFAULT_SNAPSHOTS_DIR, ref_slug
from .live import (UrllibTransport, iter_competitor_ads, page_hint_from_website,
                   resolve_page_id)

# Which QUESTION produced a set of ads — and therefore what they are worth.
#
#   page_id         we resolved the advertiser and pulled THEIR page. Every row is provably theirs.
#   keyword         no page could be identified; rows are ad-COPY matches and only the relevance
#                   gate stands between them and the analysis.
#   keyword_legacy  a snapshot from before this distinction existed.
#
# MEASURED, and the reason this field has to exist: every file in `runs/ad_snapshots/` was pulled
# with `search_terms`, which searches ad copy rather than advertisers. 260 ads across the set and
# NOT ONE belongs to the competitor it was pulled for — "Vodafone Egypt" returned Vodafone
# Foundation, "Joe's cafe" returned Goodnovel Lovers. The relevance gate drops them all and is
# working correctly; the data was wrong. Once the puller is corrected those old files become
# indistinguishable from good ones unless they say what they are, so ABSENT reads as
# `keyword_legacy` — never as trustworthy.
_BASIS_LEGACY = "keyword_legacy"


def snapshot_basis(snap) -> str:
    """The basis a snapshot was pulled with. Absent -> the keyword era, i.e. do not trust it."""
    if not isinstance(snap, dict):
        return _BASIS_LEGACY
    value = str(snap.get("basis") or "").strip()
    return value if value in ("page_id", "keyword") else _BASIS_LEGACY
from .providers.meta_ad_library import ads_from_snapshot
from .pull_ledger import DEFAULT_LEDGER, check_calls, record_call
from .schemas import AdPresence, ad_presence

_FIELDS = ("id,page_name,page_id,ad_delivery_start_time,ad_delivery_stop_time,"
           "publisher_platforms,ad_creative_bodies,ad_creative_link_captions,"
           "ad_creative_link_titles,ad_creative_link_descriptions,"
           "ad_snapshot_url,media_type")


@dataclass(frozen=True)
class PullPolicy:
    batch_size: int = 20
    hard_cap: int = 200
    min_offers: int = 3
    min_pain_points: int = 5
    min_winner_ads: int = 1


def pull_policy() -> PullPolicy:
    e = os.getenv
    return PullPolicy(batch_size=int(e("ADS_PULL_BATCH", "20")),
                      hard_cap=int(e("ADS_PULL_HARD_CAP", "200")),
                      min_offers=int(e("ADS_CONF_MIN_OFFERS", "3")),
                      min_pain_points=int(e("ADS_CONF_MIN_PAINS", "5")),
                      min_winner_ads=int(e("ADS_CONF_MIN_WINNERS", "1")))


def enough_evidence(mi: Any, presence: Any, policy: PullPolicy) -> bool:
    """The confidence predicate — 'هل لقيت أدلة كافية؟'."""
    offers = len(getattr(mi, "offers", None) or []) if mi else 0
    pains = len(getattr(mi, "pain_points", None) or []) if mi else 0
    winners = int(getattr(presence, "winners_30d", 0) or 0) if presence else 0
    return (offers >= policy.min_offers or pains >= policy.min_pain_points
            or winners >= policy.min_winner_ads)


@dataclass
class CompetitorIntelBundle:
    ref: str
    ads_pulled: int = 0
    ads_filtered_out: int = 0    # pulled by the broad query but NOT the competitor's (1.1)
    batches: int = 0
    stopped: str = "exhausted"     # confidence | hard_cap | exhausted | error:<Type> | refused:<msg>
    presence: Optional[AdPresence] = None
    intel: Any = None
    raw_rows: list = field(default_factory=list)


def adaptive_pull_competitor(name: str, *, country: str, token: str, caller: Any,
                             transport: Any = None, policy: Optional[PullPolicy] = None,
                             snapshots_dir: str = DEFAULT_SNAPSHOTS_DIR, today: Optional[date] = None,
                             ledger: str = DEFAULT_LEDGER, website: str = "",
                             log=print) -> CompetitorIntelBundle:
    """One competitor's adaptive pull. ref auto-derives via ref_slug(name) — the snapshot always
    lines up with the consumer. Never raises; every failure lands in `stopped`."""
    policy = policy or pull_policy()
    transport = transport or UrllibTransport()
    today = today or datetime.now(timezone.utc).date()
    ref = ref_slug(name)
    bundle = CompetitorIntelBundle(ref=ref)
    query = name
    basis = _BASIS_LEGACY

    def _pages(q: str):
        """ADVERTISER FIRST, ad copy only as a fallback.

        This called `iter_ad_pages` — the keyword-only search — while `iter_competitor_ads`, which
        resolves the advertiser's page and pulls THAT page's ads, sat fully written and tested with
        no caller anywhere in production. The good code existed; nothing was plugged into it. The
        cost of that: 260 ads on disk, zero of them a competitor's.

        THE RESOLUTION IS A REAL API CALL AND IS BUDGETED AS ONE. Identifying the advertiser costs
        one extra request before the first page. `_run` guards each `next()` with a single
        `check_calls(1)`, so wiring this in naively would have spent two requests against one unit
        — an under-count in the one place this project cannot afford one. It is checked and
        recorded here, on its own, before the generator starts."""
        nonlocal basis
        page_id = None
        ok, _msg = check_calls(1, ledger=ledger)
        if ok:
            page_id = resolve_page_id(q, country=country, token=token, transport=transport)
            record_call(f"ads_archive?resolve={q}", 1 if page_id else 0, ledger=ledger)
        for rows, how in iter_competitor_ads(q, country=country, token=token, fields=_FIELDS,
                                             transport=transport, batch_size=policy.batch_size,
                                             hard_cap=policy.hard_cap, page_id=page_id):
            basis = str((how or {}).get("basis") or _BASIS_LEGACY)
            yield rows

    def _analyze() -> None:
        """Re-run ش2+ش3 over everything collected so far (cheap, in-memory). The relevance gate
        (1.1) lives in the parse chokepoint: facts + intel run over the COMPETITOR'S ads only,
        and the strangers the broad keyword query dragged in are counted, not analyzed."""
        snap = {"brand_ref": ref, "query": query, "website": website,
                "basis": basis,          # what question produced these rows — see `snapshot_basis`
                "pulled_at": datetime.now(timezone.utc).isoformat(),
                "ads": bundle.raw_rows}
        raw = ads_from_snapshot(snap, only_competitor=False)
        ads = ads_from_snapshot(snap)
        bundle.ads_filtered_out = len(raw) - len(ads)
        bundle.presence = ad_presence(ads, today=today) if ads else None
        if bundle.presence is not None:
            bundle.presence.filtered_out = bundle.ads_filtered_out
        try:
            from marketing_intel.from_ads import extract_competitor_intel
            bundle.intel = extract_competitor_intel(ads, caller, brand_ref=ref)
        except Exception:  # noqa: BLE001 — intel failure must not lose the pulled facts
            bundle.intel = None

    def _run(q: str) -> None:
        nonlocal query
        query = q                    # _analyze + the saved snapshot always see the LIVE query
        gen = _pages(q)
        while True:
            ok, msg = check_calls(1, ledger=ledger)
            if not ok:
                bundle.stopped = f"refused:{msg}"
                return
            try:
                rows = next(gen)
            except StopIteration:
                bundle.stopped = "exhausted"
                return
            except Exception as exc:  # noqa: BLE001 — fail-closed with the partial, no retry storm
                bundle.stopped = f"error:{type(exc).__name__}"
                return
            record_call(f"ads_archive?q={q}", len(rows), ledger=ledger)
            bundle.raw_rows.extend(rows)
            bundle.ads_pulled = len(bundle.raw_rows)
            bundle.batches += 1
            _analyze()
            if enough_evidence(bundle.intel, bundle.presence, policy):
                bundle.stopped = "confidence"
                return
            if bundle.ads_pulled >= policy.hard_cap:
                bundle.stopped = "hard_cap"
                return

    _run(query)
    if not bundle.raw_rows:
        hint = page_hint_from_website(website)
        if hint and hint.casefold() != query.casefold():
            log(f"[ads] 0 ads for '{query}' — retrying with the page handle '{hint}'")
            _run(hint)

    if bundle.raw_rows:
        dst = Path(snapshots_dir) / f"{ref}.json"
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_text(json.dumps({"brand_ref": ref,
                                   "pulled_at": datetime.now(timezone.utc).isoformat(),
                                   "query": query, "country": country, "website": website,
                                   "ads": bundle.raw_rows}, ensure_ascii=False, indent=2),
                       encoding="utf-8")
    log(f"[ads] {name}: {bundle.ads_pulled} ads in {bundle.batches} batch(es) ({bundle.ads_filtered_out} unrelated dropped) — {bundle.stopped}")
    return bundle


def autopull_enabled() -> bool:
    """FULL-AUTO per the owner's ruling — but NEVER under a no-spend context (pytest sets
    BASEERA_NO_SPEND=1) and always behind the ADS_AUTOPULL_DISABLED escape hatch."""
    return bool(os.getenv("META_ADS_TOKEN")) \
        and os.getenv("BASEERA_NO_SPEND") != "1" \
        and not os.getenv("ADS_AUTOPULL_DISABLED") \
        and not os.getenv("PYTEST_CURRENT_TEST")


def run_adaptive_pulls(competitors, *, profile: Any, caller: Any, log=print) -> dict:
    """All competitors' adaptive pulls. Country from the brand's own evidence (search_locale),
    'EG' as the disclosed default. Returns {ref: CompetitorIntelBundle}. Never raises."""
    token = os.getenv("META_ADS_TOKEN") or ""
    try:
        from poster.locale import search_locale
        country = (search_locale(profile)[0] or "eg").upper()
    except Exception:  # noqa: BLE001
        country = "EG"
    out: dict = {}
    for c in (competitors or []):
        name = str(getattr(getattr(c, "candidate", c), "name", None)
                   or (c.get("candidate", {}) or {}).get("name", "") if isinstance(c, dict)
                   else getattr(getattr(c, "candidate", c), "name", "") or "")
        website = str(getattr(getattr(c, "candidate", c), "website", None) or "")
        if not name.strip():
            continue
        try:
            out[ref_slug(name)] = adaptive_pull_competitor(
                name, country=country, token=token, caller=caller, website=website, log=log)
        except Exception as exc:  # noqa: BLE001 — one bad competitor never kills the run
            log(f"[ads] {name}: pull failed ({type(exc).__name__}) — continuing")
    return out
