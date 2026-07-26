# -*- coding: utf-8 -*-
"""A8 (2026-07-18): the ads_intel engine (provider + ad_presence + snapshot_parser) was fully built
and tested but NOTHING in production called it — and dashboard/build.py hardcoded a "blocked on the
Meta identity confirmation" gap UNCONDITIONALLY, so even a real snapshot changed nothing. Wire a
production CONSUMER that reads saved snapshots (runs/ad_snapshots/) and aggregates ad-presence; make
the SWOT threats and the dashboard gap CONDITIONAL on it. Hermetic — a snapshot fixture on disk, no
network, no token (the live pull stays owner-gated in scripts/pull_meta_ads.py)."""
from __future__ import annotations

import json
from datetime import date


def _write_snapshot(tmp_path, ref, ads):
    p = tmp_path / f"{ref}.json"
    p.write_text(json.dumps({"brand_ref": ref, "pulled_at": "2026-07-18T00:00:00Z", "ads": ads}),
                 encoding="utf-8")
    return p


def test_consumer_aggregates_present_snapshots_and_degrades_honestly(tmp_path):
    from ads_intel import ad_presence_for_refs
    _write_snapshot(tmp_path, "rival_a", [
        {"id": "1", "page_name": "Rival A", "publisher_platforms": ["facebook", "instagram"],
         "ad_delivery_start_time": "2026-01-01", "media_type": "video"},   # long-running -> proven
        {"id": "2", "page_name": "Rival A", "publisher_platforms": ["facebook"],
         "ad_delivery_start_time": "2026-07-10", "media_type": "image"},
    ])
    pres = ad_presence_for_refs(["rival_a", "rival_b"], snapshots_dir=str(tmp_path),
                                today=date(2026, 7, 18))
    assert len(pres) == 1                                 # rival_b has no snapshot -> skipped
    ap = pres[0]
    assert ap.brand_ref == "rival_a" and ap.active_count == 2
    assert ap.proven_winners == 1 and ap.longest_days >= 180

    # no snapshots anywhere -> honest empty (NOT an error)
    assert ad_presence_for_refs(["nobody"], snapshots_dir=str(tmp_path)) == []
    assert ad_presence_for_refs([], snapshots_dir=str(tmp_path)) == []


