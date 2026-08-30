"""ONE-SHOT Meta Ad Library pull (PD-4: network pulls live ONLY here). Official Graph API,
free, but requires the owner's META_ADS_TOKEN (a Meta app access token with Ad Library API
access — https://www.facebook.com/ads/library/api). Snapshots land under runs/ad_snapshots/
(gitignored); convert/commit a sanitized fixture only if needed for tests.

Usage:
    python -m scripts.pull_meta_ads "El Dahan Grill" --brand-ref eldahan --country EG
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.parse
import urllib.request

from config_env import load_env

load_env()
from datetime import datetime, timezone
from pathlib import Path

_FIELDS = ("id,page_name,ad_delivery_start_time,ad_delivery_stop_time,"
           "publisher_platforms,ad_creative_bodies,ad_creative_link_captions,"
           "ad_creative_link_titles,ad_creative_link_descriptions,"
           "ad_snapshot_url,media_type")


def main() -> int:
    ap = argparse.ArgumentParser(description="Meta Ad Library pull (official API) — paginated, "
                                             "call-ledgered, brand-ref auto-derived.")
    ap.add_argument("query", help="competitor page name to search")
    ap.add_argument("--brand-ref", default=None,
                    help="snapshot filename ref (default: ref_slug(query) — aligns with the consumer)")
    ap.add_argument("--country", default="EG")
    ap.add_argument("--max", type=int, default=200, help="total ads cap across pages")
    args = ap.parse_args()

    token = os.environ.get("META_ADS_TOKEN")
    if not token:
        print("META_ADS_TOKEN is not set — the owner must create a Meta app token with "
              "Ad Library API access (free): https://www.facebook.com/ads/library/api")
        return 2

    from ads_intel.consumer import ref_slug
    from ads_intel.live import UrllibTransport, iter_competitor_ads
    from ads_intel.pull_ledger import check_calls, record_call
    brand_ref = args.brand_ref or ref_slug(args.query)

    rows: list = []
    basis = "keyword_legacy"
    transport = UrllibTransport()
    try:
        # ASK FOR THE ADVERTISER, NOT FOR THEIR WORDS. This used `iter_ad_pages`, whose
        # `search_terms` query searches AD COPY — so it returns whoever happens to use the words
        # and never the competitor. Measured over every snapshot this script produced: 260 ads,
        # zero of them a competitor's ("Vodafone Egypt" -> Vodafone Foundation, "Joe's cafe" ->
        # Goodnovel Lovers). `iter_competitor_ads` resolves the advertiser's page first and falls
        # back to keywords only when no page can be identified — and says which it did.
        for page, how in iter_competitor_ads(args.query, country=args.country, token=token,
                                             fields=_FIELDS, transport=transport,
                                             batch_size=100, hard_cap=max(1, args.max)):
            basis = str((how or {}).get("basis") or basis)
            ok, msg = check_calls(1)
            if not ok:
                print(f"[x] {msg}", file=sys.stderr)
                break
            record_call(f"ads_archive?q={args.query}", len(page))
            rows.extend(page)
            print(f"  … {len(rows)} ads so far  (basis: {basis})")
    except Exception as exc:  # noqa: BLE001 — fail-closed: keep the partial, never retry-storm
        print(f"[!] pull stopped early ({type(exc).__name__}) — keeping {len(rows)} ads",
              file=sys.stderr)

    snapshot = {
        "source": "meta_ad_library",
        "brand_ref": brand_ref,
        "query": args.query,
        "country": args.country,
        # WHAT QUESTION PRODUCED THESE ROWS. Without it, a file pulled by advertiser and a file
        # pulled by ad copy look identical on disk — and every snapshot written before this field
        # existed is the second kind. `ads_intel.orchestrator.snapshot_basis` reads absent as
        # `keyword_legacy`, so old noise can never be mistaken for evidence.
        "basis": basis,
        "pulled_at": datetime.now(timezone.utc).isoformat(),
        "ads": rows,
    }
    out = Path("runs") / "ad_snapshots" / f"{brand_ref}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"{len(rows)} active ad(s) -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
