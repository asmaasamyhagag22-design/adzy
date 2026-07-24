"""Production CONSUMER for ads-intel (A8): read SAVED Meta Ad Library snapshots for a run's
competitors and aggregate ad-presence. This is the piece that was missing — the provider,
`ad_presence`, and the SWOT/advisor seams all existed, but nothing in production called them, so a
real snapshot changed nothing and the dashboard hardcoded "blocked on the Meta identity".

Governance unchanged: PARSE-ONLY. The live network pull stays owner-gated in
scripts/pull_meta_ads.py behind META_ADS_TOKEN; this reads whatever snapshots are already on disk
(runs/ad_snapshots/<ref>.json). No snapshot -> honest empty (never an error, never a fabrication).
"""
from __future__ import annotations

import re
from datetime import date
from typing import List, Optional

from .providers.meta_ad_library import MetaAdLibraryProvider
from .schemas import AdPresence, ad_presence

# Where the owner's pull script writes snapshots (gitignored; see scripts/pull_meta_ads.py).
DEFAULT_SNAPSHOTS_DIR = "runs/ad_snapshots"


def ref_slug(name: str) -> str:
    """A snapshot-file ref from a competitor's public name: lower-cased, non-alphanumerics -> '_'.
    The pull script names snapshots by the same slug, so `<ref>.json` lines up."""
    return re.sub(r"[^a-z0-9]+", "_", str(name or "").strip().lower()).strip("_")


def ad_presence_for_refs(refs, *, snapshots_dir: Optional[str] = None,
                         today: Optional[date] = None) -> List[AdPresence]:
    """For each competitor ref, read its saved Meta Ad Library snapshot and aggregate ad-presence.
    Returns ONE AdPresence per ref that actually has active ads; refs with no snapshot (or no
    active ads) are skipped. Empty list == honest "no ad intelligence yet" (the live pull is
    owner-gated). Never raises."""
    import json
    from pathlib import Path

    from .providers.meta_ad_library import ads_from_snapshot

    sdir = Path(snapshots_dir or DEFAULT_SNAPSHOTS_DIR)
    prov = MetaAdLibraryProvider(snapshots_dir=str(sdir))
    out: List[AdPresence] = []
    for ref in (refs or []):
        ref = str(ref or "").strip()
        if not ref:
            continue
        filtered_out = 0
        try:
            # Raw pull snapshots go through the parse chokepoint twice so the 1.1 relevance
            # gate's drop-count is surfaced ("سحبنا X — Y مش بتوع المنافس"), not hidden.
            path = sdir / f"{ref}.json"
            data = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None
            if isinstance(data, dict) and "ads" in data:
                ads = ads_from_snapshot(data)
                filtered_out = len(ads_from_snapshot(data, only_competitor=False)) - len(ads)
            else:
                ads = prov.fetch(ref)                     # sanitized fixture rows path
        except Exception:  # noqa: BLE001 — a bad snapshot must not break the analysis
            ads = []
        if not ads:
            continue
        ap = ad_presence(ads, today=today)
        ap.filtered_out = filtered_out
        if ap.active_count > 0:
            out.append(ap)
    return out
