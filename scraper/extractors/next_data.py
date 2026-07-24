"""Next.js __NEXT_DATA__ structured-data extractor.

Many Next.js sites embed their server-rendered data in a
`<script id="__NEXT_DATA__" type="application/json">` tag. The payload
contains the same structured information the client app would render
once hydrated — menu categories, menu items, hotlines, CTAs in i18n
dictionaries, etc.

This extractor runs on every page where the script tag is present
(Option B per the user's decision on 2026-05-23). It is NOT a fallback
for missing DOM content — it's a first-class structured data source
alongside JSON-LD and Microdata.

What it extracts:
  - Organization/business name (when present at well-known paths)
  - MenuItem entries (normalized as schema.org/MenuItem SchemaOrgBlocks)
  - Hotline / short-code phones from whitelisted i18n keys
  - CTA candidates from whitelisted i18n keys
  - Inferred internal URLs from slugs (recorded but NOT fetched in this PR)

What it does NOT do:
  - Replace DOM extraction
  - Run blanket extraction over all i18n strings (would create noise)
  - Promote menu item images to images_of_interest (skipped this PR;
    image data is preserved inside the MenuItem.data dict)
  - Trust developer-test phone numbers (whitelist-only)
  - Auto-fetch inferred URLs (LinkInventory.inferred_internal is read-only)

Provenance:
  Every block/record produced here carries source="next_data"
  (SchemaOrgBlock) or extractor="rule:next_data:<kind>" (evidence items).
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Optional
from urllib.parse import urljoin

from ..schemas import (
    LinkCategory,
    LinkRecord,
    PhoneRecord,
    SchemaOrgBlock,
)

logger = logging.getLogger(__name__)


# Hard cap on payload size we attempt to parse. Real Next.js payloads
# are typically 5-200 KB; anything past a few MB is either pathological
# or hostile.
_MAX_PAYLOAD_BYTES = 3 * 1024 * 1024  # 3 MB

# Hard cap on objects we walk. Defensive against deeply-nested payloads.
_MAX_NODES = 20_000

# Hard caps per category — refuse to flood the manifest from one page.
_MAX_MENU_ITEMS = 80
_MAX_CATEGORIES = 30
_MAX_INFERRED_URLS = 60


# ---------------------------------------------------------------------
# i18n whitelists — only customer-facing keys are extracted
# ---------------------------------------------------------------------

def _normalize_key(key: str) -> str:
    """Strip case + separators so 'goToCart', 'go_to_cart', 'go-to-cart',
    'cta.go_to_cart' all collapse to the same canonical form."""
    if not isinstance(key, str):
        return ""
    s = key.lower()
    s = re.sub(r"[^a-z0-9]+", "", s)
    return s


# Phone hotline keys. Only these i18n keys' values are read as phones.
# Conservative; expand as benchmark surfaces more patterns.
_PHONE_KEYS = {
    _normalize_key(k) for k in [
        "hotline", "phone", "phone_number", "phonenumber",
        "call_us", "callus", "call_us_text",
        "contact_phone", "contact_number", "contactphone",
        "helpline", "support_phone", "supportphone",
        "telephone", "tel",
    ]
}

# CTA keys. Values are added as CTA candidate anchor texts.
_CTA_KEYS = {
    _normalize_key(k) for k in [
        # Ordering / commerce
        "order_now", "order_online", "start_ordering", "place_order",
        "buy_now", "shop_now", "checkout", "proceed_to_checkout",
        # Cart
        "go_to_cart", "view_cart", "open_cart", "cart_button",
        # Menu
        "view_menu", "see_menu", "our_menu", "menu_cta",
        # Booking / reservation
        "book_now", "book_a_table", "reserve_now", "reserve_table",
        # Contact
        "call_us", "callus", "call_now", "contact_us",
        # Apps
        "download_app", "download_our_app", "get_the_app",
        # Delivery
        "delivery", "order_delivery", "delivery_cta",
        # Generic CTA wrappers some sites use
        "cta_primary", "cta_text", "primary_action", "main_cta",
    ]
}


# ---------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------

@dataclass
class NextDataResult:
    found: bool = False
    payload_size_bytes: int = 0
    payload_likely_dominant: bool = False  # set by caller after comparing to DOM text size
    schema_blocks: list[SchemaOrgBlock] = field(default_factory=list)
    phones: list[PhoneRecord] = field(default_factory=list)
    cta_candidates: list[LinkRecord] = field(default_factory=list)
    inferred_internal: list[LinkRecord] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------
# Public entry
# ---------------------------------------------------------------------

_NEXT_DATA_RE = re.compile(
    r'<script[^>]*\bid=["\']__NEXT_DATA__["\'][^>]*>(.*?)</script>',
    re.IGNORECASE | re.DOTALL,
)


def extract_next_data(html: str, page_url: str) -> NextDataResult:
    """Extract structured data from a page's __NEXT_DATA__ script tag.

    Pure function. No I/O. Returns NextDataResult; never raises.
    Caller is responsible for setting payload_likely_dominant after
    comparing payload_size_bytes against DOM text size.
    """
    result = NextDataResult()
    if not html:
        return result

    m = _NEXT_DATA_RE.search(html)
    if not m:
        return result

    raw_payload = m.group(1).strip()
    if not raw_payload:
        return result

    # Size check before parse — refuses to load DoS-sized payloads.
    payload_bytes = raw_payload.encode("utf-8", errors="ignore")
    result.payload_size_bytes = len(payload_bytes)
    if result.payload_size_bytes > _MAX_PAYLOAD_BYTES:
        result.found = True
        result.notes.append(f"next_data_payload_too_large:{result.payload_size_bytes}")
        return result

    try:
        data = json.loads(raw_payload)
    except (json.JSONDecodeError, ValueError) as exc:
        result.found = True  # the tag was there; we just couldn't parse it
        result.notes.append(f"next_data_parse_failed:{type(exc).__name__}")
        return result

    if not isinstance(data, (dict, list)):
        result.found = True
        result.notes.append("next_data_not_object")
        return result

    result.found = True

    # Walk the JSON tree looking for known shapes.
    walker = _Walker(result, page_url)
    walker.walk(data)
    walker.finalize()

    return result


# ---------------------------------------------------------------------
# Tree walker — identifies known shapes regardless of where they sit
# in the payload
# ---------------------------------------------------------------------

class _Walker:
    """Walks an arbitrary Next.js JSON payload, harvesting recognized
    shapes. Different Next.js sites place data at different paths
    (props.pageProps.menu_categories vs props.pageProps.data.menu);
    shape detection is more robust than path-based extraction.
    """

    def __init__(self, result: NextDataResult, page_url: str):
        self.result = result
        self.page_url = page_url
        self._nodes_visited = 0
        self._seen_menu_items: set[str] = set()
        self._seen_categories: set[str] = set()
        self._seen_phones: set[str] = set()
        self._seen_ctas: set[str] = set()
        self._seen_urls: set[str] = set()

    def walk(self, node: Any, parent_key: str = "") -> None:
        if self._nodes_visited >= _MAX_NODES:
            return
        self._nodes_visited += 1

        if isinstance(node, dict):
            # Detect known shapes BEFORE recursing — once we identify a
            # dict as a menu item, we don't keep walking inside it as
            # if it might be something else.
            if _looks_like_menu_item(node):
                self._handle_menu_item(node)
                # Still recurse: a menu item might carry nested items
                # in some shapes. Bounded by _MAX_MENU_ITEMS.
            elif _looks_like_menu_category(node):
                self._handle_menu_category(node)

            # Walk children
            for k, v in node.items():
                # i18n-style keys: leaf string values that match a whitelisted
                # key get harvested. We check at the parent level so we
                # know the key name.
                if isinstance(v, str):
                    self._maybe_harvest_i18n_string(k, v)
                self.walk(v, parent_key=k)

        elif isinstance(node, list):
            for item in node:
                self.walk(item, parent_key=parent_key)

    def finalize(self) -> None:
        """Apply caps and clean up after the walk."""
        # The walker enforces _MAX_NODES; the per-category caps are
        # enforced in _handle_* methods.
        pass

    # ----- Menu items -----

    def _handle_menu_item(self, node: dict) -> None:
        if len([b for b in self.result.schema_blocks if b.type == "MenuItem"]) >= _MAX_MENU_ITEMS:
            return

        title = _pick_first(node, ("title", "name"))
        if not isinstance(title, str) or not title.strip():
            return

        # Dedupe by (title, price) — sometimes the same item appears in
        # both `featured_menu_items` and a category's `items` list.
        price = _pick_first(node, ("total_price", "price", "net_price"))
        dedupe_key = f"{title.strip().lower()}::{price}"
        if dedupe_key in self._seen_menu_items:
            return
        self._seen_menu_items.add(dedupe_key)

        # Normalize to schema.org/MenuItem shape. We keep useful raw
        # fields in the data dict (image, slug) so downstream consumers
        # have access without re-walking the original payload.
        data: dict[str, Any] = {"@type": "MenuItem", "name": title.strip()}

        description = _pick_first(node, ("description", "desc"))
        if isinstance(description, str) and description.strip():
            data["description"] = description.strip()

        category = _pick_first(node, ("category", "category_name", "categoryName"))
        if isinstance(category, str) and category.strip():
            data["menuAddOn"] = category.strip()  # closest std field for category label

        if price is not None and isinstance(price, (int, float)):
            data["offers"] = {"@type": "Offer", "price": price}

        image = _pick_first(node, ("image", "image_url", "imageUrl", "img", "photo"))
        if isinstance(image, str) and image.strip():
            data["image"] = image.strip()

        slug = _pick_first(node, ("slug",))
        if isinstance(slug, str) and slug.strip():
            data["url"] = slug.strip()  # raw slug; not a real URL yet
            self._maybe_infer_offer_url(slug.strip())

        # Arabic title — preserve under alternateName if present.
        title_ar = _pick_first(node, ("title_ar", "name_ar"))
        if isinstance(title_ar, str) and title_ar.strip():
            data["alternateName"] = title_ar.strip()

        self.result.schema_blocks.append(SchemaOrgBlock(
            type="MenuItem", data=data, source="next_data",
        ))

    # ----- Menu categories -----

    def _handle_menu_category(self, node: dict) -> None:
        if len(self._seen_categories) >= _MAX_CATEGORIES:
            return

        name = _pick_first(node, ("name", "title", "category_name", "categoryName"))
        if not isinstance(name, str) or not name.strip():
            return
        key = name.strip().lower()
        if key in self._seen_categories:
            return
        self._seen_categories.add(key)

        slug = _pick_first(node, ("slug",))
        if isinstance(slug, str) and slug.strip():
            self._maybe_infer_category_url(slug.strip())

    # ----- i18n harvesting -----

    def _maybe_harvest_i18n_string(self, key: str, value: str) -> None:
        if not isinstance(value, str):
            return
        v = value.strip()
        if not v:
            return
        nkey = _normalize_key(key)
        if not nkey:
            return

        # A key can be in both whitelists — e.g. "call_us" with the value
        # "Call Us 19914" is both a phone (19914) and a CTA (the anchor
        # text "Call Us 19914"). We route to both rather than else-ing.
        if nkey in _PHONE_KEYS:
            self._handle_phone_value(v, key)
        if nkey in _CTA_KEYS:
            self._handle_cta_value(v, key)

    def _handle_phone_value(self, value: str, key: str) -> None:
        # Extract digits. Reuses the same 3-7 digit short-code rule the
        # tel: handler uses, AND accepts longer numbers that look like
        # full international phones (we leave E.164 validation to a later
        # step; for now we store as a short-code-style record so the
        # business_profile layer can decide).
        digits = re.sub(r"\D", "", value)
        if not digits:
            return
        if digits in self._seen_phones:
            return
        self._seen_phones.add(digits)

        if 3 <= len(digits) <= 7:
            self.result.phones.append(PhoneRecord(
                e164=None,
                raw=digits,
                is_short_code=True,
                evidence_block_id=None,
                page_url=self.page_url,
            ))
        elif len(digits) >= 8:
            # A full-length phone string from i18n. Store raw; let the
            # later contact pipeline attempt E.164 normalization if it
            # picks this up through the manifest. For PR-now we store
            # as a non-short-code record with e164=None.
            self.result.phones.append(PhoneRecord(
                e164=None,
                raw=value,  # preserve original formatting for downstream normalizer
                is_short_code=False,
                evidence_block_id=None,
                page_url=self.page_url,
            ))
        # else: 1-2 digits → garbage, skipped

    def _handle_cta_value(self, value: str, key: str) -> None:
        anchor = value.strip()
        if len(anchor) > 80:
            return  # CTAs are short by definition; long values are sentences
        dedupe = anchor.lower()
        if dedupe in self._seen_ctas:
            return
        self._seen_ctas.add(dedupe)

        # We don't have a real href; some Next.js CTAs are buttons
        # bound to JS handlers, not links. We synthesize "#" so the
        # LinkRecord schema is satisfied.
        self.result.cta_candidates.append(LinkRecord(
            href="#",
            anchor_text=anchor,
            page_url=self.page_url,
            category=LinkCategory.CTA,
        ))

    # ----- URL synthesis (recorded only, NOT auto-fetched) -----

    def _maybe_infer_offer_url(self, slug: str) -> None:
        if len(self.result.inferred_internal) >= _MAX_INFERRED_URLS:
            return
        # The pattern /branches/all/offer-detail/{slug} is Buffalo Burger-
        # specific. We synthesize defensively — these URLs are NOT crawled.
        synth = urljoin(self.page_url, f"/branches/all/offer-detail/{slug}")
        if synth in self._seen_urls:
            return
        self._seen_urls.add(synth)
        self.result.inferred_internal.append(LinkRecord(
            href=synth,
            anchor_text=slug,
            page_url=self.page_url,
            category=LinkCategory.INTERNAL,
            inferred=True,          # CS-56: recorded in the inventory but NEVER crawled
        ))

    def _maybe_infer_category_url(self, slug: str) -> None:
        if len(self.result.inferred_internal) >= _MAX_INFERRED_URLS:
            return
        synth = urljoin(self.page_url, f"/branches/all/menu#{slug}")
        if synth in self._seen_urls:
            return
        self._seen_urls.add(synth)
        self.result.inferred_internal.append(LinkRecord(
            href=synth,
            anchor_text=slug,
            page_url=self.page_url,
            category=LinkCategory.INTERNAL,
            inferred=True,          # CS-56: recorded in the inventory but NEVER crawled
        ))


# ---------------------------------------------------------------------
# Shape detectors
# ---------------------------------------------------------------------

# A priced node whose NAME is one of these is a checkout line-item (shipping/fee/tax/tip/…), NOT a
# menu item / offering (P-69). Without this, "Express Shipping" @ 50 or "VAT" @ 14 became a brand
# "offering". Bilingual (EN + AR) — the scope is any brand, any country.
_NON_OFFERING_NAME_RE = re.compile(
    r"\b(shipping|delivery|freight|handling|postage|surcharge|"
    r"tax|vat|gst|fee|fees|tip|gratuity|service\s*charge|"
    r"discount|coupon|voucher|deposit|gift\s*wrap|gift\s*card|packaging)\b"
    r"|شحن|توصيل|رسوم|ضريبة|ضرائب|خدمة|بقشيش|خصم|كوبون|تغليف|عربون",
    re.IGNORECASE,
)


def _looks_like_menu_item(node: dict) -> bool:
    """A dict is a menu item if it has a title/name AND a price-like field — EXCEPT a priced
    checkout line-item (shipping / fee / tax / …), which has a name and a price but is not an
    offering (P-69). Also still avoids category objects that have a name but no price."""
    title = next((node[k] for k in ("title", "name")
                  if isinstance(node.get(k), str) and node[k].strip()), None)
    if not title:
        return False
    has_price = any(
        isinstance(node.get(k), (int, float))
        for k in ("total_price", "price", "net_price")
    )
    if not has_price:
        return False
    if _NON_OFFERING_NAME_RE.search(title):
        return False
    return True


def _looks_like_menu_category(node: dict) -> bool:
    """A dict is a menu category if it has a name AND a slug AND does
    NOT have a price-like field. Avoids matching menu items that also
    have a slug."""
    has_name = any(
        isinstance(node.get(k), str) and node[k].strip()
        for k in ("name", "title", "category_name", "categoryName")
    )
    has_slug = isinstance(node.get("slug"), str) and node.get("slug", "").strip()
    has_price = any(
        isinstance(node.get(k), (int, float))
        for k in ("total_price", "price", "net_price")
    )
    return has_name and has_slug and not has_price


def _pick_first(node: dict, keys: Iterable[str]) -> Any:
    for k in keys:
        if k in node and node[k] is not None:
            return node[k]
    return None