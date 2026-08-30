"""Pillar 1 — bottleneck pinpoint (READ-ONLY). Where does the crawl budget actually go on
e-commerce sites, and what page-types win the scarce budget? Confirms whether the lever is
render SPEED (time) or page SELECTION — before any change.

Run from the repo root:  python benchmark/measure_ecom_bottleneck.py

CAVEAT: saved scrapes may be old-budget (60s) — `total_s`/`stop-reason` reflect that. The
DURABLE signal here is the per-page render time (avg ~11.5s), which the budget version does
not change. See `scraper_pillar1_findings.md`.
"""
import glob
import json
import os
import re
from collections import Counter
from urllib.parse import urlsplit

PRODUCT_TOKENS = ("/product", "/products", "/shop", "/store", "/catalog", "/catalogue",
                  "/collection", "/collections", "best-seller", "bestseller", "new-arrival",
                  "/p/", "/category", "/categories", "منتج", "متجر", "تسوق", "مجموعات")
ECOM_MIN = 8


def base_site(d): return re.sub(r"_\d{8}_\d{6}$", "", d)


def nh(h):
    try:
        s = urlsplit((h or "").strip())
        return f"{s.netloc.lower()}{(s.path or '/').rstrip('/').lower() or '/'}"
    except Exception:
        return (h or "").lower()


def is_prod(h): return any(t in h.lower() for t in PRODUCT_TOKENS)


def main() -> None:
    latest = {}
    for mp in glob.glob("scrapes/*/manifest.json"):
        d = os.path.basename(os.path.dirname(mp)); s = base_site(d)
        if s not in latest or d > os.path.basename(os.path.dirname(latest[s])):
            latest[s] = mp

    ecom = []
    for site, mp in sorted(latest.items()):
        try:
            m = json.load(open(mp, encoding="utf-8"))
        except Exception:
            continue
        internal = (m.get("links") or {}).get("internal") or []
        uniq = {nh(l.get("href", "")) for l in internal if l.get("href")}; uniq.discard("")
        prod_links = {h for h in uniq if is_prod(h)}
        if len(prod_links) < ECOM_MIN:
            continue
        pages = m.get("pages") or []
        durs = [p.get("fetch_duration_ms", 0) for p in pages if p.get("fetch_duration_ms")]
        crawled_types = Counter(str(p.get("page_type")) for p in pages)
        crawled_urls = {nh(p.get("final_url") or p.get("url") or "") for p in pages}
        ecom.append(dict(
            site=site, pages=len(pages),
            total_s=(m.get("scrape_meta") or {}).get("duration_ms", 0) / 1000.0,
            avg_ms=(sum(durs) / len(durs)) if durs else 0, max_ms=max(durs) if durs else 0,
            prod_links=len(prod_links), prod_crawled=sum(1 for u in crawled_urls if is_prod(u)),
            top_types=", ".join(f"{t.split('.')[-1]}x{c}" for t, c in crawled_types.most_common(5)),
        ))

    print("=" * 110)
    print("PILLAR 1 BOTTLENECK — where the budget goes on e-commerce sites (READ-ONLY)")
    print("=" * 110)
    print(f"{'site':24} {'pages':>5} {'total_s':>7} {'avg_ms/pg':>9} {'max_ms':>7} "
          f"{'prodLk':>6} {'prodCrawl':>9}   crawled page-types")
    print("-" * 110)
    for r in ecom:
        print(f"{r['site'][:24]:24} {r['pages']:>5} {r['total_s']:>7.0f} {r['avg_ms']:>9.0f} "
              f"{r['max_ms']:>7.0f} {r['prod_links']:>6} {r['prod_crawled']:>9}   {r['top_types']}")
    if ecom:
        avg_pg = sum(r["avg_ms"] for r in ecom) / len(ecom)
        # CS-52: use the REAL live e-commerce budget from scraper.config, not a stale hardcoded 150s
        # (the old value). At 560s the budget fits ~50 pages, so the page CAP and the time budget
        # bind together — the honest conclusion is that BOTH matter, not "speed is the only lever".
        try:
            from scraper.config import ECOMMERCE_BUDGET_SECONDS as BUDGET_S
        except Exception:  # noqa: BLE001
            BUDGET_S = 560
        fits = BUDGET_S / (avg_pg / 1000) if avg_pg else 0
        print("-" * 110)
        print(f"AGGREGATE: avg per-page render = {avg_pg:.0f} ms (~{avg_pg/1000:.1f}s)  "
              f"-> the {BUDGET_S}s budget fits ~{fits:.0f} pages "
              f"(render SPEED and the page CAP bind together — both are levers for adaptive depth).")
        print(f"  product pages crawled across {len(ecom)} stores: {sum(r['prod_crawled'] for r in ecom)} "
              f"(of {sum(r['prod_links'] for r in ecom)} product links discovered).")


if __name__ == "__main__":
    main()
