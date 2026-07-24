"""CLI: build an N-day content calendar from a business profile.

    python -m strategy profile.json --days 30 --out plan.json
    python -m strategy profile.json --days 14 --platforms instagram,tiktok --trends
    python -m strategy profile.json --no-llm          # deterministic fallback plan
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def main() -> int:
    for _s in (sys.stdout, sys.stderr):
        try:
            _s.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        except Exception:
            pass
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except Exception:
        pass

    ap = argparse.ArgumentParser(prog="strategy", description="N-day content calendar.")
    ap.add_argument("profile", help="path to a business_profile.json")
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--out", default="outputs/content_plan.json")
    ap.add_argument("--platforms", default="", help="comma-separated (default: instagram,tiktok,linkedin)")
    ap.add_argument("--cadence", type=int, default=None,
                    help="posts per week (default: the brand category's cadence, media_plan.config)")
    ap.add_argument("--trends", action="store_true", help="ride current trends relevant to the brand")
    ap.add_argument("--no-llm", action="store_true", help="deterministic fallback plan (no LLM)")
    args = ap.parse_args()

    pp = Path(args.profile)
    if not pp.is_file():
        print(f"!! profile not found: {pp}", file=sys.stderr)
        return 2
    profile = json.loads(pp.read_text(encoding="utf-8"))

    caller = None
    if not args.no_llm:
        # CS-92: the LLM calendar path SPENDS (one live Gemini call). Refuse under a no-spend context
        # (pytest / BASEERA_NO_SPEND) so it can never bill by accident; --no-llm is the free path.
        from spend_tripwire import refuse_if_no_spend
        refuse_if_no_spend("one live Gemini call to plan the content calendar",
                           alternative="run with --no-llm for the deterministic plan")
        try:
            from business_profile.llm import default_caller
            # P-13: the content calendar is the product's core creative output — it deserves the
            # strong model, consistent with the poster's art direction (which uses strong=True).
            # It was on Flash ("planning is light"), a mismatch with its importance.
            caller = default_caller(strong=True)
        except Exception:
            caller = None

    trends = None
    if args.trends:
        try:
            from trends import top_trends, keywords_from_profile
            # D-8 (CS-62): the calendar rides the BRAND'S market trends, not Google's US default.
            from poster.locale import search_locale
            _gl, _hl = search_locale(profile)
            print("[strategy] fetching current trends...", file=sys.stderr)
            trends = top_trends(keywords_from_profile(profile), top_k=6, require_match=False,
                                gl=_gl, hl=_hl)
        except Exception:
            trends = None

    # Evidence-Ledger grounding gate (on by default in the live path): an unsourced
    # falsifiable hook/angle is blanked (drop-to-grounded). Best-effort — never blocks
    # the calendar if the grounding package is unavailable.
    ledger = None
    try:
        from grounding import EvidenceLedger
        ledger = EvidenceLedger.from_profile(profile)
    except Exception:
        ledger = None
    if ledger is not None:
        print("[strategy] grounding gate: on (unsourced hooks blanked)", file=sys.stderr)

    from strategy import build_strategy
    platforms = [p.strip() for p in args.platforms.split(",") if p.strip()] or None
    cal = build_strategy(profile, caller, days=args.days, platforms=platforms,
                         trends=trends, cadence_per_week=args.cadence, ledger=ledger)
    out = cal.save(args.out)
    print(f"[strategy] {len(cal.items)} items over {cal.days} days "
          f"(from {cal.start_date}) -> {out}")

    # Brand-safety audit trail (Step 3c): per-item (claim->source) proof + the gate's
    # remediation log + scoping, written as a sidecar next to the calendar JSON.
    if ledger is not None:
        try:
            from strategy.audit import build_calendar_audit
            audit = build_calendar_audit(profile, cal)
            apath = Path(args.out).with_suffix(".audit.json")
            apath.write_text(json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"[strategy] audit trail ({audit['items_remediated']} item(s) remediated) "
                  f"-> {apath}", file=sys.stderr)
        except Exception:
            pass
    for it in cal.items[:8]:
        hook = f" — \"{it.hook}\"" if it.hook else ""
        print(f"  {it.date}  {it.platform:10} {it.content_type:8} {it.topic}{hook}")
    if len(cal.items) > 8:
        print(f"  … +{len(cal.items) - 8} more")
    return 0


if __name__ == "__main__":
    sys.exit(main())
