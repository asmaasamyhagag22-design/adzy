# -*- coding: utf-8 -*-
"""The store's real, priced CATALOG — deterministic, from the leaf pages the crawl already saw.

WHY THIS EXISTS (measured, Al Fouad 2026-07-26). The crawler reached 38 individual product
pages and the evidence pack carried 60 of their blocks, 14 of them priced. `profile.offerings`
still came back as the 12 navigation DEPARTMENTS — "Skin Care", "Hair Care", … — each with
`price_text=None` and `page_url` pointing at the homepage. That is the catalog-SHAPE policy
behaving as designed: a 3,241-SKU store IS summarized by its departments, and rules 1-4 of the
`broad_catalog` shape say so explicitly.

But the summary is not the merchandise. Every creative surface downstream — a product poster, a
reel that must show a REAL pack, a caption naming a price — needs an actual item, and the
profile of the owner's own store had none; she had to paste a product link by hand.

So the departments stay, and this module adds the missing half from the SAME crawl, with:
  * no LLM — every field is verbatim page text, so there is nothing to hallucinate;
  * no network — the manifest is already on disk;
  * no locale assumptions (D-8) — the price is whatever currency token the page shows, in the
    page's own script; nothing here knows what EGP is.
"""
from __future__ import annotations

import logging
import re
from typing import Any, Optional

logger = logging.getLogger(__name__)

DEFAULT_LIMIT = 24

# A price is a currency MARK next to a number, in either order, in any script. The mark may be a
# symbol ($ € £ ¥ ₹ ₺), an Arabic currency word (ج.م / ر.س / د.إ / جنيه / ريال / درهم), or a
# 3-letter ISO-ish code (EGP, SAR, USD). Deliberately NOT a list of the currencies we happen to
# have seen: this repo has been burned by exactly that shape of hardcode.
_NUM = r"\d[\d.,٠-٩\s]*"
_SYMBOL = r"[$€£¥₹₺؋﷼]"
# An ISO code is a STANDALONE token. Measured: "SPF50" matched `[A-Z]{3}` + digits and shipped
# as a sunblock's price. `\b` between F and 5 does not exist (both word chars), so the boundary
# is what rejects it — while "EGP 850" and "850 EGP" still match.
_WORD = (r"ج\.?م|ر\.?س|د\.?إ|د\.?ك|ل\.?س|جنيه|ريال|درهم|دينار|ليرة|"
         r"\b[A-Z]{3}\b")
_MARK = rf"(?:{_SYMBOL}|(?:{_WORD}))"
_PRICE_RE = re.compile(rf"({_MARK}\s*{_NUM}|{_NUM}\s*{_MARK})", re.UNICODE)

# Text that is chrome on every store page and must never become a product NAME. "Language" and
# "Currency" earned their place by MEASUREMENT: the store's language switcher is an <h2> that is
# not tagged as nav, it appears before the product heading, and it named all 24 Al Fouad items.
_CHROME = {
    "skip to content", "add to cart", "add to bag", "buy now", "quantity", "search",
    "cart", "menu", "home", "sign in", "register", "checkout", "sale", "new",
    "language", "currency", "filter", "sort by", "share", "description", "reviews",
    "frequently bought together", "you may also like", "related products", "total price",
    "أضف إلى السلة", "اشتري الآن", "السلة", "بحث", "الرئيسية", "تسجيل الدخول",
    "اللغة", "العملة", "تصفية", "مشاركة", "الوصف", "التقييمات",
}
# Words that mark a LISTING page's heading ("3241 products") rather than an item.
_LISTING_RE = re.compile(r"^\s*[\d٠-٩,.]+\s*(products?|items?|منتج|منتجات)\s*$", re.I)


def _clean(text: Any) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


def canonical_product_url(url: str) -> str:
    """The product's identity, without the tracking/variant noise that makes ONE product look
    like four (measured: the Al Fouad crawl reached one serum through `?variant=`,
    `?pr_prod_strat=` and the bare path). Query params are dropped entirely: a leaf URL's
    identity is its path — a `?variant=` is the same product in another size, and a store that
    truly needs the variant still has it in the name."""
    u = str(url or "").split("#", 1)[0]
    u = u.split("?", 1)[0]
    return u.rstrip("/")


def _same_host_https(src: str, page_url: str) -> str:
    """Upgrade an http:// image to https:// when its page proved the SAME HOST speaks https.

    Al Fouad's og:image tag says `http://alfouadpharmacies.com/cdn/...` while the page it sits on
    is served over https. Kept verbatim, that URL is blocked as mixed content the moment the
    dashboard is served over https — the catalog would look populated and show broken images.

    This is not a guess about the wider web: the ONLY evidence used is that this exact host just
    served the page over https. Different host, or a page that is itself http -> untouched.
    """
    if not src.startswith("http://") or not page_url.startswith("https://"):
        return src
    host = src.split("://", 1)[1].split("/", 1)[0].lower()
    page_host = page_url.split("://", 1)[1].split("/", 1)[0].lower()
    return "https://" + src[len("http://"):] if host == page_host else src


def _is_leaf(url: str) -> bool:
    try:
        from scraper.classify.page_type import is_leaf_detail
        return bool(is_leaf_detail(url or ""))
    except Exception:  # noqa: BLE001
        return False


def _slug_tokens(url: str) -> set:
    """The meaningful words of a product URL's last path segment. `/products/
    eucerin-dermo-purifyer-triple-serum-40ml` -> {eucerin, dermo, purifyer, triple, serum, 40ml}."""
    tail = canonical_product_url(url).rsplit("/", 1)[-1]
    return {t for t in re.split(r"[-_+.]+", tail.lower()) if len(t) > 2}


def _slug_overlap(text: str, tokens: set) -> float:
    """How much of the URL slug this heading accounts for (0..1)."""
    if not tokens:
        return 0.0
    words = {w for w in re.split(r"[^\w؀-ۿ]+", text.lower()) if len(w) > 2}
    return len(words & tokens) / len(tokens)


def _name_from_page(page: Any) -> Optional[str]:
    """The product's name.

    THE SLUG IS THE ARBITER (measured on Al Fouad: all 24 items came back named "Language",
    the store's language switcher, an <h2> not tagged as nav that simply appears first). A
    product page's URL slug is written from its name, so the heading that best covers the slug
    is the product — no page chrome can fake that overlap. Falls back to first-real-heading
    when no heading matches the slug (a CMS whose URLs are ids, not names)."""
    blocks = list(getattr(page, "text_blocks", []) or [])
    tokens = _slug_tokens(str(getattr(page, "url", "") or ""))
    best, best_score = None, 0.0
    for b in blocks:
        if str(getattr(b, "tag", "")).lower() not in ("h1", "h2", "h3", "title"):
            continue
        sec = getattr(getattr(b, "section", None), "value", "") or ""
        if sec in ("nav", "footer"):
            continue
        text = _clean(getattr(b, "text", ""))
        if not (3 <= len(text) <= 160) or text.lower() in _CHROME or _LISTING_RE.match(text):
            continue
        score = _slug_overlap(text, tokens)
        if score > best_score:
            best, best_score = text, score
    if best is not None and best_score >= 0.5:
        return best

    for want_tags in (("h1",), ("h2", "title")):
        for b in blocks:
            if str(getattr(b, "tag", "")).lower() not in want_tags:
                continue
            sec = getattr(getattr(b, "section", None), "value", "") or ""
            if sec in ("nav", "footer"):
                continue
            text = _clean(getattr(b, "text", ""))
            if not (3 <= len(text) <= 160):
                continue
            if text.lower() in _CHROME or _LISTING_RE.match(text):
                continue
            return text
    title = _clean(getattr(page, "title", ""))
    if title:
        head = re.split(r"\s+[|–—-]\s+", title)[0].strip()
        if 3 <= len(head) <= 160 and head.lower() not in _CHROME:
            return head
    return None


def _price_from_page(page: Any) -> Optional[str]:
    """The first price-looking token on the page, VERBATIM. Verbatim matters twice: it is the
    grounding contract (a parsed float invents precision the page never stated), and it keeps
    the currency the customer actually sees."""
    for b in list(getattr(page, "text_blocks", []) or []):
        text = _clean(getattr(b, "text", ""))
        if not text or len(text) > 400:
            continue
        m = _PRICE_RE.search(text)
        if m:
            price = _clean(m.group(1))
            if any(ch.isdigit() for ch in price):
                return price
    return None


def build_catalog(manifest: Any, *, limit: int = DEFAULT_LIMIT) -> list[dict]:
    """[{name, price_text, url, image_url}] for the individual items this store sells.

    Reads ONLY leaf-detail pages (the universal `is_leaf_detail` notion the crawl frontier
    already reserves budget for), dedupes by canonical URL, and never raises — a catalog is an
    enrichment, and a store whose pages do not parse simply has an empty one."""
    try:
        pages = list(getattr(manifest, "pages", []) or [])
    except Exception:  # noqa: BLE001
        return []

    # MEASURED (Al Fouad, 24/24 items with image_url=None, and the same on every other brand):
    # `ImageOfInterest` has NO `url` field — the attribute is `src`. The old line read
    # `getattr(img, "url", "")`, so the default silently made every image "missing", the join
    # dict was empty on every site, and a store with a photo on every product page produced a
    # catalog with none. A default on a getattr turns a WRONG NAME into AN EMPTY RESULT, which
    # is why this survived: nothing ever failed.
    #
    # Role matters too. A product page's images include the site LOGO (its page_url is the
    # product page), and first-wins would have pinned the store logo onto every product — a
    # catalog that is populated and wrong, which is worse than the empty one it replaces.
    #
    # og:image leads, and that ordering was CHOSEN BY LOOKING, not assumed. Both candidates on
    # the Al Fouad serum page are the right product: `content` is a hand holding the bottle,
    # `og_image` is the clean packshot. og:image wins on two counts — it is the page's OWN
    # declaration of what represents it (evidence, not a heuristic "first large image", which
    # on this very page returned the LOGO for `hero`), and a packshot is what a product insert
    # can actually composite. A lifestyle photo carries a person, who collides with whatever
    # the reel already put on screen.
    _ROLE_RANK = {"og_image": 0, "content": 1, "hero": 2, "logo": 9}
    ranked: dict[str, tuple[int, str]] = {}
    for img in list(getattr(manifest, "images_of_interest", []) or []):
        page_url = canonical_product_url(getattr(img, "page_url", "") or "")
        src = _same_host_https(str(getattr(img, "src", "") or ""), page_url)
        if not (page_url and src):
            continue
        role = getattr(getattr(img, "role", None), "value", None) or str(getattr(img, "role", ""))
        rank = _ROLE_RANK.get(str(role).lower(), 5)
        if page_url not in ranked or rank < ranked[page_url][0]:
            ranked[page_url] = (rank, src)
    images: dict[str, str] = {k: v for k, (_, v) in ranked.items()}

    out: list[dict] = []
    seen: set[str] = set()
    for page in pages:
        url = str(getattr(page, "url", "") or "")
        if not _is_leaf(url):
            continue
        canon = canonical_product_url(url)
        if not canon or canon in seen:
            continue
        try:
            name = _name_from_page(page)
        except Exception:  # noqa: BLE001
            name = None
        if not name:
            continue
        seen.add(canon)
        out.append({
            "name": name,
            "price_text": _price_from_page(page),
            "url": canon,
            "image_url": images.get(canon),
        })
        if len(out) >= max(1, limit):
            break
    logger.info("catalog: %d item(s) from %d page(s)", len(out), len(pages))
    return out
