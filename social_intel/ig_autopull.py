# -*- coding: utf-8 -*-
"""Owner-authorized IG autopull (2026-07-19 "اه — عمّميها"): inside every full analysis, when an
APIFY_TOKEN exists and the brand's own profile carries an Instagram URL, pull its public posts +
comments (Tier-2 Apify, pay-per-result, NO login) and run the evidence-bound marketing-intel
extraction — automatically, like the Meta ads autopull.

Guards mirror ads_intel.orchestrator: spend_guard ($20/mo cap, fail-closed ledger), hard post cap,
BASEERA_NO_SPEND + IG_AUTOPULL_DISABLED + pytest all block. PII: comment authors stripped before
anything persists. The network fetch is ONE injectable seam (hermetic tests use a fake)."""
from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

_ACTOR = "apify~instagram-scraper"
_RUN_SYNC = f"https://api.apify.com/v2/acts/{_ACTOR}/run-sync-get-dataset-items"


def ig_username_from_profile(profile: dict) -> Optional[str]:
    """The brand's OWN public IG username from its evidenced social_presence — or None."""
    for s in (profile or {}).get("social_presence") or []:
        if str((s or {}).get("platform") or "").lower() != "instagram":
            continue
        m = re.match(r"https?://(?:www\.)?instagram\.com/([A-Za-z0-9._]+)",
                     str(s.get("url") or ""))
        if m and m.group(1).lower() not in ("p", "reel", "stories", "explore"):
            return m.group(1)
    return None


def ig_autopull_enabled() -> bool:
    from .tokens import apify_token
    return bool(apify_token()) \
        and os.getenv("BASEERA_NO_SPEND") != "1" \
        and not os.getenv("IG_AUTOPULL_DISABLED") \
        and not os.getenv("PYTEST_CURRENT_TEST")


def _default_fetcher(username: str, posts: int, token: str) -> list:
    import requests
    resp = requests.post(
        _RUN_SYNC, params={"token": token},
        json={"directUrls": [f"https://www.instagram.com/{username}/"],
              "resultsType": "posts", "resultsLimit": posts, "addParentData": False},
        timeout=300)
    resp.raise_for_status()
    data = resp.json()
    return data if isinstance(data, list) else []


def extract_from_saved_snapshot(brand_slug: str, caller: Any, *,
                                snapshots_dir: str = "runs/social_snapshots",
                                log=print) -> Any:
    """Re-run the evidence-bound extraction from an ALREADY-SAVED IG snapshot — zero Apify
    spend (one cheap LLM call). The recovery path when a result rebuild wiped the intel."""
    p = Path(snapshots_dir) / f"{brand_slug}_ig.json"
    if not p.is_file():
        return None
    try:
        items = (json.loads(p.read_text(encoding="utf-8")) or {}).get("items") or []
    except Exception:  # noqa: BLE001
        return None
    from scripts.pull_instagram_apify import signals_from_apify_items
    signals = signals_from_apify_items(items)
    if not signals:
        return None
    from marketing_intel.extractor import extract_marketing_intel
    mi = extract_marketing_intel(signals, caller, brand_ref=brand_slug)
    log(f"[ig] re-extracted from saved snapshot: offers={len(mi.offers)} "
        f"pains={len(mi.pain_points)} rejected={mi.rejected}")
    return mi


def pull_ig_intel(profile: dict, brand_slug: str, caller: Any, *,
                  fetcher: Callable[[str, int, str], list] | None = None,
                  posts: int = 15, snapshots_dir: str = "runs/social_snapshots",
                  log=print) -> Any:
    """The full bridge: username -> guarded pull -> PII strip -> snapshot -> evidence-bound
    extraction. Returns a MarketingIntelligence or None (every failure degrades honestly —
    an IG hiccup must never break the analysis)."""
    username = ig_username_from_profile(profile)
    if not username:
        return None
    from .spend_guard import check_budget, est_cost_usd, record_pull
    from .tokens import apify_token
    token = apify_token() or ""
    planned = max(1, min(posts, 30))
    ok, msg = check_budget(planned)
    if not ok:
        log(f"[ig] spend guard refused: {msg}")
        return None
    try:
        log(f"[ig] autopull @{username}: {planned} posts (~${est_cost_usd(planned):.3f})")
        items = (fetcher or _default_fetcher)(username, planned, token)
    except Exception as exc:  # noqa: BLE001 — fail-closed, no retry storm
        log(f"[ig] pull failed ({type(exc).__name__}) — continuing without IG intel")
        return None
    record_pull(len(items), url=f"apify:{_ACTOR}:{username}")
    for it in items:                     # PII: authors never persist (PD-6)
        if isinstance(it, dict):
            for c in (it.get("latestComments") or []):
                if isinstance(c, dict):
                    c.pop("ownerUsername", None)
                    c.pop("owner", None)
    try:
        d = Path(snapshots_dir)
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{brand_slug}_ig.json").write_text(
            json.dumps({"username": username,
                        "pulled_at": datetime.now(timezone.utc).isoformat(),
                        "items": items}, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass
    from scripts.pull_instagram_apify import signals_from_apify_items
    signals = signals_from_apify_items(items)
    if not signals:
        log("[ig] no usable text signals")
        return None
    from marketing_intel.extractor import extract_marketing_intel
    mi = extract_marketing_intel(signals, caller, brand_ref=brand_slug)
    log(f"[ig] intel: offers={len(mi.offers)} usps={len(mi.usps)} "
        f"pains={len(mi.pain_points)} rejected={mi.rejected}")
    return mi
