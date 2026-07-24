# -*- coding: utf-8 -*-
"""Owner CLI — extract evidence-bound competitor intel from a SAVED ad snapshot (parse-only feed;
the ONE Gemini call is the paid part, so the spend tripwire guards it).

    python -m scripts.extract_competitor_intel <brand_ref> [--snapshots runs/ad_snapshots]
                                               [--out runs/competitor_intel]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def main() -> int:
    ap = argparse.ArgumentParser(description="Evidence-bound competitor intel from a saved snapshot.")
    ap.add_argument("brand_ref")
    ap.add_argument("--snapshots", default="runs/ad_snapshots")
    ap.add_argument("--out", default="runs/competitor_intel")
    args = ap.parse_args()

    from spend_tripwire import refuse_if_no_spend
    refuse_if_no_spend("one live Gemini call to extract competitor marketing intel",
                       alternative="run the hermetic tests (MockCaller) instead")

    from ads_intel.providers.meta_ad_library import MetaAdLibraryProvider
    ads = MetaAdLibraryProvider(snapshots_dir=args.snapshots).fetch(args.brand_ref)
    if not ads:
        print(f"[x] no snapshot for '{args.brand_ref}' under {args.snapshots}", file=sys.stderr)
        return 2

    from business_profile.llm import default_caller
    from marketing_intel.from_ads import extract_competitor_intel
    mi = extract_competitor_intel(ads, default_caller(strong=False), brand_ref=args.brand_ref)
    out = Path(args.out) / f"{args.brand_ref}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(mi.model_dump_json(indent=2), encoding="utf-8")
    print(f"[ok] offers={len(mi.offers)} usps={len(mi.usps)} pains={len(mi.pain_points)} "
          f"angles={len(mi.message_angles)} rejected={mi.rejected} -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
