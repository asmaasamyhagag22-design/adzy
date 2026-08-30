"""Scrape ONE product from its direct URL — for a product the crawl never reached.

The owner: "if the user wants an ad/poster for a specific product that isn't in the scrape,
they give the product's link and we scrape THAT page — its image and info." The full crawler
deliberately re-anchors a deep URL to the brand homepage (url_utils.site_root_if_deep, so the
brand profile isn't hijacked by one deep page); this is the OPPOSITE need — fetch exactly the
given product page and pull the ONE product's name / image / price / description.

`parse_product(html, url)` is pure and hermetically testable (JSON-LD/schema.org Product first,
then og:/meta/<h1> fallback). `scrape_product_page(url)` fetches then parses. Never raises."""
from __future__ import annotations

import logging
from typing import Any, Optional
from urllib.parse import urljoin

from pydantic import BaseModel

from scraper.extractors.metadata import extract_metadata
from scraper.extractors.structured_data import extract_structured_data

logger = logging.getLogger(__name__)

_PRODUCT_TYPES = {"product", "productmodel", "individualproduct", "productgroup", "itempage"}


class ProductInfo(BaseModel):
    name: str = ""
    image: str = ""
    # EVERY candidate image the page shows, best-first. MEASURED (Al Fouad × La Roche,
    # 2026-07-26): `image` is the og:image, and for this product that is a composed marketing
    # BANNER — gradient ground, its own heart prop — which cannot be keyed into a cut-out, so
    # the poster shipped with no product at all. The gallery holds clean pack shots; nothing
    # ever looked at them.
    images: list[str] = []
    url: str = ""
    description: str = ""
    price: str = ""
    currency: str = ""

    @property
    def usable(self) -> bool:
        """A result is only useful if we recovered at least a name or an image."""
        return bool(self.name or self.image)


def _first(v: Any) -> str:
    """First scalar string from a JSON-LD value that may be a str / number / list / ImageObject."""
    if v is None:
        return ""
    if isinstance(v, str):
        return v.strip()
    if isinstance(v, (int, float)):
        return str(v)
    if isinstance(v, dict):
        for k in ("url", "@value", "contentUrl", "name", "@id"):
            got = _first(v.get(k))
            if got:
                return got
        return ""
    if isinstance(v, (list, tuple)):
        for item in v:
            got = _first(item)
            if got:
                return got
    return ""


def _image_of(data: dict, base: str) -> str:
    img = _first(data.get("image"))
    return urljoin(base, img) if img else ""


def _price_of(data: dict) -> tuple[str, str]:
    offers = data.get("offers")
    cand = offers[0] if isinstance(offers, list) and offers else offers
    if isinstance(cand, dict):
        price = _first(cand.get("price") or cand.get("lowPrice") or cand.get("highPrice"))
        return price, _first(cand.get("priceCurrency"))
    return "", ""


def _type_is_product(node: dict) -> bool:
    t = node.get("@type")
    types = t if isinstance(t, list) else [t]
    return any(isinstance(x, str) and x.strip().lower() in _PRODUCT_TYPES for x in types)


def _walk(obj: Any):
    """Yield every dict node, descending into lists and JSON-LD @graph."""
    if isinstance(obj, list):
        for it in obj:
            yield from _walk(it)
    elif isinstance(obj, dict):
        yield obj
        g = obj.get("@graph")
        if g:
            yield from _walk(g)


def _jsonld_product(html: str) -> Optional[dict]:
    """The first schema.org Product in any <script type='application/ld+json'> (Shopify & most
    stores put the full Product here; extract_structured_data deliberately skips JSON-LD)."""
    try:
        import json

        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, "html.parser")
    except Exception:  # noqa: BLE001
        return None
    for tag in soup.find_all("script", attrs={"type": "application/ld+json"}):
        txt = tag.string or tag.get_text() or ""
        if not txt.strip():
            continue
        try:
            obj = json.loads(txt)
        except Exception:  # noqa: BLE001
            continue
        for node in _walk(obj):
            if isinstance(node, dict) and _type_is_product(node):
                return node
    return None


def _product_data(html: str, url: str) -> Optional[dict]:
    """The best schema.org Product object on the page — JSON-LD first, then microdata/rdfa."""
    node = _jsonld_product(html)
    if node:
        return node
    try:
        blocks = extract_structured_data(html, url)
    except Exception:  # noqa: BLE001
        return None
    for b in blocks:                                  # explicit Product type wins
        t = getattr(b, "type", None)
        if isinstance(t, str) and t.strip().lower() in _PRODUCT_TYPES and isinstance(b.data, dict):
            return b.data
    for b in blocks:                                  # else anything that walks like a product
        d = b.data if isinstance(getattr(b, "data", None), dict) else {}
        if d.get("name") and (d.get("image") or d.get("offers")):
            return d
    return None


def parse_product(html: str, url: str) -> Optional[ProductInfo]:
    """Extract one product's {name, image, price, description} from a product PAGE.
    schema.org Product first (structured, real), then og:/meta/<h1> fallback. Returns None when
    neither a name nor an image can be found (i.e. not a product page)."""
    info = ProductInfo(url=url)
    data = _product_data(html, url)
    if data:
        info.name = _first(data.get("name"))
        info.image = _image_of(data, url)
        info.description = _first(data.get("description"))
        info.price, info.currency = _price_of(data)

    if not (info.name and info.image):                # og/meta fills the gaps
        try:
            meta = extract_metadata(html, url)
        except Exception:  # noqa: BLE001
            meta = None
        if meta is not None:
            info.name = info.name or (getattr(meta, "og_title", "") or "").strip()
            info.image = info.image or (getattr(meta, "og_image", "") or "").strip()
            info.description = info.description or (
                getattr(meta, "meta_description", "") or getattr(meta, "description", "")
                or getattr(meta, "og_description", "") or "").strip()

    # The gallery: every <img> that looks like a product asset, og:image first.
    try:
        from bs4 import BeautifulSoup
        from urllib.parse import urljoin
        soup = BeautifulSoup(html, "html.parser")
        seen, gallery = set(), []
        for src in ([info.image] if info.image else []) + [
                (t.get("src") or t.get("data-src") or "") for t in soup.find_all("img")]:
            u = str(src or "").strip()
            if not u or u.startswith("data:"):
                continue
            u = urljoin(url, u)
            base = u.split("?", 1)[0].lower()
            if base in seen or not base.endswith((".png", ".jpg", ".jpeg", ".webp")):
                continue
            seen.add(base)
            gallery.append(u)
        info.images = gallery[:8]
    except Exception:  # noqa: BLE001 — the gallery is an enrichment
        info.images = [info.image] if info.image else []

    if not info.name:                                 # last resort: the first <h1>
        try:
            from bs4 import BeautifulSoup
            h1 = BeautifulSoup(html, "html.parser").find("h1")
            if h1:
                info.name = h1.get_text(strip=True)
        except Exception:  # noqa: BLE001
            pass

    info.name = (info.name or "").strip()[:200]
    info.description = (info.description or "").strip()[:600]
    return info if info.usable else None


def _fetch_with_browser(url: str, http2: bool):
    """One Playwright fetch of `url` at the chosen transport. Raises on launch/fetch trouble —
    the caller decides whether that is a retry or a give-up."""
    from playwright.sync_api import sync_playwright

    from scraper.browser_launch import launch_chromium
    from scraper.fetcher import fetch_page, make_browser_context
    with sync_playwright() as pw:
        browser = launch_chromium(pw, http2=http2)
        try:
            return fetch_page(make_browser_context(browser), url, keep_page=False)
        finally:
            browser.close()


def scrape_product_page(url: str) -> Optional[ProductInfo]:
    """Fetch ONE product page (Playwright, so JS-rendered stores work) and parse it. Used when the
    user gives a direct product link for an item the crawl didn't reach. None on any failure.

    HTTP/2 STAYS ON unless the transport itself fails — the same law the crawler follows, now
    from the shared helper. MEASURED on the owner's own link (2026-07-26): the old global
    `--disable-http2` returned 169 bytes where HTTP/2 returned 974,865, and the client was told
    «مقدرناش نفتح صفحة المنتج دي» about a page the site serves happily."""
    from scraper.browser_launch import looks_like_http2_failure
    from scraper.url_utils import ensure_scheme
    u = ensure_scheme((url or "").strip())
    if not u.lower().startswith(("http://", "https://")):
        return None
    res = None
    for http2 in (True, False):
        try:
            res = _fetch_with_browser(u, http2)
        except Exception as exc:  # noqa: BLE001
            logger.warning("scrape_product_page: fetch failed (http2=%s): %s", http2, exc)
            res = None
        html = (getattr(res, "html", "") or "").strip() if res is not None else ""
        if html:
            return parse_product(html, getattr(res, "final_url", u) or u)
        if not (res is not None and looks_like_http2_failure(res)):
            break            # a real failure, not the transport — HTTP/1.1 would not help
        logger.warning("scrape_product_page: HTTP/2 protocol error — retrying over HTTP/1.1")
    return None
