"""Pickable PRODUCTS for the studio's product-picker, extracted from the RAW scrape (not the
profile — a brand's `content_images` turned out to be store-location photos, not products).

The owner/engineer want the user to CHOOSE which product/line to advertise, from all the scraped
data, then generate a poster + reel FOR that specific product. This module turns a scrape manifest
into a grounded list of {name, image, url}: the product LINES the crawl actually reached (Shopify
`/collections/<slug>` and `/products/<slug>` pages), each paired with a real product image from the
manifest's images-of-interest. Everything is GROUNDED — a name is a real page the crawl fetched, an
image is a real asset on the site; nothing invented. Empty list when no product page was reached.
"""
from __future__ import annotations

import glob
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

# Non-product images to never offer as a product thumbnail (banners, logos, icons, payment badges).
_IMG_SKIP = ("banner", "slide", "logo", "icon", "sprite", "payment", "visa", "mastercard",
             "placeholder", "favicon", "badge")


def _titleize(slug: str) -> str:
    """'shop-hair-care' -> 'Hair Care'; '/collections/b2g1-eid-offer' -> 'B2G1 Eid Offer'.
    1.6 (owner-caught on nanocare): Arabic slugs arrive percent-encoded — unquote FIRST, or the
    'name' is a 151-char '%D9%86...' string that overflows every card."""
    from urllib.parse import unquote
    s = re.sub(r"[-_]+", " ", unquote(slug)).strip()
    s = re.sub(r"\b(shop|collection|collections|all|the)\b", "", s, flags=re.I).strip()
    return " ".join(w.upper() if (w.isupper() or re.fullmatch(r"[a-z]?\d.*", w)) else w.capitalize()
                    for w in s.split()) or slug


def _line_name_from_url(url: str) -> str | None:
    """A product-LINE name from a Shopify-style /collections/<slug> or /products/<slug> URL.
    None for a decoded slug that is still >80 chars — that is a sentence, not a name."""
    path = urlparse(url).path.rstrip("/")
    m = re.search(r"/(?:collections|products|product-category|shop)/([^/]+)$", path)
    if not m:
        return None
    name = _titleize(m.group(1))
    return name if len(name) <= 80 else None


def _norm_key(name: str) -> str:
    """Dedup key: same product however punctuated/spaced/encoded ('D3 / (MK7) K2' == 'D3 Mk7 K2')."""
    return re.sub(r"[\W_]+", "", str(name or ""), flags=re.UNICODE).casefold()


def _content_images(manifest: dict) -> list[dict]:
    """Real product-ish images from the manifest's images_of_interest (role=content, not chrome)."""
    out = []
    for im in (manifest.get("images_of_interest") or []):
        if not isinstance(im, dict):
            continue
        if im.get("role") not in (None, "content", "product"):
            continue
        src = str(im.get("src") or "")
        low = (src + " " + str(im.get("alt") or "")).lower()
        if not src.startswith("http") or any(k in low for k in _IMG_SKIP):
            continue
        out.append({"src": src, "alt": str(im.get("alt") or ""),
                    "page": str(im.get("page_url") or "")})
    return out


def _best_image(name: str, images: list[dict], used: set, item_url: str = "") -> str:
    """FIX-R3 (op-shoes: a SNEAKER image landed on every category incl. 'الحقائب', then the
    poster/reel inherited it): grounded matching only. (1) an image found ON the item's own
    page wins (language-proof — Shopify CDN hashes carry no words); (2) else an alt/filename
    word match; (3) else NO image — an honest empty beats a wrong sneaker, and downstream
    generation treats empty as 'feature by name only', never a random unused photo."""
    if item_url:
        target = item_url.rstrip("/").lower()
        for im in images:
            page = (im.get("page") or "").rstrip("/").lower()
            if page and page == target and im["src"] not in used:
                used.add(im["src"])
                return im["src"]
    words = [w for w in re.findall(r"[\w؀-ۿ]+", name.lower()) if len(w) >= 3]
    for im in images:
        blob = (im["src"] + " " + im["alt"]).lower()
        if im["src"] not in used and any(w in blob for w in words):
            used.add(im["src"])
            return im["src"]
    return ""


# Offering-type pages whose LEAF entries are pickable products (FIX-A: universal, not Shopify-only)
_OFFERING_PAGE_TYPES = {"products", "courses", "services", "menu", "programs", "pricing"}


def _first_heading(page: dict) -> str:
    """The page's own display name: its first h1/h2 text block (a course page's title), trimmed."""
    for b in (page.get("text_blocks") or []):
        if isinstance(b, dict) and str(b.get("tag") or "").lower() in ("h1", "h2"):
            t = str(b.get("text") or "").strip()
            if 2 <= len(t) <= 90:
                return t
    return ""


def _offering_products(profile: dict | None, images: list[dict], used: set, seen: set,
                       limit: int) -> list[dict]:
    """Pickable products from the PROFILE's extracted offerings — the best-named source (real
    item names + prices, any vertical), which the picker used to ignore entirely."""
    out: list[dict] = []
    for o in ((profile or {}).get("offerings") or []):
        if not isinstance(o, dict):
            continue
        name = o.get("name")
        if isinstance(name, dict):
            name = name.get("value")
        name = str(name or "").strip()
        key = _norm_key(name)
        if not name or key in seen:
            continue
        price = o.get("price_text")
        entry = {"name": name[:90], "url": str(o.get("page_url") or ""),
                 "image": _best_image(name, images, used, str(o.get("page_url") or "")),
                 "price_text": price if isinstance(price, str) and price.strip() else None}
        seen[key] = entry
        out.append(entry)
        if len(out) >= limit:
            break
    return out


def products_from_manifest(manifest: dict, *, limit: int = 12,
                           profile: dict | None = None) -> list[dict]:
    """Grounded pickable products: [{name, image, url, price_text?}]. Sources in priority order
    (FIX-A — the old picker understood ONLY Shopify URL slugs and returned [] for education/
    services/clinics, verified on nti):
      1. the profile's extracted offerings (real names + prices, every vertical);
      2. manifest pages of an offering type that are LEAF items, named by their own h1/h2;
      3. the Shopify-style URL-slug rule (existing behavior, kept as the fallback)."""
    images = _content_images(manifest)
    seen: dict[str, dict] = {}          # norm-key -> the entry (for merge/enrich)
    used: set[str] = set()
    out: list[dict] = _offering_products(profile, images, used, seen, limit)

    if len(out) < limit:
        try:
            from scraper.classify.page_type import is_leaf_detail  # lazy: no heavy deps
        except Exception:  # noqa: BLE001
            def is_leaf_detail(_u: str) -> bool:
                return False
        for page in (manifest.get("pages") or []):
            if len(out) >= limit:
                break
            url = page.get("final_url") or page.get("url") or ""
            if not is_leaf_detail(url):
                continue
            pt = str(page.get("page_type") or "")
            # an offering-typed leaf always qualifies; an 'other'-typed page qualifies only
            # when BOTH its URL is offering-shaped (is_leaf_detail above) AND it announces
            # itself with a heading (nti's JS-modal course pages classify as 'other')
            if pt not in _OFFERING_PAGE_TYPES and not (pt == "other" and _first_heading(page)):
                continue
            name = _first_heading(page) or _line_name_from_url(url) or ""
            if not name:
                continue
            key = _norm_key(name)
            if key in seen:
                prev = seen[key]                 # same product from the offerings — ENRICH it
                prev["url"] = prev["url"] or url
                prev["image"] = prev["image"] or _best_image(prev["name"], images, used, url)
                continue
            entry = {"name": name, "url": url, "image": _best_image(name, images, used, url)}
            seen[key] = entry
            out.append(entry)

    for page in (manifest.get("pages") or []):
        if len(out) >= limit:
            break
        url = page.get("final_url") or page.get("url") or ""
        name = _line_name_from_url(url)
        if not name:
            continue
        key = _norm_key(name)
        if key in seen:
            prev = seen[key]                     # duplicate of a priced offering — ENRICH it
            prev["url"] = prev["url"] or url
            prev["image"] = prev["image"] or _best_image(prev["name"], images, used, url)
            continue
        entry = {"name": name, "url": url, "image": _best_image(name, images, used, url)}
        seen[key] = entry
        out.append(entry)
    return out


def _latest_manifest(slug: str, scrapes_dir: str = "scrapes") -> Path | None:
    """The freshest saved scrape manifest for a brand slug (scrapes/<slug>_<ts>/manifest.json)."""
    hits = glob.glob(os.path.join(scrapes_dir, f"{slug}_*", "manifest.json"))
    if not hits:
        return None
    return Path(max(hits, key=os.path.getmtime))


def _profile_for_slug(slug: str, scrapes_dir: str, out_dir: str) -> dict | None:
    """The FRESHEST extracted profile for a brand (by file mtime): the studio's
    outputs/<slug>_profile.json vs the scrape-dir sibling business_profile.json — a re-scraped
    + re-extracted brand must not keep serving last week's offerings. None when neither exists."""
    candidates = [Path(out_dir) / f"{slug}_profile.json"]
    mp = _latest_manifest(slug, scrapes_dir)
    if mp:
        candidates.append(mp.parent / "business_profile.json")
    existing = [p for p in candidates if p.is_file()]
    existing.sort(key=lambda p: os.path.getmtime(p), reverse=True)
    for p in existing:
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data.get("profile") if isinstance(data.get("profile"), dict) else data
        except Exception:  # noqa: BLE001
            continue
    return None


def products_for_slug(slug: str, *, scrapes_dir: str = "scrapes", limit: int = 12,
                      out_dir: str = "outputs") -> list[dict]:
    """Pickable products for a brand slug — freshest manifest + extracted profile offerings.
    [] if no scrape exists/unreadable."""
    mp = _latest_manifest(slug, scrapes_dir)
    if not mp:
        return []
    try:
        return products_from_manifest(json.loads(mp.read_text(encoding="utf-8")), limit=limit,
                                      profile=_profile_for_slug(slug, scrapes_dir, out_dir))
    except Exception:
        return []


def _name_words(name: str) -> list[str]:
    """The product name's significant words (>=3 chars), lowercase — the matching key everywhere."""
    return [w for w in re.findall(r"[\w؀-ۿ]+", (name or "").lower()) if len(w) >= 3]


def save_product_scrape(slug: str, product_name: str, *, product_image: str | None = None,
                        kind: str = "", scrapes_dir: str = "scrapes",
                        out_asset: str = "") -> Path | None:
    """OWNER FEATURE: the moment a product-specific ad is generated, persist a PRODUCT-ONLY scrape
    snapshot into the brand's scrape dir — `product_scrape_<product>.json` — so the owner can open
    the scrapes folder and see exactly how far the crawl reached for THAT product: which pages
    matched it, which images, which priced offerings, how many text blocks mention it. Everything
    is read from the existing manifest + profile (grounded, nothing invented). Never raises; None
    when the brand has no saved scrape."""
    mp = _latest_manifest(slug, scrapes_dir)
    if not mp:
        return None
    try:
        m = json.loads(mp.read_text(encoding="utf-8"))
    except Exception:
        return None
    words = _name_words(product_name)

    # pages the crawl fetched that MATCH the product (url or any text block mentions it)
    pages_matched, mention_blocks = [], 0
    for p in (m.get("pages") or []):
        if not isinstance(p, dict):
            continue
        url = str(p.get("final_url") or p.get("url") or "")
        blocks = [str(b.get("text") or "") if isinstance(b, dict) else str(b)
                  for b in (p.get("text_blocks") or [])]
        hits = sum(1 for t in blocks if any(w in t.lower() for w in words))
        mention_blocks += hits
        url_match = any(w in url.lower() for w in words)
        if url_match or hits:
            pages_matched.append({"url": url, "url_match": url_match,
                                  "blocks_mentioning_product": hits, "total_blocks": len(blocks)})

    images_matched = [im for im in _content_images(m)
                      if any(w in (im["src"] + " " + im["alt"]).lower() for w in words)]

    # priced offerings from the profile that match the product name
    offerings_matched = []
    prof_path = mp.parent / "business_profile.json"
    if prof_path.is_file():
        try:
            prof = json.loads(prof_path.read_text(encoding="utf-8"))
            for o in (prof.get("offerings") or []):
                nm = str((o.get("name") if isinstance(o, dict) else "") or "")
                if any(w in nm.lower() for w in words):
                    offerings_matched.append({"name": nm,
                                              "price_text": o.get("price_text"),
                                              "page_url": o.get("page_url")})
        except Exception:
            pass

    snapshot = {
        "_doc": "Product-only scrape snapshot — written when a product-specific ad is generated, "
                "so the owner can see how far the crawl reached for THIS product. Grounded: every "
                "entry is a real crawled page / real site image / extracted offering.",
        "product": {"name": product_name, "image": product_image or "",
                    "picked_from": str(mp)},
        "generation": {"kind": kind, "requested_at": datetime.now(timezone.utc).isoformat(),
                       "out_asset": out_asset},
        "coverage": {
            "reached_product_page": any(p["url_match"] for p in pages_matched),
            "text_blocks_mentioning_product": mention_blocks,
            "images_matched": len(images_matched),
            "priced_offering_found": any(o.get("price_text") for o in offerings_matched),
        },
        "pages_matched": pages_matched[:25],
        "images_matched": images_matched[:12],
        "offerings_matched": offerings_matched[:12],
    }
    fname = "product_scrape_" + (re.sub(r"[^\w؀-ۿ]+", "-", product_name.lower()).strip("-")
                                 or "product") + ".json"
    out_path = mp.parent / fname
    try:
        out_path.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        return None
    return out_path


# Notes worth surfacing in the Scraper-QA panel (capabilities/limits the owner cares about).
_QA_NOTE_KEYS = ("ajax_modal", "e-commerce", "ecommerce", "product", "budget", "sitemap",
                 "root_anchor", "deep", "readiness")


def scrape_qa_for_slug(slug: str, *, scrapes_dir: str = "scrapes", out_dir: str = "outputs") -> dict | None:
    """A compact QUALITY summary of a brand's freshest scrape — so the owner can verify the scraper
    at a glance (pages, products, images, text coverage, readiness, and the notable notes such as the
    url-less-modal resolution). None when there is no manifest. Never raises."""
    mp = _latest_manifest(slug, scrapes_dir)
    if not mp:
        return None
    try:
        m = json.loads(mp.read_text(encoding="utf-8"))
    except Exception:
        return None
    sm = m.get("scrape_meta") or {}
    rd = m.get("readiness") or {}
    pages = m.get("pages") or []
    notes = [n for n in (m.get("notes") or []) if isinstance(n, str)]
    text_blocks = sum(len(p.get("text_blocks") or []) for p in pages if isinstance(p, dict))
    # CS-104: the QA "Products" count MUST use the SAME rule as the studio picker
    # (`products_for_slug` :203 passes the profile). Without the profile this count ignored the
    # profile's extracted OFFERINGS (source #1), so education/services/clinic brands with no Shopify
    # URL slugs reported 0 products while the picker offered 12-13 (22/90 brands, incl. KFC/topshoes).
    products = products_from_manifest(m, limit=100,
                                      profile=_profile_for_slug(slug, scrapes_dir, out_dir))
    # AGGREGATE the per-category ajax_modal_details lines (owner 2026-07-12: 10 stacked pink
    # URL rows = "عبث بصري") into ONE human summary; the raw lines stay available under
    # ajax_detail_lines for a details toggle. Other notable notes pass through unchanged.
    import re as _re
    ajax_lines = [n for n in notes if "ajax_modal_details" in n]
    other = [n for n in notes if "ajax_modal_details" not in n]
    key_notes = [n for n in other if any(k in n.lower() for k in _QA_NOTE_KEYS)]
    if ajax_lines:
        total = sum(int(m2.group(1)) for n in ajax_lines
                    for m2 in [_re.search(r"\+(\d+) JS-built", n)] if m2)
        key_notes.insert(0, f"JS-modal detail pages resolved: +{total} URLs "
                            f"across {len(ajax_lines)} page(s)")
    return {
        "manifest_path": str(mp),
        "final_url": sm.get("final_url") or "",
        "pages_attempted": int(sm.get("pages_attempted") or 0),
        "pages_succeeded": int(sm.get("pages_succeeded") or 0),
        "duration_ms": int(sm.get("duration_ms") or 0),
        "budget_exceeded": bool(sm.get("budget_exceeded")),
        "ready": bool(rd.get("ready_for_extraction")),
        "major_missing": list(rd.get("major_missing") or [])[:8],
        "text_blocks": text_blocks,
        "images": len(m.get("images_of_interest") or []),
        "products": len(products),
        "failures": len(m.get("failures") or []),
        "pages": [{"url": (p.get("url") or p.get("final_url") or ""),
                   "blocks": len(p.get("text_blocks") or [])}
                  for p in pages if isinstance(p, dict)][:25],
        "key_notes": key_notes[:10],
        "ajax_detail_lines": ajax_lines[:12],
    }
