"""Pillar 1 — MEASUREMENT ONLY (no fix): E-commerce page-coverage on saved scrapes.

Quantifies the gap the dynamic-crawl-budget fix must close: across saved scrapes, how many
internal pages did the crawler DISCOVER vs actually CRAWL, focused on E-COMMERCE sites
(detected UNIVERSALLY by product-pattern internal-link density — no hardcoded site names).

Run from the repo root:  python benchmark/measure_ecom_coverage.py

Reads `scrapes/*/manifest.json` (latest scrape per site). Current cap = MAX_INTERNAL_PAGES=12
(+ homepage); TOTAL_BUDGET_SECONDS=150. (The spec's "7" is stale — a prior commit raised it.)

CAVEAT: many saved scrapes were made with the OLD 60s budget / 7-page cap, so the numbers
OVERSTATE the current problem — re-run on fresh scrapes for a true reading. See
`scraper_pillar1_findings.md`.
"""
import glob
import json
import os
import re
from collections import Counter
from urllib.parse import urlsplit

# CS-51: read the REAL live cap from scraper.config — the hardcoded 12 was stale (the live cap is
# 40, and 50 for e-commerce), so the 'stop' column mislabelled almost every store as CAP-bound when
# the actual binding constraint was time/speed. Fall back to 40 only if the import ever fails.
try:
    from scraper.config import ECOMMERCE_MAX_INTERNAL_PAGES as CAP  # the e-commerce store cap (50)
except Exception:  # noqa: BLE001
    CAP = 40

# Product/shop URL tokens (mirrors scraper.config PAGE_TYPE_PATTERNS['products']) — the
# UNIVERSAL e-commerce signal: a store has many product/collection internal links.
PRODUCT_TOKENS = (
    "/product", "/products", "/shop", "/store", "/catalog", "/catalogue", "/collection",
    "/collections", "best-seller", "bestseller", "new-arrival", "/p/", "/pd/", "/dp/",
    "منتج", "متجر", "تسوق", "مجموعات", "/category", "/categories",
)
ECOM_MIN_PRODUCT_LINKS = 8   # >= this many distinct product-pattern internal links -> e-commerce


def base_site(dirname: str) -> str:
    return re.sub(r"_\d{8}_\d{6}$", "", dirname)


def norm_href(href: str) -> str:
    try:
        s = urlsplit((href or "").strip())
        path = (s.path or "/").rstrip("/") or "/"
        return f"{s.netloc.lower()}{path.lower()}"
    except Exception:
        return (href or "").strip().lower()


def is_product(h: str) -> bool:
    hl = h.lower()
    return any(t in hl for t in PRODUCT_TOKENS)


def main() -> None:
    latest: dict[str, str] = {}
    for mp in glob.glob("scrapes/*/manifest.json"):
        d = os.path.basename(os.path.dirname(mp))
        site = base_site(d)
        if site not in latest or d > os.path.basename(os.path.dirname(latest[site])):
            latest[site] = mp

    rows = []
    for site, mp in sorted(latest.items()):
        try:
            m = json.load(open(mp, encoding="utf-8"))
        except Exception as e:
            print(f"  ! skip {site}: {e}")
            continue
        meta = m.get("scrape_meta") or {}
        diag = m.get("scrape_diagnostics") or {}
        pages = m.get("pages") or []
        internal = (m.get("links") or {}).get("internal") or []
        uniq = {norm_href(l.get("href", "")) for l in internal if l.get("href")}
        uniq.discard("")
        uniq_prod = {h for h in uniq if is_product(h)}
        scraped = diag.get("pages_scraped") if diag else None
        if scraped is None:
            scraped = len(pages)
        rows.append(dict(
            site=site, scraped=scraped,
            discovered=(diag.get("pages_discovered", 0) if diag else 0),
            uniq_internal=len(uniq), prod_links=len(uniq_prod),
            sitemap=(diag.get("sitemap_urls_found", 0) if diag else 0),
            budget=bool(meta.get("budget_exceeded", False)), cap_hit=(scraped >= CAP),
        ))

    ecom = sorted([r for r in rows if r["prod_links"] >= ECOM_MIN_PRODUCT_LINKS],
                  key=lambda r: -r["uniq_internal"])
    non = [r for r in rows if r["prod_links"] < ECOM_MIN_PRODUCT_LINKS]

    def cov(r):
        return (100.0 * r["scraped"] / r["uniq_internal"]) if r["uniq_internal"] else float("nan")

    print("=" * 92)
    print(f"PILLAR 1 — E-commerce page coverage   (cap MAX_INTERNAL_PAGES={CAP}; saved data may be old-budget)")
    print(f"sites={len(rows)}  |  E-COMMERCE (prod-links >= {ECOM_MIN_PRODUCT_LINKS})={len(ecom)}")
    print("=" * 92)
    print(f"{'site':30} {'scraped':>7} {'uniqInt':>7} {'prodLk':>6} {'sitemap':>7} {'cov%':>6} {'stop':>5}")
    print("-" * 92)
    for r in ecom:
        stop = "CAP" if r["cap_hit"] else ("TIME" if r["budget"] else "-")
        print(f"{r['site'][:30]:30} {r['scraped']:>7} {r['uniq_internal']:>7} {r['prod_links']:>6} "
              f"{r['sitemap']:>7} {cov(r):>5.0f}% {stop:>5}")
    if ecom:
        ts, tu = sum(r["scraped"] for r in ecom), sum(r["uniq_internal"] for r in ecom)
        print("-" * 92)
        print(f"AGGREGATE e-commerce: {ts} of {tu} unique internal pages = {100.0*ts/max(1,tu):.1f}% coverage")
    if non:
        an = sum(cov(r) for r in non if r["uniq_internal"]) / max(1, len([r for r in non if r["uniq_internal"]]))
        print(f"\nnon-e-commerce avg coverage = {an:.1f}% (cap rarely binds there)")
    print("\nNOTE: read-only. cov% = scraped / unique-internal-links discovered.")


if __name__ == "__main__":
    main()
