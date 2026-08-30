"""ONE-SHOT capped Facebook pull (PD-4: network pulls live ONLY here, never in providers).

Drives the team's facebook_collector UNMODIFIED (Tier-2, Apify Actors only) behind the
owner's $20/month cap (on record 2026-07-12) via social_intel.spend_guard. The raw snapshot
(carries unhashed author names — PII) is written under runs/ (gitignored); convert it to a
committable fixture with scripts/convert_facebook_snapshot.py. NO recurring scheduling here.

Usage:
    python -m scripts.pull_facebook https://www.facebook.com/<page>/ --posts 5 --comments 20
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path


def main() -> int:
    ap = argparse.ArgumentParser(description="Capped one-shot Facebook pull via Apify.")
    ap.add_argument("url", help="Facebook Page URL (https://www.facebook.com/...)")
    ap.add_argument("--posts", type=int, default=5)
    ap.add_argument("--comments", type=int, default=20, help="max comments per post")
    ap.add_argument("--output", default=None,
                    help="snapshot path (default runs/social_snapshots/<page>_<ts>.json)")
    args = ap.parse_args()

    from social_intel.spend_guard import check_budget, record_pull
    # worst case the actors can bill for: 1 profile + posts + comments on every post
    worst_case = 1 + args.posts + args.posts * args.comments
    ok, msg = check_budget(worst_case)
    print(msg)
    if not ok:
        return 2

    # D-3 (owner ruling 2026-07-19): facebook_collector/ carried PII + a .env — moved OUT of the
    # project entirely (dev/_quarantine/facebook_collector). To run a live pull, point PYTHONPATH
    # at the quarantine dir explicitly; nothing inside this repo imports it anymore.
    import sys as _sys
    _q = r"C:\Users\Admin\Desktop\dev\_quarantine"
    if _q not in _sys.path:
        _sys.path.insert(0, _q)
    from facebook_collector import FacebookCollector   # imports apify_client; env has the token
    result = FacebookCollector().collect(
        facebook_url=args.url, posts_limit=args.posts,
        comments_limit_per_post=args.comments)

    summary = result.get("summary") or {}
    actual = 1 + int(summary.get("posts_collected") or 0) \
        + int(summary.get("comments_collected") or 0)
    row = record_pull(actual, url=args.url)
    print(f"recorded: {row['results']} results ~= ${row['est_cost_usd']:.2f} (ledger est)")

    if args.output:
        out = Path(args.output)
    else:
        page = re.sub(r"[^a-z0-9_]+", "_",
                      args.url.split("facebook.com/", 1)[-1].strip("/").lower()) or "page"
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        out = Path("runs") / "social_snapshots" / f"{page}_{ts}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False))
    print(f"snapshot (RAW, PII — stays out of git): {out}")
    print("next: python -m scripts.convert_facebook_snapshot", out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
