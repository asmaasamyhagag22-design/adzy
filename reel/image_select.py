"""Vision-based selection of the brand's REAL photos for the reel.

The scraper collects every on-page image; many are NOT footage you'd put in a
marketing reel — partner/sponsor logos (Microsoft, AWS, ...), QR codes, icons,
diagrams, text banners, screenshots. Animating those is the "زوّم على QR code"
bug. URL/extension heuristics can't tell a partner logo from a real photo, so we
SHOW each image to a vision model together with the business identity and keep
only real, on-brand PHOTOGRAPHS of THIS business (its space, people, work, food,
products). Honest-degrade: if no key/SDK/network, return the input unchanged.
"""
from __future__ import annotations

import logging
import os
from typing import Optional

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

# Kinds the model assigns. Only REAL_PHOTO is kept for the reel.
_KEEP_KIND = "real_photo"


class PhotoVerdict(BaseModel):
    index: int
    kind: str = Field(description=(
        "one of: real_photo (a genuine photograph of THIS business — its space, "
        "people, staff, customers, food, products, work, events), logo, "
        "partner_or_sponsor_logo, qr_code, icon, diagram_or_chart, "
        "text_banner_or_poster, screenshot, stock_or_irrelevant"
    ))
    subject: str = Field(description="3-8 words: what the photo actually shows")
    on_brand: bool = Field(description="true only if it authentically represents THIS business/vertical")
    quality: int = Field(description="1-5: how good a marketing-reel shot it is (5=hero)")


class PhotoSelection(BaseModel):
    verdicts: list[PhotoVerdict]


def _image_data_url(url: str, *, max_side: int = 640) -> Optional[str]:
    """Download an image OURSELVES (cert-tolerant, SSRF-guarded) and return a small
    base64 JPEG data URL. We send bytes, not the URL, so a site that blocks the
    model's image fetcher (e.g. gov.eg) still gets analyzed. None on any failure."""
    import base64
    import io
    from reel.video_provider import _load_reference_image
    loaded = _load_reference_image(url)
    if not loaded:
        return None
    data, _mime = loaded
    try:
        from PIL import Image
        img = Image.open(io.BytesIO(data)).convert("RGB")
        img.thumbnail((max_side, max_side))
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=80)
        data = buf.getvalue()
    except Exception:
        pass  # PIL missing/odd format -> send original bytes
    return "data:image/jpeg;base64," + base64.b64encode(data).decode("ascii")


def _system_prompt(business_name: str, category: str, description: str) -> str:
    return (
        "You are a senior video art director selecting shots for a vertical marketing "
        f"REEL for this specific business:\n"
        f"  Name: {business_name}\n  Category/field: {category or 'unknown'}\n"
        f"  About: {description or '(no description)'}\n\n"
        "You are shown numbered images scraped from the business's own website. For EACH "
        "image decide what it IS and whether it belongs in the reel. KEEP only kind="
        "'real_photo' that is genuinely a photograph OF THIS business and on-brand for its "
        "field. DROP logos, partner/sponsor badges, QR codes, icons, diagrams/charts, text "
        "banners/posters, screenshots, and generic stock or unrelated images. Be strict: a "
        "company logo (even a partner's) is NOT a real_photo; a QR code is NOT a real_photo. "
        "Return exactly one verdict per image, using its index."
    )


def _image_jpeg_bytes(url: str, *, max_side: int = 640) -> Optional[bytes]:
    """Fetched + downscaled JPEG bytes for the vision caller. None on any failure."""
    import io
    from reel.video_provider import _load_reference_image
    loaded = _load_reference_image(url)
    if not loaded:
        return None
    data, _mime = loaded
    try:
        from PIL import Image
        img = Image.open(io.BytesIO(data)).convert("RGB")
        img.thumbnail((max_side, max_side))
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=80)
        return buf.getvalue()
    except Exception:  # noqa: BLE001
        return data


def _fail_closed(pairs: list[tuple[str, bytes]], max_keep: int) -> list[str]:
    """FIX-D1: the no-caller / call-failure fallback. The old 'keep all' fallback is what
    let banners + logo-walls through for the whole all-Gemini era (the curator hard-required
    the now-empty OPENAI_API_KEY and silently became a no-op). With no model verdict we now
    keep only images that pass the DETERMINISTIC suspicious screens (collage/logo-grid ban);
    the technical size/ratio gate runs separately in image_quality.filter_usable_photos."""
    from reel.image_quality import is_collage_bytes
    return [u for u, d in pairs if not is_collage_bytes(d)][:max_keep]


def select_brand_photos(
    image_urls: list[str],
    *,
    business_name: str,
    category: str = "",
    description: str = "",
    max_keep: int = 12,
    caller: Optional[object] = None,
    fetch: Optional[object] = None,    # fetch(url)->jpeg bytes|None; injectable for tests
    api_key: Optional[str] = None,     # legacy, unused (OpenAI removed 2026-07)
    model: str = "",                   # legacy, unused
    timeout_s: float = 90.0,           # legacy, unused
    return_verdicts: bool = False,     # return (url, subject) pairs instead of bare URLs
) -> list:
    """Return the subset of `image_urls` that are real, on-brand photographs, ordered
    best-first. FIX-D1 (2026-07-11): runs on the LIVE model stack — the shared Gemini
    caller (`caller` injectable for tests; default `default_caller(strong=False)`), the
    same interface the creative director uses. The old implementation hard-required
    OPENAI_API_KEY (empty since the all-Gemini migration) and silently kept EVERYTHING —
    the mechanism behind the screwdriver-macro / TRAINING-banner / partner-logo-wall reel.
    Never raises; with no caller or a failed call it fails CLOSED via the deterministic
    collage screen instead of keeping junk."""
    def _ret(url_list, subj_map=None):
        """Shape the result: bare URLs, or (url, subject) pairs when return_verdicts is set — so the
        director learns what each photo depicts (labels the vision model already computed)."""
        if not return_verdicts:
            return url_list
        subj_map = subj_map or {}
        return [(u, subj_map.get(u, "")) for u in url_list]

    _bad_hosts = ("facebook.com/tr", "/tr?", "google-analytics", "googletagmanager",
                  "doubleclick", "/pixel", "analytics.")
    urls = [u for u in (image_urls or [])
            if isinstance(u, str) and u.startswith(("http://", "https://"))
            and not any(b in u.lower() for b in _bad_hosts)]
    if len(urls) <= 1:
        return _ret(urls)

    # Download + downscale each image OURSELVES; keep only the ones we could fetch
    # (a site that blocks the model's fetcher still works because we send bytes).
    _fetch = fetch or _image_jpeg_bytes
    pairs: list[tuple[str, bytes]] = []
    for u in urls:
        d = _fetch(u)
        if d:
            pairs.append((u, d))
    if len(pairs) <= 1:
        logger.info("image_select: only %d images fetchable; keeping them", len(pairs))
        return _ret([u for u, _ in pairs] or urls[:max_keep])

    if caller is None:
        try:
            from business_profile.llm import default_caller
            caller = default_caller(strong=False)
        except Exception:  # noqa: BLE001
            caller = None
    if caller is None:
        logger.info("image_select: no vision caller; failing CLOSED on suspicious classes")
        return _ret(_fail_closed(pairs, max_keep))

    try:
        user = (
            f"The {len(pairs)} images are attached IN ORDER — the first attached image is "
            "index 0, the second is index 1, and so on. Classify every image; reply with "
            "exactly one verdict per image, using its index."
        )
        parsed, _usage = caller(
            _system_prompt(business_name, category, description), user, PhotoSelection,
            group_name="reel_image_select", images=[(d, "image/jpeg") for _, d in pairs],
        )
        fetched = [u for u, _ in pairs]
        if parsed is None:
            return _ret(_fail_closed(pairs, max_keep))

        kept: list[tuple[int, str]] = []   # (quality, url)
        subj_map: dict[str, str] = {}      # url -> what the photo depicts (for the director)
        dropped: list[str] = []
        for v in parsed.verdicts:
            if not (0 <= v.index < len(fetched)):
                continue
            if v.kind == _KEEP_KIND and v.on_brand:
                kept.append((v.quality, fetched[v.index]))
                subj_map[fetched[v.index]] = str(getattr(v, "subject", "") or "").strip()
            else:
                dropped.append(f"{v.kind}:{v.subject}")
        kept.sort(key=lambda kv: -kv[0])
        result = [u for _, u in kept][:max_keep]
        logger.info("image_select: kept %d/%d (dropped: %s)",
                    len(result), len(fetched), "; ".join(dropped[:8]))
        # If the model rejected everything (over-strict / all-logos site), that's the
        # caller's honest "logo-only" signal — return empty and let the caller decide.
        return _ret(result, subj_map)
    except Exception as e:  # noqa: BLE001
        logger.warning("image_select failed (%s); failing CLOSED on suspicious classes", e)
        return _ret(_fail_closed(pairs, max_keep))
