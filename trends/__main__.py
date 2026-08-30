"""CLI: show trends relevant to keywords or a business profile.

╔══════════════════════════════════════════════════════════════════════════════════════════╗
║ ⚠  THIS ENTRYPOINT SPENDS REAL MONEY. `SerperTrendSource` is FIRST in                    ║
║    `default_trend_sources` and calls the PAID serper.dev SERP API                        ║
║    (competitor/search_providers.py:14 — "paid credits"); load_dotenv below supplies      ║
║    SERPER_API_KEY. NOTE: trends/sources.py's "FREE, keyless public APIs" docstring       ║
║    predates Serper and is WRONG. There is no cap on this path today (unlike Apify's      ║
║    $20/mo and Vertex's $50/mo).                                                          ║
║    Refused automatically under pytest / BASEERA_NO_SPEND=1 (see spend_tripwire.py).      ║
║    To exercise the engine for free, inject fake sources — see tests/test_trends.py.      ║
╚══════════════════════════════════════════════════════════════════════════════════════════╝

    python -m trends "ai marketing automation"          # from keywords
    python -m trends --profile profile.json             # keywords from a profile
    python -m trends "jewelry" --require-match --top 5   # only on-topic trends
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
    # A comment is not a control — refuse mechanically before load_dotenv hands us a paid key.
    from spend_tripwire import refuse_if_no_spend
    refuse_if_no_spend("live paid Serper SERP calls (SerperTrendSource is first in "
                       "default_trend_sources)",
                       alternative="To exercise the engine for free, inject fake sources — see "
                                   "tests/test_trends.py.")
    # Load .env so the web (Serper) trend source finds SERPER_API_KEY — trends used to be keyless
    # (HN/Reddit/Dev.to), but the reliable consumer source now searches the SERP provider.
    try:
        from dotenv import load_dotenv
        load_dotenv(Path(__file__).resolve().parent.parent / ".env")
    except Exception:
        pass

    ap = argparse.ArgumentParser(prog="trends", description="Trending items for a business.")
    ap.add_argument("keywords", nargs="*", help="topic keywords (space-separated)")
    ap.add_argument("--profile", help="a business_profile.json to derive keywords from")
    ap.add_argument("--top", type=int, default=10, help="how many trends to show")
    ap.add_argument("--per-source", type=int, default=20, help="items fetched per source")
    ap.add_argument("--require-match", action="store_true",
                    help="only show trends that share a keyword with the business")
    args = ap.parse_args()

    from trends import top_trends, keywords_from_profile

    keywords = list(args.keywords)
    # D-8 (CS-62): keep the profile so the SERP can be aimed at the brand's market (gl/hl), not
    # Google's US/English default. Only a --profile run carries a country; a bare keyword run has
    # no brand, so gl/hl stay "" (omitted) — the honest no-market request.
    gl = hl = ""
    if args.profile:
        p = Path(args.profile)
        if not p.is_file():
            print(f"!! profile not found: {p}", file=sys.stderr)
            return 2
        _profile = json.loads(p.read_text(encoding="utf-8"))
        keywords += keywords_from_profile(_profile)
        try:
            from poster.locale import search_locale
            gl, hl = search_locale(_profile)
        except Exception:  # noqa: BLE001
            gl = hl = ""

    if not keywords:
        print("!! provide keywords or --profile", file=sys.stderr)
        return 2

    print(f"[trends] keywords: {', '.join(keywords[:12])}", file=sys.stderr)
    items = top_trends(keywords, top_k=args.top, limit_per_source=args.per_source,
                       require_match=args.require_match, gl=gl, hl=hl)
    if not items:
        print("(no trends — sources unreachable or no match)", file=sys.stderr)
        return 0
    for i, it in enumerate(items, 1):
        tag = (" [" + ", ".join(it.matched_terms) + "]") if it.matched_terms else ""
        print(f"{i:2}. ({it.trend_score:.2f} {it.source}){tag}  {it.title}")
        print(f"     {it.url}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
