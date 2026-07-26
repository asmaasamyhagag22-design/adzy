from __future__ import annotations

import re
from typing import Any, Optional
from urllib.parse import urljoin, urlparse

from poster.schemas import PosterBrief


def _field_value(profile: dict[str, Any], key: str) -> Optional[Any]:
    field = profile.get(key)
    if isinstance(field, dict):
        return field.get("value")
    return field


def _clean_text(value: Any) -> str:
    return " ".join(str(value).strip().split())


def _clean_business_name(name: Optional[Any]) -> str:
    if not name:
        return "Unknown Business"

    cleaned = _clean_text(name)
    cleaned = re.sub(r"\s+Website$", "", cleaned, flags=re.IGNORECASE).strip()
    cleaned = re.sub(r"\s+Official$", "", cleaned, flags=re.IGNORECASE).strip()
    return cleaned or "Unknown Business"


def _first_sentence(text: Optional[Any], max_len: int = 150) -> Optional[str]:
    if not text:
        return None

    text = _clean_text(text)
    if not text:
        return None

    parts = re.split(r"(?<=[.!?])\s+", text)
    sentence = parts[0].strip() if parts else text

    if len(sentence) <= max_len:
        return sentence

    cut = sentence[: max_len - 1].rstrip()
    last_space = cut.rfind(" ")
    if last_space > 80:
        cut = cut[:last_space]

    return cut.rstrip(" ,.;:") + "…"


def _is_hex(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    return bool(re.fullmatch(r"#[0-9a-fA-F]{6}", value.strip()))


def _infer_category(profile: dict[str, Any]) -> str:
    """
    Infer the business category without overfitting to generic website words.

    Important:
    - Do NOT classify as restaurant just because the page contains "menu".
    - Use strong semantic/domain signals.
    """
    raw = _field_value(profile, "category")
    raw_category = _clean_text(raw).lower() if raw else ""

    text_parts = [
        raw_category,
        _clean_text(_field_value(profile, "description") or ""),
        _clean_text(_field_value(profile, "tagline") or ""),
        _clean_text(_field_value(profile, "tone_of_voice") or ""),
    ]

    for item in profile.get("offerings", []) or []:
        if isinstance(item, dict):
            text_parts.append(_clean_text(item.get("name", "")))
            text_parts.append(_clean_text(item.get("short_description", "")))
            text_parts.append(_clean_text(item.get("page_url", "")))

    text = " ".join(text_parts).lower()

    scores = {
        "restaurant": 0,
        "education": 0,
        "medical_clinic": 0,
        "skincare": 0,
        "business": 0,
    }

    if "restaurant" in raw_category or "cafe" in raw_category or "café" in raw_category:
        scores["restaurant"] += 3
    if "education" in raw_category or "training" in raw_category or "institute" in raw_category:
        scores["education"] += 3
    if "medical" in raw_category or "clinic" in raw_category or "doctor" in raw_category:
        scores["medical_clinic"] += 3
    if "skincare" in raw_category or "cosmetic" in raw_category or "beauty" in raw_category:
        scores["skincare"] += 3

    restaurant_terms = [
        "restaurant",
        "cafe",
        "café",
        "cuisine",
        "dining",
        "dish",
        "dishes",
        "grill",
        "grilled",
        "kebab",
        "kabab",
        "food",
        "chef",
        "branch menu",
        "special menu",
    ]

    education_terms = [
        "training",
        "academy",
        "institute",
        "course",
        "courses",
        "program",
        "programs",
        "certification",
        "certificate",
        "upskilling",
        "ict",
        "telecommunication",
        "telecommunications",
        "networking",
        "digital egypt",
        "dey",
        "train to hire",
        "learning",
        "education",
        "scholarship",
        "graduates",
    ]

    medical_terms = [
        "clinic",
        "doctor",
        "dermatology",
        "medical",
        "patient",
        "consultation",
        "specialist",
        "treatment",
        "healthcare",
    ]

    skincare_terms = [
        "skincare",
        "skin care",
        "cosmetic",
        "beauty",
        "serum",
        "cream",
        "ingredient",
        "routine",
        "product",
    ]

    for term in restaurant_terms:
        if term in text:
            scores["restaurant"] += 1

    for term in education_terms:
        if term in text:
            scores["education"] += 1

    for term in medical_terms:
        if term in text:
            scores["medical_clinic"] += 1

    for term in skincare_terms:
        if term in text:
            scores["skincare"] += 1

    if scores["education"] >= 2 and scores["education"] >= scores["restaurant"]:
        return "education"

    if scores["medical_clinic"] >= 2 and scores["medical_clinic"] >= scores["restaurant"]:
        return "medical_clinic"

    if scores["skincare"] >= 2 and scores["skincare"] >= scores["restaurant"]:
        return "skincare"

    if scores["restaurant"] >= 2:
        return "restaurant"

    if raw_category:
        return raw_category

    return "business"


def _fallback_palette_for_category(category: str) -> list[str]:
    """
    Used only when scraped palette is missing or visually unreliable.

    Keep fallbacks category-specific and neutral.
    Do not leak restaurant colors into education/clinic posters.
    """
    category = category.lower()

    if "restaurant" in category or "cafe" in category:
        return ["#2B1810", "#C8A355", "#F4EBDD", "#173340", "#7A4428"]

    if "medical" in category or "clinic" in category:
        return ["#17324D", "#5EA3C4", "#F4F8FB", "#0E1B2A", "#A8C7D9"]

    if "skincare" in category or "beauty" in category:
        return ["#6F4E57", "#D7B9A7", "#FFF5EF", "#2D2023", "#C88B7A"]

    if "education" in category or "training" in category or "institute" in category:
        return ["#0B1F3A", "#2E6E9E", "#F4F7FB", "#08111F", "#D49A3A"]

    return ["#172033", "#4F7D95", "#F3F6F8", "#10151F", "#D5A84C"]


def _palette_looks_unreliable(profile: dict[str, Any]) -> bool:
    visual = profile.get("visual") or {}

    warnings = visual.get("visual_warnings") or []
    note = visual.get("palette_extraction_note")

    warning_text = " ".join(str(w).lower() for w in warnings)
    note_text = str(note or "").lower()

    bad_markers = [
        "weak_brand_palette_confidence",
        "palette_dominated_by_background",
        "background",
        "weak",
    ]

    return any(marker in warning_text or marker in note_text for marker in bad_markers)


def _extract_palette(profile: dict[str, Any], category: str) -> tuple[list[str], Optional[str]]:
    visual = profile.get("visual") or {}

    # Prefer the cleaned, background-stripped brand colors the scraper already
    # computed. GROUNDING FIX: only fall back to a category swatch when the brand
    # gave us NO usable color. Previously a non-fatal warning
    # ('weak_brand_palette_confidence' / a 'background' marker) threw away the REAL
    # brand palette — e.g. digilians #133b69 -> generic education #0B1F3A — which
    # is exactly the "wrong colors" the poster AND reel showed (the reel reuses
    # this brief). _palette_looks_unreliable is no longer a reason to DISCARD.
    raw_palette = (
        visual.get("accent_colors")
        or visual.get("brand_palette")
        or visual.get("palette_hex")
        or visual.get("raw_palette")
        or []
    )
    exclude = {
        str(c).upper()
        for c in (visual.get("background_colors") or []) + (visual.get("neutral_colors") or [])
        if _is_hex(c)
    }

    palette: list[str] = []
    for color in raw_palette:
        if _is_hex(color):
            normalized = str(color).upper()
            if normalized not in palette and normalized not in exclude:
                palette.append(normalized)

    primary = visual.get("primary_brand_color") or visual.get("primary_color")
    primary_color = str(primary).upper() if _is_hex(primary) else None

    # Fall back to the category swatch ONLY when no real brand color exists at all.
    if not palette and not primary_color:
        palette = _fallback_palette_for_category(category)
        primary_color = palette[0]

    if primary_color is None and palette:
        primary_color = palette[0]

    # Lead the palette with the real primary brand color so it drives the scene.
    if primary_color:
        palette = [primary_color] + [c for c in palette if c != primary_color]

    return palette[:5], primary_color


def _business_name_tokens(name: str) -> set[str]:
    return {
        token.lower()
        for token in re.split(r"[^a-zA-Z0-9]+", name)
        if len(token) >= 3
    }


def _looks_like_business_name(text: str, business_name: str) -> bool:
    text_tokens = _business_name_tokens(text)
    name_tokens = _business_name_tokens(business_name)

    if not text_tokens or not name_tokens:
        return False

    overlap = len(text_tokens & name_tokens)
    return overlap >= max(1, min(2, len(name_tokens)))


def _extract_offerings(
    profile: dict[str, Any],
    business_name: str,
    category: str,
) -> list[str]:
    """Verbatim, evidence-backed offering names from the profile — at most 3.

    ZERO HALLUCINATION: names are used exactly as scraped (only whitespace is
    normalized). We never reword them (no marketing rewrites), never pad with
    invented copy, and never substitute off-field values (e.g. value_propositions).
    If the profile has no usable offerings, the list is empty and the poster omits
    the section entirely.

    `category` is kept in the signature for call-site stability but is no longer
    used — offering text must not depend on the vertical.
    """
    raw_items = profile.get("offerings") or []
    result: list[str] = []

    for item in raw_items:
        if not isinstance(item, dict):
            continue

        raw_name = item.get("name")
        if not raw_name:
            continue

        name = _clean_text(str(raw_name))
        if not name:
            continue

        if _looks_like_business_name(name, business_name):
            continue

        if name.lower() not in {x.lower() for x in result}:
            result.append(name)

        if len(result) >= 3:
            break

    return result


def _extract_program_or_source_url(profile: dict[str, Any]) -> Optional[str]:
    """The destination URL, UNWRAPPED.

    Every field in this project's profile schema may arrive as {"value": ..., "source": ...}.
    This reader returned the envelope itself, and `PosterBrief.cta_url` is a `str` — so a
    profile in its own standard shape raised ValidationError, killed the whole brief, and (as
    measured on the Al Fouad run, 2026-07-26) dropped the dashboard's directed reel to the
    Ken Burns fallback with no visible reason."""
    def _plain(v):
        if isinstance(v, dict):
            v = v.get("value")
        v = str(v).strip() if v is not None else ""
        return v or None

    for offering in profile.get("offerings") or []:
        if not isinstance(offering, dict):
            continue
        page_url = _plain(offering.get("page_url"))
        if page_url:
            return page_url

    return _plain(profile.get("final_url")) or _plain(profile.get("source_url"))


def _extract_contact_line(profile: dict[str, Any]) -> Optional[str]:
    contact = profile.get("contact_channels") or {}

    emails = contact.get("emails") or []
    if emails:
        return str(emails[0])

    whatsapp = contact.get("whatsapp_numbers") or []
    if whatsapp:
        return str(whatsapp[0])

    # 2026-06 fix: read the structured `phones` list, NOT `phones_e164`.
    # `phones_e164` is a Pydantic @property that is NOT serialized by
    # model_dump(mode="json"), so this dict never contains that key — it
    # always returned [] and every poster silently dropped the phone.
    # The live field is `phones`: a list of PhoneChannel dicts, each with
    # a dialable `raw` string and an optional validated `e164`. Prefer the
    # e164 form when present (clean international format), else fall back
    # to raw (covers short-code hotlines like "19914").
    phones = contact.get("phones") or []
    for ph in phones:
        if isinstance(ph, dict):
            number = ph.get("e164") or ph.get("raw")
            if number:
                return str(number)
        elif isinstance(ph, str):
            # Defensive: a plain string slipped in
            return ph

    # Legacy fallback: if an older profile still carries phones_e164 as a
    # real list (e.g. hand-built dicts, not via model_dump), honor it.
    legacy = contact.get("phones_e164") or []
    if legacy:
        return str(legacy[0])

    return None


def _cta_from_profile(profile: dict[str, Any]) -> tuple[str, Optional[str]]:
    """Evidence-grounded call-to-action.

    Uses a REAL scraped CTA label from the profile's `existing_ctas` (e.g.
    "Contact Us"), never a category-invented verb ("Explore programs", "Book a
    consultation", ...). The URL is the site/offering URL. If no usable CTA was
    scraped, falls back to a neutral, non-claim default — never a fabricated
    marketing phrase.
    """
    url = _extract_program_or_source_url(profile)

    for item in profile.get("existing_ctas") or []:
        value = item.get("value") if isinstance(item, dict) else item
        text = _clean_text(value) if value else ""
        if text and 2 <= len(text) <= 40:
            return text, url

    return "Visit website", url


def _pricing_warning(profile: dict[str, Any], category: str) -> tuple[Optional[str], list[str]]:
    note = profile.get("pricing_extraction_note")
    warnings: list[str] = []

    if "restaurant" in category or "cafe" in category:
        if note == "menu_page_found_but_prices_not_text_extracted":
            warnings.append(
                "Prices were not included because menu prices were not extracted as visible text."
            )
        elif note == "menu_page_timeout_partial_or_failed":
            warnings.append(
                "Prices were not included because the menu page extraction was partial or timed out."
            )
        elif note:
            warnings.append(str(note).replace("_", " "))

    return note, warnings


def _base_url(profile: dict[str, Any]) -> Optional[str]:
    url = profile.get("final_url") or profile.get("source_url")
    return str(url) if url else None


def _extract_social(profile: dict[str, Any]) -> list[dict[str, str]]:
    """Evidence-backed social profiles (platform + url) from profile.social_presence.

    Verbatim URLs, deduped, capped. Never fabricated — empty when none exist.
    """
    out: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in profile.get("social_presence") or []:
        if not isinstance(item, dict):
            continue
        url = _clean_text(item.get("url"))
        if not url:
            continue
        key = url.lower()
        if key in seen:
            continue
        seen.add(key)
        platform = _clean_text(item.get("platform")) or "link"
        out.append({"platform": platform, "url": url})
        if len(out) >= 6:
            break
    return out


def _absolute_url(base: Optional[str], maybe_url: Optional[str]) -> Optional[str]:
    if not maybe_url:
        return None

    url = str(maybe_url).strip()
    if not url:
        return None

    if url.startswith("data:"):
        return url  # a self-contained data URI (e.g. an inlined SVG logo) IS absolute

    parsed = urlparse(url)
    if parsed.scheme in {"http", "https"}:
        return url

    if base:
        return urljoin(base, url)

    return None


def _candidate_confidence(candidate: dict[str, Any]) -> float:
    for key in ["confidence", "score"]:
        value = candidate.get(key)
        if isinstance(value, (int, float)):
            return max(0.0, min(1.0, float(value)))
    return 0.0


def _candidate_is_reasonable_logo(candidate: dict[str, Any]) -> bool:
    source_type = str(candidate.get("source_type") or "").lower()
    classification = str(candidate.get("classification") or "").lower()
    src = str(candidate.get("src") or "").lower()

    if "favicon" in source_type or "favicon" in src:
        return False

    if "og_image" in source_type or "hero" in classification:
        return False

    # NOTE: .svg is intentionally ALLOWED. The renderer is Playwright/Chromium,
    # which renders SVG natively (the old "Pillow can't render SVG" reject dropped
    # SVG-only brand logos). The template/reel inline an SVG logo as a data URI.

    # Reject hero/banner/cover images by a WHOLE-WORD match, not substring: a
    # substring check rejected legitimate logos whose filename contains the word as
    # part of another token (MEASURED: a "...Transparent_Background_..." brand logo
    # was dropped, forcing a weaker candidate; "discover"/"recover" contain "cover").
    # Underscore is a word char, so "transparent_background" has no \bbackground\b
    # boundary and survives, while a real "site-background.jpg" hero still matches.
    if re.search(r"\b(banner|hero|background|cover)\b", src):
        return False

    return True


# Classifications that are NOT the business's own brand logo (set by the scraper's
# now-trustworthy logo classifier). Never use these as the poster logo.
_NON_BRAND_LOGO_CLASSES = {
    "partner_logo", "sponsor_logo", "initiative_logo", "government_logo",
    "favicon", "og_image",
}

# A candidate the scraper could NOT positively identify as a brand mark
# (classification "unknown_candidate") AND that scored low confidence is almost
# always a CONTENT image — a wide product banner / hero promo — not the logo.
# MEASURED across 37 saved sites: every TRUE logo scored >=0.54 (including a real
# logo the scraper left as unknown_candidate — Qasr Elkbabgi's crest at 0.54); the
# ONLY banner-as-logo disaster (Vodafone's wide "Rateplans.jpg" promo) scored 0.38.
# So below this floor, reject an unknown_candidate and fall back to a clean text
# wordmark instead of rendering a product banner as the brand logo. Defense-in-depth:
# the deeper fix is the scraper classifier + inline-SVG capture (see process.md).
_UNKNOWN_LOGO_MIN_CONF = 0.5


def _is_probably_not_a_logo(classification: str, conf: float) -> bool:
    return classification == "unknown_candidate" and conf < _UNKNOWN_LOGO_MIN_CONF


_LOWRES_LOGO_MARKERS = ("imagepreview", "thumbnail-")


def _logo_res_rank(url: str) -> int:
    """Resolution hint from a CDN URL: 2 = explicit hi-res (preview-1000…), 0 = low-res
    (imagePreview / thumbnail-NxN), 1 = plain/unknown."""
    lu = (url or "").lower()
    if "preview-1000" in lu or "/preview-" in lu:
        return 2
    if any(m in lu for m in _LOWRES_LOGO_MARKERS):
        return 0
    return 1


def _logo_stem(url: str) -> str:
    """Core logo name from a URL's last path segment (drop query, extension, a -ar locale
    suffix) — so 'we-logo-ar?imagePreview=1' and 'preview-1000x0/we-logo.png' share 'we-logo'."""
    import re
    seg = (url or "").lower().split("?")[0].rstrip("/").rsplit("/", 1)[-1]
    seg = re.sub(r"\.\w{2,4}$", "", seg)          # strip extension
    seg = re.sub(r"[-_](ar|en|eg|ar-eg|en-eg)$", "", seg)  # strip locale suffix
    return seg


def _upgrade_to_hires_logo(src, candidates, base, real_raster_src) -> str:
    """When `src` is a low-res logo, return a HIGHER-RES candidate of the SAME logo (matched
    by stem) if one exists; else `src` unchanged. Never raises."""
    try:
        if _logo_res_rank(src) >= 2:
            return src
        stem = _logo_stem(src)
        best_src, best_rank = src, _logo_res_rank(src)
        for c in candidates:
            if not isinstance(c, dict):
                continue
            if str(c.get("classification") or "") in _NON_BRAND_LOGO_CLASSES:
                continue
            cs = real_raster_src(c)
            if not cs or not cs.startswith(("http://", "https://")):
                continue
            # Same logo (stem match) and crisper than what we have.
            if _logo_stem(cs) == stem and _logo_res_rank(cs) > best_rank:
                best_src, best_rank = cs, _logo_res_rank(cs)
        return best_src
    except Exception:
        return src


def _extract_logo(profile: dict[str, Any], business_name: str) -> tuple[Optional[str], str, str, Optional[float]]:
    """
    Returns: logo_url, logo_text, logo_source_type, logo_confidence

    Trusts the scraper, which now (a) selects the brand's OWN logo as
    `primary_logo` and (b) correctly classifies partner/authority logos. So:
    1. use `primary_logo` when it is a real raster image;
    2. else the best raster candidate, EXCLUDING partner/authority/sponsor logos;
    3. else the business name as a text wordmark.
    A `text-wordmark:` pseudo-src is not an image and is skipped.
    """
    visual = profile.get("visual") or {}
    base = _base_url(profile)

    def _real_raster_src(c: dict[str, Any]) -> Optional[str]:
        if not _candidate_is_reasonable_logo(c):
            return None
        src = _absolute_url(base, c.get("src"))
        # Must resolve to a FETCHABLE image URL. Reject the pseudo-srcs the scraper
        # emits for non-file logos: `text-wordmark:<text>` (a nav label) and
        # `inline-svg:<n>` (an inline <svg> with no fetchable URL). The latter was
        # silently WINNING over real raster candidates in the list AND suppressing
        # the wordmark-fallback warning, so brands with an inline-svg logo rendered
        # no logo at all. (Inline-SVG support is a separate scraper-side follow-up.)
        if not src or not src.startswith(("http://", "https://", "data:")):
            return None
        return src

    # 1) The scraper's primary brand logo, when it's a usable raster image.
    primary_logo = visual.get("primary_logo")
    if isinstance(primary_logo, dict):
        src = _real_raster_src(primary_logo)
        conf = _candidate_confidence(primary_logo)
        if src and not _is_probably_not_a_logo(
            str(primary_logo.get("classification") or ""), conf
        ):
            # If the chosen logo is a LOW-RES preview/thumbnail (MEASURED: te.eg's
            # we-logo-ar?imagePreview is 251x71 → its fine Arabic sub-text renders as mush),
            # upgrade to a HIGHER-RES variant of the SAME logo when one exists.
            src = _upgrade_to_hires_logo(src, visual.get("logo_candidates") or [], base, _real_raster_src)
            return src, business_name, str(primary_logo.get("source_type") or "primary_logo"), \
                conf or None

    # 2) Best raster candidate, excluding partner/authority/sponsor logos.
    best: Optional[tuple[float, str, dict[str, Any]]] = None
    for c in (visual.get("logo_candidates") or []):
        if not isinstance(c, dict):
            continue
        if str(c.get("classification") or "") in _NON_BRAND_LOGO_CLASSES:
            continue
        src = _real_raster_src(c)
        if not src:
            continue
        conf = _candidate_confidence(c)
        if conf <= 0:
            continue
        # Skip a low-confidence unknown_candidate: it's a content banner, not a logo.
        if _is_probably_not_a_logo(str(c.get("classification") or ""), conf):
            continue
        if best is None or conf > best[0]:
            best = (conf, src, c)
    if best:
        conf, src, c = best
        return src, business_name, str(c.get("source_type") or "logo_candidate"), conf or None

    # 3) Legacy single field, only if it is a FETCHABLE image URL (http/data). The
    # scraper's pseudo-srcs (`text-wordmark:`, `inline-svg:`) must NOT leak here — they
    # render as a broken <img>. (Same contract as `_real_raster_src` for steps 1-2.)
    legacy = _absolute_url(base, visual.get("logo_url"))
    if legacy and legacy.startswith(("http://", "https://", "data:")):
        return legacy, business_name, "legacy_logo_url", None

    # 4) No usable raster -> wordmark using the brand name.
    return None, business_name, "wordmark_fallback", None


def _headline_fitness(text: str) -> float:
    """How 'headline-like' a VERBATIM scraped line is (higher = better).

    Deterministic and language-agnostic (char/word counts work for Arabic too).
    Rewards concise, multi-word lines in a poster-headline length band; penalizes
    single words, over-long lines, full sentences, and comma list run-ons. This
    is SELECTION ONLY — it never edits or invents text (zero-hallucination intact).
    """
    t = (text or "").strip()
    n = len(t)
    words = t.split()
    if n < 6 or len(words) < 2:          # too short / single word -> weak headline
        return 0.0
    if n > 90 or len(words) > 16:        # too long for a headline
        return 0.10
    # PLATEAU: a punchy 2-3 word slogan ("الرواد الرقميون", "Just Do It") is a
    # GREAT headline, so don't penalize the short end — full score across the
    # natural headline band, gentle falloff only outside it.
    if 10 <= n <= 55:
        length_score = 1.0
    elif n < 10:
        length_score = max(0.0, 1.0 - (10 - n) / 10.0)
    else:                                 # 55 < n <= 90
        length_score = max(0.0, 1.0 - (n - 55) / 50.0)
    sentence_penalty = 0.15 if t.endswith((".", "،", "؛", "!", "؟", "?")) else 0.0
    comma_penalty = 0.10 * min(t.count(",") + t.count("،"), 3)
    return max(0.0, length_score - sentence_penalty - comma_penalty)


def _select_headline(
    profile: dict[str, Any], business_name: str, desc_sentence: Optional[str],
) -> Optional[str]:
    """Pick the STRONGEST verbatim line as the poster headline.

    Candidates (all real scraped evidence): tagline, value propositions, and the
    first sentence of the description. The tagline gets a small bonus (it is the
    brand's own chosen slogan), but a punchier value-prop can still win. Returns
    None only if nothing usable exists (caller falls back to the business name).
    """
    candidates: list[tuple[float, str]] = []

    tagline = _clean_text(_field_value(profile, "tagline") or "")
    if tagline:
        # Slogan bonus: the tagline is the brand's OWN chosen line, so it wins
        # ties against feature-y value props — but a clearly stronger value prop
        # (e.g. when the tagline is a weak filler like "Stay Up To Date") can win.
        candidates.append((_headline_fitness(tagline) + 0.25, tagline))

    for vp in profile.get("value_propositions") or []:
        value = vp.get("value") if isinstance(vp, dict) else vp
        text = _clean_text(value) if value else ""
        if text:
            candidates.append((_headline_fitness(text), text))

    # A UNIQUE competitive edge (catch-all field) makes a strong, differentiated headline —
    # give it a small bonus so a genuine edge can beat a generic value prop (still below
    # the brand's own tagline on ties).
    for ins in profile.get("other_unique_insights") or []:
        value = ins.get("value") if isinstance(ins, dict) else ins
        text = _clean_text(value) if value else ""
        if text:
            candidates.append((_headline_fitness(text) + 0.15, text))

    if desc_sentence:
        candidates.append((_headline_fitness(desc_sentence), desc_sentence))

    # Drop lines that are just the business name, and any zero-fitness line.
    candidates = [
        (score, text) for (score, text) in candidates
        if text and score > 0 and not _looks_like_business_name(text, business_name)
    ]
    if not candidates:
        return None
    candidates.sort(key=lambda x: x[0], reverse=True)
    return candidates[0][1]


def build_poster_brief(
    profile: dict[str, Any], *, headline_override: Optional[str] = None
) -> PosterBrief:
    business_name = _clean_business_name(_field_value(profile, "name"))
    # GROUNDING: trust the scraped/extracted category (the EvidencedField on the
    # profile) when present; only fall back to keyword inference when it is absent.
    # Stops a keyword re-classification from overriding the real category and
    # cascading into the palette fallback + art-director scene.
    _scraped_cat = _field_value(profile, "category")
    category = str(_scraped_cat).strip().lower() if _scraped_cat else _infer_category(profile)

    description = _field_value(profile, "description")
    tone = _field_value(profile, "tone_of_voice")

    palette, primary_color = _extract_palette(profile, category)
    offerings = _extract_offerings(profile, business_name, category)
    cta_text, cta_url = _cta_from_profile(profile)
    contact_line = _extract_contact_line(profile)
    pricing_note, warnings = _pricing_warning(profile, category)

    logo_url, logo_text, logo_source_type, logo_confidence = _extract_logo(profile, business_name)

    # A rasterized inline-svg logo carries a chip preference: a light/white mark needs a
    # DARK plate to stay visible, otherwise the default light plate. Only trust it when
    # the chosen logo IS that rasterized primary (source_type set by the enricher).
    logo_chip = "light"
    if logo_source_type == "inline_svg_rasterized":
        _chip = ((profile.get("visual") or {}).get("primary_logo") or {}).get("logo_chip")
        if _chip in ("light", "dark"):
            logo_chip = _chip

    # Brand typography from the scraped visual identity (may be a private/generic
    # family name like "myfont2"/"system-ui"; the template decides loadability).
    _visual = profile.get("visual") or {}
    heading_font = _clean_text(_visual.get("heading_font")) or None
    body_font = _clean_text(_visual.get("body_font")) or None

    # Headline + subheadline: evidence-only, no invented marketing copy.
    # SELECTION (not generation): pick the strongest verbatim line (tagline /
    # value-prop / description sentence) via _select_headline; fall back to the
    # business name (a factual identity, never a fabricated claim).
    desc_sentence = _first_sentence(description)

    # A planned headline (e.g. a content-calendar item's hook, or a trend angle) wins
    # when provided; otherwise SELECT the strongest verbatim line from evidence.
    headline = (
        headline_override.strip()
        if (headline_override and headline_override.strip())
        else (_select_headline(profile, business_name, desc_sentence) or business_name)
    )
    # Subheadline = the description sentence, unless it's already the headline
    # (don't echo the same line twice).
    subheadline = desc_sentence if (desc_sentence and desc_sentence != headline) else None

    if not logo_url:
        warnings.append("No confident raster logo found; poster used text wordmark fallback.")

    return PosterBrief(
        business_name=business_name,
        category=category,
        headline=headline,
        subheadline=subheadline,
        offerings=offerings,
        cta_text=cta_text,
        cta_url=cta_url,
        contact_line=contact_line,
        palette_hex=palette,
        primary_color=primary_color,
        tone=str(tone) if tone else None,
        logo_url=logo_url,
        logo_text=logo_text,
        logo_source_type=logo_source_type,
        logo_confidence=logo_confidence,
        logo_chip=logo_chip,
        pricing_note=pricing_note,
        warnings=warnings,
        social=_extract_social(profile),
        heading_font=heading_font,
        body_font=body_font,
    )