# -*- coding: utf-8 -*-
"""Owner-authorized (2026-07-19: "ما تجيبهم الاتنين — معاك الـAPI") — pull a brand's OWN public
Instagram via the sanctioned Apify route (Tier-2, pay-per-result, NO login) and feed the
evidence-bound marketing-intel extractor directly. The old "owner-provided snapshot" handoff is
retired for brands with a known IG handle.

Guards: social_intel/spend_guard ($20/mo Apify cap, fail-closed ledger) + hard result caps +
BASEERA_NO_SPEND tripwire. PII: comment author names are NEVER persisted (hashed at ingestion,
PD-6); raw payloads land in runs/social_snapshots/ (gitignored).

    python -m scripts.pull_instagram_apify nano_care_pharma --brand-slug nanocareegypt_com
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

_ACTOR = "apify~instagram-scraper"
_RUN_SYNC = f"https://api.apify.com/v2/acts/{_ACTOR}/run-sync-get-dataset-items"


@dataclass(frozen=True)
class IgSignal:
    source_url: str
    text: str
    signal_kind: str = "post"
    # THE NUMBER THE WHOLE FEATURE NEEDED, and it was on disk the entire time.
    #
    # `social_intel/performance.py` reads five attributes off a signal; this carried three. So
    # `_brand`'s `ranked` list was empty for every input, `measured` was False, `checked` was
    # False, and `social_performance_for` COULD NOT return anything else no matter what was
    # pulled. Meanwhile `runs/social_snapshots/*.json` holds 15 posts with `likesCount` for four
    # brands, and `media_plan/maturity.py` has been reading that exact key successfully.
    #
    # None means UNMEASURED and must stay None — see the note in `signals_from_apify_items`.
    like_count: Optional[int] = None
    comment_count: Optional[int] = None
    posted_at: Optional[str] = None
    # Which brand this row belongs to, and whether it is the subject. Without these,
    # `performance.py:146` filed the brand's OWN posts under `peers` and the lessons layer
    # described them as "a competitor's top post".
    brand_ref: str = ""
    is_own_brand: bool = True


def _engagement(value) -> Optional[int]:
    """A count, or None for "we could not measure it" — never 0.

    `int(x or 0)` is the defect, not the fix. Apify emits -1 when a post's like count is hidden
    and omits the key entirely on a limited response; coercing either to 0 turns "unmeasured" into
    "this content gets zero likes", which is the same absence-read-as-a-value shape the whole
    module exists to correct. `top_performers` already splits ranked from unranked honestly — it
    just needs to be told the truth.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    v = int(value)
    return v if v >= 0 else None


def signals_from_apify_items(items: list, *, is_own_brand: bool = True,
                             brand_ref: str = "") -> list[IgSignal]:
    """Apify instagram-scraper rows -> extractor signals. Captions become 'post' signals,
    latestComments become 'comment' signals. Author names are DROPPED (never persisted);
    empty texts skipped. Pure + hermetic.

    `is_own_brand` / `brand_ref` label the rows so the ranker can separate the subject from a
    competitor. Defaults describe the only caller that exists today (the subject's own snapshot).
    """
    out: list[IgSignal] = []
    for it in (items or []):
        if not isinstance(it, dict):
            continue
        url = str(it.get("url") or it.get("shortCode") or "")
        ref = str(brand_ref or it.get("ownerUsername") or "").strip()
        cap = str(it.get("caption") or "").strip()
        if cap:
            out.append(IgSignal(
                source_url=url, text=cap[:1000], signal_kind="post",
                like_count=_engagement(it.get("likesCount")),
                comment_count=_engagement(it.get("commentsCount")),
                posted_at=str(it.get("timestamp") or "") or None,
                brand_ref=ref, is_own_brand=bool(is_own_brand)))
        for c in (it.get("latestComments") or []):
            txt = str((c or {}).get("text") or "").strip()
            if txt:
                # A COMMENT INHERITS NOTHING. Its parent post's like count measures the post;
                # attaching it here would rank a commenter's sentence as if it were the brand's
                # content. `top_performers` filters to published posts anyway, and leaving this
                # None keeps that filter honest rather than redundant.
                out.append(IgSignal(source_url=url, text=txt[:500], signal_kind="comment",
                                    brand_ref=ref, is_own_brand=bool(is_own_brand)))
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("username", help="the brand's public IG username (e.g. nano_care_pharma)")
    ap.add_argument("--brand-slug", required=True, help="outputs/<slug>_result.json to enrich")
    ap.add_argument("--posts", type=int, default=15)
    args = ap.parse_args()

    from spend_tripwire import refuse_if_no_spend
    refuse_if_no_spend("a paid Apify pull + one Gemini extraction call",
                       alternative="run the hermetic tests instead")

    from dotenv import load_dotenv
    load_dotenv(".env")
    from social_intel.tokens import apify_token
    token = apify_token()
    if not token:
        print("[x] APIFY_TOKEN missing from .env", file=sys.stderr)
        return 2

    from social_intel.spend_guard import check_budget, est_cost_usd, record_pull
    planned = max(1, min(args.posts, 30))
    ok, msg = check_budget(planned)
    if not ok:
        print(f"[x] spend guard refused: {msg}", file=sys.stderr)
        return 2

    import requests
    print(f"[ig] pulling {planned} posts of @{args.username} via Apify "
          f"(~${est_cost_usd(planned):.3f})…")
    resp = requests.post(
        _RUN_SYNC, params={"token": token},
        json={"directUrls": [f"https://www.instagram.com/{args.username}/"],
              "resultsType": "posts", "resultsLimit": planned,
              "addParentData": False},
        timeout=300)
    resp.raise_for_status()
    items = resp.json()
    record_pull(len(items) if isinstance(items, list) else 0,
                url=f"apify:{_ACTOR}:{args.username}")

    # raw payload (PII-bearing) -> gitignored snapshots dir, authors stripped first
    for it in items if isinstance(items, list) else []:
        for c in (it.get("latestComments") or []):
            c.pop("ownerUsername", None)
            c.pop("owner", None)
    snap_dir = Path("runs/social_snapshots")
    snap_dir.mkdir(parents=True, exist_ok=True)
    snap_p = snap_dir / f"{args.brand_slug}_ig.json"
    snap_p.write_text(json.dumps({"username": args.username,
                                  "pulled_at": datetime.now(timezone.utc).isoformat(),
                                  "items": items}, ensure_ascii=False, indent=2),
                      encoding="utf-8")

    signals = signals_from_apify_items(items)
    print(f"[ig] {len(items)} media -> {len(signals)} signals "
          f"({sum(1 for s in signals if s.signal_kind == 'comment')} comments)")
    if not signals:
        print("[!] no usable text signals — nothing to extract")
        return 0

    from business_profile.llm import default_caller
    from marketing_intel.extractor import extract_marketing_intel
    mi = extract_marketing_intel(signals, default_caller(strong=False),
                                 brand_ref=args.brand_slug)
    print(f"[intel] offers={len(mi.offers)} usps={len(mi.usps)} "
          f"pains={len(mi.pain_points)} rejected={mi.rejected}")

    result_p = Path("outputs") / f"{args.brand_slug}_result.json"
    if result_p.is_file():
        doc = json.loads(result_p.read_text(encoding="utf-8"))
        doc["marketing_intel"] = mi.model_dump(mode="json")
        result_p.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[ok] marketing_intel written into {result_p} — الداشبورد هتعرضها في أول تحميل")
    else:
        print(f"[!] {result_p} not found — extraction saved to snapshot only")
    return 0


if __name__ == "__main__":
    sys.exit(main())
