"""Translate the pipeline's `BusinessProfile` (model_dump) into the brand object the AdArchitect
frontend reads (organization_name / readiness_score / brand_colors / social_links / services / …).

The frontend is brand-centric: after every step it re-fetches `GET /api/brands/{domain_key}` and
renders off this shape. This is the one translation layer; everything downstream (swot/strategy/
plan/images) enriches the SAME stored object.
"""
from __future__ import annotations

import re
from typing import Any, Optional
from urllib.parse import urlparse


# ---------------------------------------------------------------------
# domain_key — MUST match the frontend's domainKeyFromUrl (index.html:3762)
#   new URL(url).hostname.replace(/^www\./,"").replace(/\./g,"_").replace(/-/g,"_")
# ---------------------------------------------------------------------

def domain_key_from_url(url: str) -> str:
    host = (urlparse(url).hostname or urlparse("http://" + str(url)).hostname or "") or ""
    host = re.sub(r"^www\.", "", host.lower())
    return host.replace(".", "_").replace("-", "_") or "brand"


# ---------------------------------------------------------------------
# EvidencedField / list helpers (the profile is a model_dump, so enums are already .value strings)
# ---------------------------------------------------------------------

def _v(field: Any) -> Optional[Any]:
    """Unwrap an EvidencedField dict {value, evidence, …} to its value."""
    if isinstance(field, dict):
        return field.get("value")
    return field


def _s(field: Any) -> str:
    v = _v(field)
    return str(v).strip() if v not in (None, "") else ""


def _vlist(fields: Any) -> list[str]:
    """A list of EvidencedField (or plain) -> list of non-empty string values."""
    out: list[str] = []
    for f in fields or []:
        s = _s(f)
        if s:
            out.append(s)
    return out


def _readiness_score(profile: dict) -> tuple[int, str]:
    """Prefer the real swot_richness_score; else completeness of the 8 quality booleans."""
    readiness = profile.get("readiness") or {}
    quality = profile.get("quality") or {}
    score = readiness.get("swot_richness_score")
    if not isinstance(score, int) or score <= 0:
        flags = ["has_name", "has_category", "has_offerings", "has_audience",
                 "has_value_propositions", "has_tone", "has_contact", "has_visual"]
        got = sum(1 for k in flags if quality.get(k))
        score = round(got / len(flags) * 100)
    ready_strategy = readiness.get("ready_for_strategy", quality.get("ready_for_strategy", False))
    status = "ready" if ready_strategy else ("partial" if score >= 40 else "thin")
    return int(score), status


def profile_to_brand(profile: dict, *, source_url: str = "",
                     domain_key: Optional[str] = None) -> dict:
    """Build the frontend brand object from a BusinessProfile dump. Identity fields are filled now;
    the analysis blocks (swot / marketing_strategy / marketing_plan / content_calendar /
    ai_influencer) start empty and get enriched by later stages."""
    profile = profile or {}
    src = source_url or profile.get("source_url") or ""
    key = domain_key or domain_key_from_url(src)

    visual = profile.get("visual") or {}
    contact = profile.get("contact_channels") or {}
    locations = profile.get("locations") or []
    loc0 = locations[0] if locations else {}

    # logos
    logo_url = visual.get("logo_url") or ""
    if not logo_url:
        pl = visual.get("primary_logo") or {}
        logo_url = pl.get("src", "") if isinstance(pl, dict) else ""
    logo_urls = [logo_url] if logo_url else []
    for cand in (visual.get("logo_candidates") or []):
        s = cand.get("src") if isinstance(cand, dict) else None
        if s and s not in logo_urls:
            logo_urls.append(s)

    # social_links keyed by platform (frontend indexes by platform name)
    social_links: dict[str, str] = {}
    for acc in (profile.get("social_presence") or []):
        if isinstance(acc, dict) and acc.get("platform") and acc.get("url"):
            social_links.setdefault(str(acc["platform"]).lower(), acc["url"])

    # contact
    phones = contact.get("phones") or []
    phone = ""
    if phones:
        p0 = phones[0]
        phone = (p0.get("raw") if isinstance(p0, dict) else str(p0)) or ""
    emails = contact.get("emails") or []
    email = emails[0] if emails else ""
    addresses = contact.get("physical_addresses") or []
    address = (addresses[0] if addresses else "") or loc0.get("address_text", "")

    # offerings -> products/services (frontend derives columns from Object.keys(products[0]))
    products = []
    for off in (profile.get("offerings") or []):
        if not isinstance(off, dict):
            continue
        products.append({
            "name": off.get("name", ""),
            "price": off.get("price_text", "") or "",
            "description": off.get("short_description", "") or "",
            "url": off.get("page_url", "") or "",
        })

    score, status = _readiness_score(profile)
    colors = visual.get("palette_hex") or []
    fonts = [f for f in (visual.get("heading_font"), visual.get("body_font")) if f]
    images = list(visual.get("content_images") or [])
    if visual.get("hero_image_url"):
        images.insert(0, visual["hero_image_url"])

    key_messages = _vlist(profile.get("value_propositions")) + \
        _vlist(profile.get("other_unique_insights"))

    return {
        "domain_key": key,
        "source_url": src,
        "organization_name": _s(profile.get("name")) or key,
        "meta_title": _s(profile.get("name")),
        "tagline": _s(profile.get("tagline")),
        "description": _s(profile.get("description")),
        "meta_description": _s(profile.get("description")),
        "logo_url": logo_url,
        "logo_urls": logo_urls,
        "website_type": (_s(profile.get("market_category"))
                         or _s(profile.get("category"))
                         or profile.get("business_model") or ""),
        "readiness_score": score,
        "readiness_status": status,
        "country": loc0.get("country") or "",
        "city": loc0.get("city") or "",
        "address": address,
        "founding_year": None,
        "pages_count": (profile.get("extraction_meta") or {}).get("pages_scraped"),
        "brand_colors": colors,
        "fonts": fonts,
        "key_messages": key_messages,
        "social_links": social_links,
        "phone": phone,
        "email": email,
        "tone_of_voice": _s(profile.get("tone_of_voice")),
        "target_audience": _s(profile.get("audience_type")) or ", ".join(
            _vlist(profile.get("audience_signals"))[:3]),
        "languages": list(profile.get("languages") or []),
        "nav_links": [],
        "testimonials": [],
        "images": images,
        "products": products,
        "services": products,
        "pricing_plans": [],
        "categories": [c for c in (_s(profile.get("market_category")),
                                   _s(profile.get("category"))) if c],
        # --- run-state flags + analysis blocks (filled by later stages) ---
        "has_swot": False,
        "swot_approved": False,
        "has_strategy": False,
        "swot": None,
        "marketing_strategy": None,
        "marketing_plan": None,
        "content_calendar": None,
        "ai_influencer": {"has_portrait": False, "validated": False,
                          "has_poster": False, "config": {}},
    }
