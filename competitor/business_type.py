"""Business-type classification: LOCAL / ECOMMERCE / HYBRID / REACH / UNKNOWN.

Pure and grounded — no network, no inference beyond the signals it exposes. The
discovery router (discovery.route_discovery) uses the verdict to pick an engine:
LOCAL -> Places, ECOMMERCE/REACH -> web engine, HYBRID -> both, UNKNOWN -> web.

Every result carries the `signals` that drove it, so the decision is traceable
(no hidden heuristics). Reads the real BusinessProfile shape but tolerates a
plain dict (model_dump) and missing fields.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


class BusinessType(str, Enum):
    LOCAL = "local"
    ECOMMERCE = "ecommerce"
    HYBRID = "hybrid"
    REACH = "reach"          # competes by category/reach, not proximity -> web discovery
    UNKNOWN = "unknown"


# category.value -> coarse class. Anything not listed is "ambiguous" (services_b2c,
# nonprofit, other) and leans on structural signals.
_LOCAL_CATEGORIES = {
    "restaurant", "cafe", "clinic", "hospital", "beauty", "fitness",
    "automotive", "real_estate", "hospitality", "government",
}
_ECOMMERCE_CATEGORIES = {"ecommerce", "retail"}

# REACH categories compete by CATEGORY / expertise across a whole city, country, or online
# — an IT-training institute's rivals are other institutes / bootcamps / platforms, NOT the
# building next door — so geographic PROXIMITY is a poor relevance signal for them (Places
# returns broad-category neighbours: universities, faculties). They route to web-discovery
# (SERP + LLM relevance judge). With a real local footprint they become HYBRID (Places + web);
# without one they are REACH (web only), never pure proximity-LOCAL. (Measured: ITI, an IT
# institute, was classed LOCAL and Places returned universities + the brand's own branch.)
_REACH_CATEGORIES = {"education", "professional_services", "services_b2b", "agency"}

# Strong: a services/local site almost never exposes these. Medium: pathy hints
# that products exist but are weaker on their own.
_ECOM_URL_STRONG = ("cart", "checkout", "add-to-cart", "add_to_cart", "basket", "/collections")
_ECOM_URL_MEDIUM = ("/product", "/products", "/shop", "/store")


@dataclass
class BusinessTypeResult:
    business_type: BusinessType
    signals: Dict[str, Any] = field(default_factory=dict)
    reasoning: str = ""


# --- tolerant accessors (object or dict; unwrap EvidencedField.value) ---------

def _get(obj, name, default=None):
    if isinstance(obj, dict):
        val = obj.get(name, default)
    else:
        val = getattr(obj, name, default)
    return val if val is not None else default


def _unwrap(f):
    if isinstance(f, dict):
        return f.get("value")
    inner = getattr(f, "value", None)
    return inner if inner is not None else f


def _category_str(profile) -> str:
    cat = _unwrap(_get(profile, "category"))
    return str(getattr(cat, "value", cat) or "").lower()


def _has_address(profile) -> bool:
    for loc in _get(profile, "locations", []) or []:
        addr = _get(loc, "address_text")
        lat, lng = _get(loc, "latitude"), _get(loc, "longitude")
        if (addr and str(addr).strip()) or (lat is not None and lng is not None):
            return True
    return False


def _candidate_urls(profile, manifest) -> List[str]:
    urls: List[str] = []
    for o in _get(profile, "offerings", []) or []:
        u = _get(o, "page_url")
        if u:
            urls.append(str(u))
    if manifest is not None:
        links = _get(manifest, "links")
        if links is not None:
            for bucket in ("cta_candidates", "internal", "external"):
                for link in _get(links, bucket, []) or []:
                    href = _get(link, "href")
                    if href:
                        urls.append(str(href))
    return [u.lower() for u in urls]


def _priced_offerings(profile) -> int:
    return sum(1 for o in (_get(profile, "offerings", []) or []) if _get(o, "price_text"))


def classify_business_type(profile, manifest=None) -> BusinessTypeResult:
    """Classify a business for discovery routing. `manifest` is optional and only
    sharpens the ecommerce structural signal (cart/checkout/product paths)."""
    category = _category_str(profile)
    has_address = _has_address(profile)
    urls = _candidate_urls(profile, manifest)
    priced = _priced_offerings(profile)

    cart_or_checkout = any(h in u for u in urls for h in _ECOM_URL_STRONG)
    product_paths = sum(1 for u in urls if any(h in u for h in _ECOM_URL_MEDIUM))

    # ecommerce structural evidence (any strong signal, or repeated weaker ones)
    ecom_structural = cart_or_checkout or priced >= 2 or product_paths >= 2

    # process.md §8.11: `business_model` answers HOW a business sells directly. When present it
    # drives the three routing axes instead of re-deriving them from the conflated `category` via
    # hand-kept sets. Absent/unknown -> the legacy category-set behaviour still runs, so an
    # un-migrated profile routes exactly as before.
    model = _unwrap(_get(profile, "business_model"))
    model = str(getattr(model, "value", model) or "").strip().lower()

    if model == "ecommerce":
        reach, local, ecom = False, has_address, True
    elif model == "local":
        reach, local, ecom = False, True, ecom_structural
    elif model == "service":
        reach, local, ecom = True, has_address, False
    elif model == "hybrid":
        reach, local, ecom = False, True, True
    else:  # "unknown" or absent -> the pre-split signals
        reach = category in _REACH_CATEGORIES
        local = has_address or category in _LOCAL_CATEGORIES
        ecom = category in _ECOMMERCE_CATEGORIES or ecom_structural

    # A REACH CATEGORY wins over the weak ecom_structural signal: an education/services brand
    # trips priced_offerings>=2 just by listing course/service fees, but it is NOT an ecommerce
    # store — it competes by category, so it must route to web discovery (HYBRID with a footprint,
    # else REACH), never get mislabelled ECOMMERCE. Measured: ITI (2 priced courses) -> REACH.
    if reach:
        bt = BusinessType.HYBRID if local else BusinessType.REACH
    elif local and ecom:
        bt = BusinessType.HYBRID          # a real footprint AND category-searchable -> both engines
    elif local:
        bt = BusinessType.LOCAL
    elif ecom:
        bt = BusinessType.ECOMMERCE
    else:
        bt = BusinessType.UNKNOWN

    signals = {
        "category": category or None,
        "has_physical_address": has_address,
        "priced_offerings": priced,
        "cart_or_checkout_links": cart_or_checkout,
        "product_shop_paths": product_paths,
        "ecom_structural": ecom_structural,
        "reach_category": reach,
    }
    reasoning = (
        f"category={category or 'unknown'} "
        f"(local={category in _LOCAL_CATEGORIES}, ecom={category in _ECOMMERCE_CATEGORIES}, "
        f"reach={reach}); "
        f"address={has_address}; ecom_structural={ecom_structural} "
        f"(cart/checkout={cart_or_checkout}, priced_offerings={priced}, "
        f"product_paths={product_paths}) -> {bt.value}"
    )
    return BusinessTypeResult(business_type=bt, signals=signals, reasoning=reasoning)
