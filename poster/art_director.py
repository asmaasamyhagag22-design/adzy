from __future__ import annotations

import logging
import random
from typing import Any, Literal, Optional
from urllib.parse import urlparse

from pydantic import BaseModel

from poster.schemas import PosterArtDirection, PosterBrief, PosterDesignSpec

logger = logging.getLogger(__name__)


# Which on-poster elements the LLM may choose to show (validated against this set).
_SHOW_ALLOWED = {"logo", "kicker", "headline", "sub", "offerings", "cta", "contact", "social"}

# Where each layout reserves its calm area — shared with the background prompt so the
# generated image leaves empty space exactly where the text block lands.
_ZONE_PHRASE = {
    "bottom": "lower third", "top": "upper third",
    "left": "left half", "right": "right half", "center": "central area",
}


def _zone_phrase(zone: Optional[str]) -> str:
    return _ZONE_PHRASE.get(zone or "bottom", "lower third")


def _calm_zone_instruction(zone: Optional[str], text_box: Optional[list] = None) -> str:
    """Reserve the text area as a NATURAL calm part of the SAME photo — never a flat color
    block. Imagen otherwise satisfies 'GENEROUS empty negative space' by painting a hard
    color-blocked panel that splits the frame (MEASURED: a cream diagonal eating ~40% of the
    Digilians background). So we ask for an IN-SCENE calm zone, sized to the text, and forbid
    the flat block explicitly. When the LLM chose a free `text_box`, name the PRECISE band that
    must stay clear of the subject (Step 2: strict, placement-aware calm zone)."""
    base = (
        f"Keep the {_zone_phrase(zone)} a naturally calm, uncluttered part of the SAME single "
        "photo — a softly-lit wall, a shallow-focus background, or gentle atmospheric depth — "
        "sized just enough for the overlaid text plus clean margins. It MUST be a real part of "
        "the scene, NEVER a flat color block, a solid color field, a hard-edged or color-blocked "
        "panel, or a diagonal band dividing the frame."
    )
    if not text_box or len(text_box) < 3:
        return base
    try:
        x, y, w = float(text_box[0]), float(text_box[1]), float(text_box[2])
    except Exception:
        return base
    if y >= 0.45:
        band = f"the BOTTOM ~{max(20, min(60, round((1.0 - y) * 100)))}% of the image"
    elif (y + 0.30) <= 0.60 and zone in (None, "top"):
        band = f"the TOP ~{max(20, min(60, round((y + 0.32) * 100)))}% of the image"
    else:
        band = f"the {_zone_phrase(zone)} (a vertical band around x {round(x * 100)}%-{round((x + w) * 100)}%)"
    return (
        f"CRITICAL TEXT SAFE ZONE: keep {band} ENTIRELY clear of the main subject's face and any "
        "important detail — place the subject OUTSIDE it. That area must be a smooth, calm, "
        "low-detail part of the SAME photo (soft-lit wall, shallow focus, or gentle in-scene "
        "gradient) reserved for overlaid text. " + base
    )


def _hex_to_color_name(hx: Any) -> Optional[str]:
    """Coarse natural-language name for a hex color (e.g. '#133B66' -> 'deep navy blue').

    Image models BAKE raw hex strings into the picture as visible text (MEASURED:
    '#133B66 #B19257' rendered in a Digilians background) despite 'no text' rules.
    So the brand palette is described to the image model by NAME, never as hex.
    """
    import colorsys
    h = str(hx or "").strip().lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    if len(h) != 6:
        return None
    try:
        r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    except ValueError:
        return None
    hue, light, sat = colorsys.rgb_to_hls(r / 255, g / 255, b / 255)
    H = hue * 360
    if sat < 0.12:
        if light > 0.85:
            return "white"
        if light < 0.15:
            return "near-black"
        if light < 0.35:
            return "charcoal grey"
        return "soft grey"
    qual = "deep " if light < 0.32 else ("bright " if light > 0.62 else "")
    if H < 15 or H >= 345:
        name = "red"
    elif H < 40:
        name = "orange"
    elif H < 65:
        name = "gold" if light < 0.6 else "yellow"
    elif H < 95:
        name = "lime green"
    elif H < 150:
        name = "green"
    elif H < 195:
        name = "teal"
    elif H < 235:
        name = "navy blue" if light < 0.42 else "blue"
    elif H < 280:
        name = "indigo"
    elif H < 320:
        name = "purple"
    else:
        name = "magenta"
    if name in ("orange", "gold") and light < 0.30:
        name = "brown"
    return (qual + name).strip()


def _palette_names(palette_hex: Optional[list]) -> str:
    """Natural-language palette for the IMAGE prompt (never raw hex — see above)."""
    names: list[str] = []
    for hx in (palette_hex or [])[:4]:
        n = _hex_to_color_name(hx)
        if n and n not in names:
            names.append(n)
    return ", ".join(names)


def _clean(value: Any) -> str:
    return " ".join(str(value or "").strip().split())


def _palette_text(colors: list[str]) -> str:
    if not colors:
        return "a professional brand palette"
    return ", ".join(colors[:5])


def _safe_overlay_copy(brief: PosterBrief) -> str:
    """
    Keep overlay copy short. The full profile description is too long for a poster.
    """
    parts: list[str] = []

    if brief.headline:
        parts.append(brief.headline)

    if brief.offerings:
        parts.append(brief.offerings[0])

    text = " • ".join(parts)
    if len(text) <= 105:
        return text

    return text[:102].rstrip(" ,.;:") + "..."


def _category_key(category: str | None) -> str:
    c = (category or "").lower()

    if "restaurant" in c or "cafe" in c:
        return "restaurant"

    if "education" in c or "training" in c or "institute" in c:
        return "education"

    if "medical" in c or "clinic" in c or "doctor" in c:
        return "medical"

    if "skincare" in c or "beauty" in c or "cosmetic" in c:
        return "skincare"

    return "business"


def _choose_layout(category: str) -> str:
    rng = random.SystemRandom()

    if category == "education":
        return rng.choice(
            [
                "clean_institutional",
                "split_editorial",
                "magazine_cover",
                "bottom_band",
            ]
        )

    if category == "restaurant":
        return rng.choice(
            [
                "hero_overlay",
                "magazine_cover",
                "bottom_band",
                "split_editorial",
            ]
        )

    if category == "medical":
        return rng.choice(
            [
                "split_editorial",
                "clean_institutional",
                "bottom_band",
            ]
        )

    if category == "skincare":
        return rng.choice(
            [
                "hero_overlay",
                "magazine_cover",
                "bottom_band",
            ]
        )

    return rng.choice(
        [
            "magazine_cover",
            "split_editorial",
            "bottom_band",
            "clean_institutional",
        ]
    )


def _layout_prompt(layout: str) -> str:
    if layout == "magazine_cover":
        return (
            "Composition strategy: premium magazine-cover style. "
            "Strong visual subject or atmosphere, dramatic depth, clear negative space around the center. "
            "Do not place busy objects where the main headline will be overlaid."
        )

    if layout == "split_editorial":
        return (
            "Composition strategy: editorial split layout. "
            "Place the main visual interest on one side and keep the opposite side cleaner for overlay text. "
            "Use premium campaign photography aesthetics."
        )

    if layout == "bottom_band":
        return (
            "Composition strategy: image-led poster with a clean lower-third area. "
            "Keep the top and middle visually rich, and leave the bottom area darker or calmer for CTA overlay."
        )

    if layout == "clean_institutional":
        return (
            "Composition strategy: clean institutional campaign layout. "
            "Use structured space, premium professional lighting, modern geometric depth, and clear overlay zones."
        )

    return (
        "Composition strategy: hero overlay. "
        "Create one strong visual scene with clear negative space for title, CTA, and logo."
    )


def build_art_direction(
    brief: PosterBrief,
    profile: dict[str, Any] | None = None,
) -> PosterArtDirection:
    """
    Build a creative direction from the scraped business profile.

    OpenAI generates background/scene only.
    Pillow overlays the exact logo, text, CTA, and contact.
    """
    category = _category_key(brief.category)
    layout = _choose_layout(category)

    palette = _palette_text(brief.palette_hex)
    offerings = ", ".join(brief.offerings[:3]) if brief.offerings else "core offerings"
    tone = brief.tone or "professional"
    brand = brief.business_name
    headline = brief.headline
    subheadline = brief.subheadline or ""

    layout_instruction = _layout_prompt(layout)

    source_fields = [
        "business_name",
        "category",
        "headline",
        "subheadline",
        "offerings",
        "palette_hex",
        "tone",
        "logo",
        "cta",
    ]

    if category == "restaurant":
        concept = "Premium restaurant campaign background"
        mood = "warm, appetizing, premium, hospitality-led"
        background_style = (
            "premium restaurant photography, authentic cuisine, elegant table atmosphere, "
            "cinematic lighting, realistic depth, refined hospitality"
        )
        category_prompt = f"""
Create a premium vertical marketing poster BACKGROUND for a restaurant brand.

Brand context:
- Brand name: {brand}
- Headline: {headline}
- Description: {subheadline}
- Offerings: {offerings}
- Tone: {tone}
- Brand palette: {palette}

Visual direction:
Show an elegant dining atmosphere with authentic cuisine, refined hospitality, and premium presentation.
Use appetizing lighting and brand-safe tones inspired by the palette.
Make it look like a real high-quality social media campaign visual.

{layout_instruction}

Strict rules:
No text. No words. No logo. No watermark. No readable menu. No price tags.
Do not generate typography or signage.
""".strip()

    elif category == "education":
        concept = "Modern education and career development campaign background"
        mood = "modern, credible, technology-oriented, aspirational"
        background_style = (
            "modern ICT learning environment, professional training, digital screens, "
            "career development, clean institutional design, realistic premium look"
        )
        category_prompt = f"""
Create a professional vertical marketing poster BACKGROUND for an education and training institute.

Brand context:
- Brand name: {brand}
- Headline: {headline}
- Description: {subheadline}
- Programs / offerings: {offerings}
- Tone: {tone}
- Brand palette: {palette}

Visual direction:
Show a modern technology-oriented learning environment.
Include subtle cues of ICT, networking, digital learning, certifications, training, and career growth.
The scene must feel premium, credible, institutional, and realistic.
Use a clean professional palette inspired by the brand colors.

{layout_instruction}

Strict rules:
No text. No words. No logo. No watermark. No fake certificate text. No readable signage.
Do not generate typography.
""".strip()

    elif category == "medical":
        concept = "Clean trustworthy healthcare campaign background"
        mood = "calm, clinical, trustworthy, reassuring"
        background_style = (
            "modern clinic interior, clean surfaces, soft natural light, professional healthcare atmosphere, "
            "subtle human warmth, no procedure scene"
        )
        category_prompt = f"""
Create a clean vertical marketing poster BACKGROUND for a medical clinic brand.

Brand context:
- Brand name: {brand}
- Headline: {headline}
- Services / offerings: {offerings}
- Tone: {tone}
- Brand palette: {palette}

Visual direction:
Show a modern clinic environment with a calm, trustworthy, professional atmosphere.
Use soft light, clean composition, and reassuring brand-safe colors.
The image should be suitable for compliant medical marketing and human review.

{layout_instruction}

Safety:
Do not show identifiable patients.
Do not show before/after imagery.
Do not show procedures, needles, blood, invasive treatment, or dramatic symptoms.
Do not imply guaranteed results.

Strict rules:
No text. No words. No logo. No watermark. No readable signage.
Do not generate typography.
""".strip()

    elif category == "skincare":
        concept = "Premium skincare campaign background"
        mood = "soft, clean, aspirational, ingredient-aware"
        background_style = (
            "premium skincare routine scene, soft light, clean surface, gentle cosmetic aesthetic, "
            "elegant product-like composition"
        )
        category_prompt = f"""
Create a premium vertical marketing poster BACKGROUND for a skincare or beauty brand.

Brand context:
- Brand name: {brand}
- Headline: {headline}
- Offerings: {offerings}
- Tone: {tone}
- Brand palette: {palette}

Visual direction:
Show a clean premium skincare routine scene with soft lighting, refined surfaces, elegant textures,
and aspirational but realistic beauty aesthetics.

{layout_instruction}

Compliance:
Do not imply medical treatment or guaranteed results.
Do not show skin disease, before/after comparisons, or clinical claims.

Strict rules:
No text. No words. No logo. No watermark. No readable labels.
Do not generate typography.
""".strip()

    else:
        concept = "Premium business campaign background"
        mood = "professional, modern, brand-led"
        background_style = (
            "premium abstract business campaign scene, modern brand visual, clean composition, "
            "elegant lighting and depth"
        )
        category_prompt = f"""
Create a premium vertical marketing poster BACKGROUND for a business brand.

Brand context:
- Brand name: {brand}
- Headline: {headline}
- Offerings: {offerings}
- Tone: {tone}
- Brand palette: {palette}

Visual direction:
Create a modern, professional, brand-safe campaign background with clean visual hierarchy.

{layout_instruction}

Strict rules:
No text. No words. No logo. No watermark. No readable signage.
Do not generate typography.
""".strip()

    from poster.contracts import IMAGE_NEGATIVE_TERMS
    negative_prompt = IMAGE_NEGATIVE_TERMS   # the ONE shared image negative (Batch 3)

    return PosterArtDirection(
        provider_prompt=category_prompt,
        negative_prompt=negative_prompt,
        concept=concept,
        category=category,
        mood=mood,
        layout=layout,  # type: ignore[arg-type]
        background_style=background_style,
        safe_overlay_copy=_safe_overlay_copy(brief),
        color_notes=[
            "Background is generated by OpenAI.",
            "Logo, business name, CTA, and contact are added by Pillow.",
            "Important text is never generated inside the image.",
            "Overlay layout is randomized per generation.",
        ],
        source_fields_used=source_fields,
    )


def build_creative_prompt(brief: PosterBrief, bake_text: bool = False, zone: str = "bottom",
                          variation: dict | None = None) -> str:
    """Ultra-minimal, concept-driven advertising-poster prompt.

    Default (bake_text=False, RECOMMENDED): a TEXT-FREE creative concept with clean
    empty zones — the real logo + minimal copy are overlaid afterward (crisp, and
    Arabic-capable). bake_text=True asks the model to render the headline/CTA INSIDE
    the image; only viable for short English (it garbles Arabic and misspells text).
    Universal: the concept derives from the business's own category + hero offering,
    never a hardcoded vertical gag.
    """
    category = (brief.category or "brand").replace("_", " ")
    hero = brief.offerings[0] if brief.offerings else category
    palette = _palette_names(brief.palette_hex) or "a refined, premium brand palette"

    concept = f"""
A bold, ultra-minimal advertising poster for a {category} brand — ONE striking
creative concept, not a busy scene. Surreal forced-perspective with a single
dramatic hero subject inspired by "{hero}" as the focal point. Premium editorial /
high-fashion campaign aesthetic, vibrant yet clean,
cinematic studio lighting, soft shadows, photorealistic, magazine-quality, 8K.

Brand color palette: {palette}.""".strip()

    from poster.variation import concept_variation_cue
    var_cue = concept_variation_cue(variation)
    if var_cue:
        concept += f"\n\n{var_cue}"

    if bake_text:
        headline = _clean(brief.headline)
        cta = _clean(brief.cta_text)
        text_block = (
            "Typography baked into the image, rendered crisply and CORRECTLY, minimal "
            "and modern:\n"
            f'- a short headline reading exactly: "{headline}"\n'
            f'- a small pill call-to-action button reading exactly: "{cta}"\n'
            "Clean Latin typography only; no other words, captions, watermarks, or logos."
        )
    else:
        text_block = (
            "ABSOLUTELY NO text, words, letters, numbers, logos, captions, or signage "
            f"anywhere in the image. {_calm_zone_instruction(zone)} Keep a clean corner for a logo."
        )

    # CS-02: this static fallback (used when there's no caller / the LLM failed / returned empty)
    # never carried the ad-safety gate either, so a clinic falling back here shipped ungated. Add
    # it in the same \n\n-separated style. Empty (compliance_for -> "") for non-regulated categories.
    from poster.contracts import compliance_for
    _c = compliance_for(brief.category)
    tail = "Vertical 4:5 poster, centered composition, advertising-campaign style."
    if _c:
        tail = f"{_c}\n\n{tail}"
    return f"{concept}\n\n{text_block}\n\n{tail}"


class _CreativeConceptResponse(BaseModel):
    """Structured output from the LLM art-director."""
    concept_title: str
    image_prompt: str


def _profile_field_value(profile: dict[str, Any] | None, key: str) -> str:
    field = (profile or {}).get(key)
    if isinstance(field, dict):
        return _clean(field.get("value"))
    return _clean(field)


def _persona_lines(profile: dict[str, Any] | None) -> str:
    """Brand-persona block for the art-director — every line is verbatim scraped
    evidence from the profile (zero hallucination: omitted when absent)."""
    if not profile:
        return ""
    lines: list[str] = []
    tagline = _profile_field_value(profile, "tagline")
    if tagline:
        lines.append(f"- Tagline (verbatim): {tagline}")
    desc = _profile_field_value(profile, "description")
    if desc:
        lines.append(f"- What they do: {desc[:300]}")
    props = []
    for item in (profile.get("value_propositions") or [])[:3]:
        v = _clean(item.get("value") if isinstance(item, dict) else item)
        if v:
            props.append(v)
    if props:
        lines.append("- Value propositions (verbatim): " + " | ".join(props))
    audience = _profile_field_value(profile, "audience_type")
    if audience:
        lines.append(f"- Audience: {audience}")
    langs = [str(x) for x in (profile.get("languages") or []) if x]
    if langs:
        lines.append("- Site languages: " + ", ".join(langs))
    return "\n".join(lines)


def _brandbook_style_lines(brand_book: Any) -> str:
    """The brand's OWN visual world, learned by the BrandBook from its real photos —
    fed to image generation so the scene is a fresh, on-brand photo (understand → create),
    NOT a reused website image and NOT a generic metaphor. Duck-typed (no hard import)."""
    if brand_book is None:
        return ""
    vi = getattr(brand_book, "visual_identity", None)
    if vi is None:
        return ""
    parts: list[str] = []
    for label, attr in (
        ("Photography style", "photography_style"),
        ("Aesthetic", "aesthetic"),
        ("Mood", "mood"),
        ("Color story", "color_story"),
    ):
        v = (getattr(vi, attr, "") or "").strip()
        if v:
            parts.append(f"- {label}: {v}")
    aud = (getattr(brand_book, "audience", "") or "").strip()
    if aud:
        parts.append(f"- Audience: {aud[:180]}")
    return "\n".join(parts)


def _country(profile: dict[str, Any] | None) -> str:
    """The brand's country. CS-13/P-08 (2026-07-18): this used to carry its OWN 18-entry MENA ccTLD
    table (a subset of poster.locale's 60) and read the ccTLD ONLY — a .com brand lost its region
    anchor. Route through the SINGLE, richer resolver (ccTLD -> service_areas -> phone e164 -> "")
    that the oneshot poster engine already trusts, so both engines depict the same nationality and
    there is one country map to maintain, not two that drift."""
    try:
        from poster.locale import country_of
        return country_of(profile or {})
    except Exception:  # noqa: BLE001 — never break generation over a locale lookup
        return ""


def _region_line(brief: PosterBrief, profile: dict[str, Any] | None) -> str:
    """A culture/region anchor so generation depicts the brand's ACTUAL nationality and
    doesn't drift to generic Western (MEASURED: a European street) or generic Gulf/Arab
    (owner: an Egyptian brand got a Khaleeji hijab) imagery."""
    country = _country(profile)
    if country:
        return (
            f"set in {country}, featuring authentic local people from {country} in their "
            f"own everyday style and dress (NOT Gulf/Khaleeji and NOT a generic 'Arab' "
            f"look); a real {country} environment — NOT Western or European architecture, "
            f"streets, signage, or people"
        )
    langs = [str(x).lower() for x in ((profile or {}).get("languages") or [])]
    is_ar = any(l.startswith("ar") for l in langs)
    if not is_ar:
        try:
            from poster.template import _is_rtl
            is_ar = _is_rtl(brief.headline) or _is_rtl(brief.business_name)
        except Exception:
            pass
    if is_ar:
        return ("set in an authentic Middle Eastern environment with real local people; "
                "NOT Western or European architecture, streets, signage, or people")
    return ""


# A concrete, MODERN environment per category — so the scene is the brand's real
# workplace, not a generic warm "bohemian/editorial" interior (owner: "the bohemian
# background has nothing to do with the brand"). Matched by substring; universal
# (derived from the scraped category, not a per-brand hack).
_ENV_BY_CATEGORY = {
    "education": ("a modern, bright technology training space — laptops and large "
                  "screens, a contemporary classroom or innovation lab, clean and professional"),
    "training": ("a modern training/innovation lab — laptops, large screens, clean "
                 "contemporary setting"),
    "technology": ("a modern tech workspace — screens, devices, networking gear, clean "
                   "contemporary lines"),
    "software": "a modern software studio — monitors with code, clean contemporary workspace",
    "cafe": "a warm, modern specialty cafe",
    "restaurant": "an inviting, well-styled dining space true to the cuisine",
    "medical": "a clean, modern, reassuring clinic interior with soft daylight",
    "clinic": "a clean, modern, reassuring clinic interior with soft daylight",
    "skincare": "a clean, premium skincare/beauty setting with soft light",
    "beauty": "an elegant, modern beauty studio",
    "retail": "a stylish, modern, well-lit retail space",
    "ecommerce": "a clean, modern lifestyle product setting",
    "fitness": "an energetic, modern gym with dynamic light",
    "real estate": "bright, beautifully staged modern interiors",
}


def _env_for_category(category: Optional[str]) -> str:
    c = (category or "").lower()
    for key, env in _ENV_BY_CATEGORY.items():
        if key in c:
            return env
    return ""


def _subject_line(brief: PosterBrief) -> str:
    """The concrete on-brand subject the scene must depict (its real activity + its real
    modern environment), so the image is about THIS business — not a random studio
    portrait, a warm bohemian interior, or an unrelated scene.

    NOTE: we deliberately do NOT inject the offering's marketing NAME (e.g. "Train To
    Hire (4 Month)") — image models BAKE such branded phrases into the scene as visible
    text (MEASURED on NTI). The category + concrete environment ground the scene without
    any bakeable copy; the real offering text lives in the overlay."""
    cat = (brief.category or "").replace("_", " ").strip() or "the brand's activity"
    base = f"real people authentically engaged in {cat}"
    env = _env_for_category(brief.category)
    if env:
        base += f", in {env}"
    return base


def _brand_dna_lines(brand_dna: Any) -> str:
    """The brand's LEARNED visual design language (from BrandCreativeDNA — reverse-engineered
    from its REAL creatives). When present, this is the dominant style instruction: it tells
    the image model to render in the brand's OWN language (e.g. WE: composite + purple light
    + dramatic rim-lit subject), which deliberately OVERRIDES the generic natural-light
    defaults — the brand's measured reality wins. Empty unless a vision DNA exists."""
    if brand_dna is None or not getattr(brand_dna, "used_vision", False):
        return ""
    parts: list[str] = []
    for label, attr in (
        ("Imagery style", "imagery_style"),
        ("Colour usage", "color_usage"),
        ("Composition", "composition_patterns"),
        ("Signature moves", "signature_moves"),
        ("Mood", "mood"),
    ):
        v = (getattr(brand_dna, attr, "") or "").strip()
        if v:
            parts.append(f"- {label}: {v}")
    block = "\n".join(parts)
    do = [s for s in (getattr(brand_dna, "do_list", None) or []) if s][:6]
    dont = [s for s in (getattr(brand_dna, "dont_list", None) or []) if s][:6]
    if do:
        block += "\nDO: " + "; ".join(do)
    if dont:
        block += "\nDO NOT: " + "; ".join(dont)

    # THE DNA HAS TWO AUDIENCES AND THIS ONE IS THE SCENE. Learned from the brand's real
    # creatives — which include its PACKS — the block arrives full of packaging-design
    # instructions, and this function used to paste all of it into a prompt whose next paragraph
    # says "DO NOT DRAW THE PRODUCT" and "NO logos anywhere". MEASURED on one real assembled
    # prompt: 39 rule clauses, one every 26 words, with logo/product/text each simultaneously
    # demanded and forbidden. The model resolved that the only way it can — it drew a pack.
    # `brand.dna_scope` keeps what a photograph can obey and drops what only a pack designer can.
    try:
        from brand.dna_scope import dropped_clauses, scene_safe
        trimmed = scene_safe(block)
        dropped = dropped_clauses(block)
        if dropped:
            # Never a silent filter: a brand whose DNA is ALL packaging should be visible as such.
            logger.info("[dna] %s packaging/layout clause(s) withheld from the scene prompt "
                        "(e.g. %s)", len(dropped), (dropped[0] or "")[:70])
        return trimmed
    except Exception:  # noqa: BLE001 — a filter failure must not cost the brand its whole DNA
        return block


def build_llm_concept_prompt(
    brief: PosterBrief,
    caller: Optional[Any] = None,
    profile: dict[str, Any] | None = None,
    spec: Optional[PosterDesignSpec] = None,
    brand_book: Any = None,
    variation: dict | None = None,
    brand_dna: Any = None,
    scene_idea: str | None = None,
) -> str:
    """LLM art-director: invent a UNIQUE, text-free visual concept per business.

    `scene_idea` (the Creative Concept's visual_idea) — when given, it is the scene the
    image must depict, so the picture expresses the SAME single message as the copy.

    Falls back to the static build_creative_prompt() when no caller is supplied or
    the call fails. ZERO HALLUCINATION: the concept is purely VISUAL (a metaphor
    for what the business does) — it states no factual claims; the real copy stays
    in the overlay. The image is always text-free (text is overlaid later).

    When `profile` is given, the concept is grounded in the brand's scraped
    PERSONA and its VISUAL IDENTITY; with `brand_dna` it renders in the brand's
    learned design language.
    """
    # The text block lands in this zone -> the image must keep it calm there. The free
    # text_box (when present) lets us name the PRECISE band the subject must stay out of.
    zone = spec.negative_space_zone if spec else "bottom"
    text_box = spec.text_box if spec else None
    if caller is None:
        return build_creative_prompt(brief, zone=zone, variation=variation)

    offerings = "; ".join([o for o in (brief.offerings or [])[:5] if o]) or (brief.category or "its offerings")
    palette = _palette_names(brief.palette_hex) or "a premium brand palette"
    persona = _persona_lines(profile)
    style = _brandbook_style_lines(brand_book)

    if style:
        # UNDERSTAND → CREATE: the BrandBook learned the brand's real visual world from its
        # own photos; generate a FRESH photo in that exact world (not a reused site image,
        # not an abstract metaphor) — this is the value over copy-pasting the website image.
        system = (
            "You are an award-winning advertising art director. Using the brand's OWN visual "
            "world below (learned from its real photos), write a detailed image-generation "
            "prompt for a FRESH, photorealistic, on-brand scene — a NEW photo the brand "
            "itself could have shot. NOT a reused stock image, NOT an abstract metaphor.\n"
            "STRICT RULES:\n"
            "- Show the brand's people ACTIVELY DOING what the brand actually does (its "
            "category + real offerings, e.g. learners using laptops in a tech lab, diners at "
            "the table) — a real moment, NOT a static studio portrait of one person.\n"
            "- Depict the brand's REAL world: its real kind of people, place, and activity, "
            "in its actual photography style (below). Authentic and specific to THIS brand.\n"
            "- Match the brand's mood + color story; the brand palette leads the lighting.\n"
            "- Respect the audience's cultural context (Arabic brands: regionally authentic "
            "people and settings, never clichés).\n"
            "- Photorealistic premium campaign photography; cinematic lighting.\n"
            f"- {_calm_zone_instruction(zone, text_box)}\n"
            "- ABSOLUTELY NO text, words, letters, numbers, logos, or signage in the image.\n"
            "- Vertical 4:5. Purely visual: no factual claims, prices, or guarantees."
        )
    else:
        system = (
            "You are an award-winning advertising art director. Invent ONE striking, "
            "surreal or metaphorical VISUAL concept for a premium vertical ad poster for "
            "the given business, written as a detailed image-generation prompt.\n"
            "STRICT RULES:\n"
            "- ONE bold hero concept — a visual metaphor for what the business does; not a busy scene.\n"
            "- EMBODY the brand persona below: the concept's subject, mood, and cultural "
            "context must feel like THIS specific brand, not its generic category.\n"
            "- The brand palette is the scene's DOMINANT color story (lighting, surfaces, "
            "atmosphere) — accent tones may vary, but the brand colors must lead.\n"
            "- Respect the audience's cultural context (e.g. Arabic-language brands: "
            "regionally authentic settings, props, and people — never clichés).\n"
            "- Photorealistic, editorial, high-end campaign aesthetic; cinematic lighting.\n"
            f"- {_calm_zone_instruction(zone, text_box)}\n"
            "- ABSOLUTELY NO text, words, letters, numbers, logos, or signage in the image.\n"
            "- Vertical 4:5. Purely visual: no factual claims, prices, or guarantees."
        )

    from poster.contracts import compliance_for
    _compliance = compliance_for(brief.category)   # ad-safety on the LLM image path (was missing)
    user = (
        f"Business: {brief.business_name}\n"
        f"Category: {brief.category}\n"
        + (f"{_compliance}\n" if _compliance else "")
        + f"Real offerings: {offerings}\n"
        f"Tone: {brief.tone or 'premium'}\n"
        f"Brand palette: {palette}\n"
        + (f"\nThe brand's OWN visual world (from its real photos):\n{style}\n" if style else "")
        + (f"\nBrand persona (all verbatim from the real website):\n{persona}\n" if persona else "")
        + "\nReturn concept_title and a vivid, text-free image_prompt."
    )

    try:
        concept, _usage = caller(system, user, _CreativeConceptResponse, group_name="poster_concept")
        prompt = (getattr(concept, "image_prompt", "") or "").strip()
    except Exception:
        return build_creative_prompt(brief, zone=zone, variation=variation)

    if not prompt:
        return build_creative_prompt(brief, zone=zone, variation=variation)

    # Reinforce the hard constraints regardless of what the LLM returned.
    # LEAD WITH GROUNDING. Imagen weights the START of the prompt most, and a long free
    # LLM scene + a generic "stock photo" style description made it WANDER (MEASURED: a
    # Western man / a European street for an Egyptian tech brand). So the concrete subject
    # + region + brand palette go FIRST as the dominant instruction; the LLM's prose is
    # demoted to a short, secondary creative cue; generic "stock/AI" style noise is dropped.
    region = _region_line(brief, profile)
    # The Creative Concept's visual_idea (when supplied) IS the scene — so the picture
    # expresses the same single message as the copy; else the generic on-brand subject.
    subject = (scene_idea or "").strip() or _subject_line(brief)
    style_low = style.lower()
    style_clean = "" if ("stock" in style_low or "ai-generated" in style_low or "ai generated" in style_low) else style
    # Per-RUN variation drives the lighting + overall feel (so the same brand differs each
    # run) — but only with NATURAL lighting options, so the de-RGB guard below still holds.
    lighting_phrase = (variation["lighting"] if variation else "soft, even, natural daylight")
    feel_phrase = (
        f"Overall feel: {variation['mood']}, {variation['energy']}; {variation['composition']}. "
        if variation else ""
    )
    dna_block = _brand_dna_lines(brand_dna)
    if dna_block:
        # DNA-LED: render in the brand's OWN learned visual language (reverse-engineered
        # from its real creatives). This DELIBERATELY overrides the generic natural-light /
        # no-gel defaults — for a brand whose real ads ARE composite + colored light (e.g.
        # WE's purple), matching them is correct, not a 'gamer RGB' bug. Facts stay grounded;
        # subject + region + no-text guards remain.
        lead = (
            f"A single premium advertising visual for the brand '{brief.business_name}', "
            f"designed in the BRAND'S OWN visual language below. Show {subject}. "
            + (f"{region}. " if region else "")
            + f"Lead the palette with {palette}.\n\nBRAND DESIGN LANGUAGE (follow it faithfully):\n"
            + dna_block
            + f"\n\nComposition: ONE clear focal subject with a clean eye-path — do NOT crowd the "
            "frame with competing busy elements (multiple UI panels, charts, maps, HUDs). "
            + f"{_calm_zone_instruction(zone)} ABSOLUTELY "
            "NO text, letters, numbers, logos, or signage anywhere in the image. Vertical 4:5 poster."
        )
    else:
        lead = (
            f"A single photorealistic advertising photograph. Show {subject}. "
            + (f"{region}. " if region else "")
            # Brand colors belong in the SCENE (surfaces/props/wardrobe) under NATURAL light —
            # NOT in the lighting. "Dominant colors ... in the lighting" produced a clichéd
            # red-and-blue split-lit 'gamer RGB' scene (MEASURED on ITI: palette red+blue).
            + f"The brand palette ({palette}) appears naturally in the environment, surfaces, "
            f"props and wardrobe — NOT as colored lighting. {lighting_phrase.capitalize()}; "
            "ABSOLUTELY NO colored gel lighting, NO duotone, NO harsh red-and-blue or "
            "warm-vs-cool split lighting. Clean, modern, professional commercial campaign "
            "photography (NOT artsy/bohemian, NOT rustic; NO arches, pottery, sculpture, or "
            "handicraft props unless that is literally the business), shallow "
            f"depth of field, authentic candid moment. {feel_phrase}"
            + (f"Visual style: {style_clean}. " if style_clean else "")
            + "Composition: ONE clear focal subject — do NOT crowd the frame with competing "
            "busy elements. "
            + f"{_calm_zone_instruction(zone)} No text, letters, numbers, logos, "
            "or signage anywhere. Vertical 4:5 advertising poster."
        )
    # CS-02: the ad-safety COMPLIANCE was computed at :788 and injected into the concept LLM's
    # `user` block (:792) — then this function rebuilt `lead` from scratch and dropped it, so the
    # prompt that actually reaches the IMAGE MODEL carried no gate (a clinic could ship before/after
    # imagery). Append it here, AFTER both `lead` branches, so it survives into both returns exactly
    # once. Empty for non-regulated categories (compliance_for -> "") == no change.
    if _compliance:
        lead = f"{lead} {_compliance}"

    # NO free-metaphor secondary cue. The LLM's surreal scene was the main WANDER source
    # — it leaked off-brand objects (a clay sculpture on NTI) and pulled toward a generic
    # "bohemian/editorial" look. The grounded lead (real people + the brand's real modern
    # environment + region + palette) plus the per-run VARIATION (lighting/mood/composition/
    # layout/fonts) gives controlled, ON-BRAND variety instead. A short, on-brand atmosphere
    # hint from the LLM is appended only if it stays a detail (not a separate scene/object).
    creative = prompt.strip().replace("\n", " ")
    bad = ("sculpt", "statue", "pottery", "clay", "arch", "handicraft", "surreal",
           "floating", "void", "staircase", "abstract")
    if creative and not any(b in creative.lower() for b in bad):
        return f"{lead} Subtle on-brand atmospheric detail (keep it minor, do not add a separate object or scene): {creative[:160]}"
    return lead


class _DesignSpecResponse(BaseModel):
    """Structured output: the LLM art-director's COMPOSITION decisions for one poster.

    Pure design — carries no factual claims. (No numeric range constraints here:
    OpenAI strict structured outputs reject min/max; we clamp `scrim_strength` after.)
    """
    layout: Literal[
        "bottom_band", "side_panel_left", "side_panel_right",
        "top_anchor", "center_editorial", "magazine_hero",
    ]
    headline_treatment: Literal["stacked_hero", "block", "highlight"]
    # HOW THE CALL TO ACTION IS DRAWN — and it was the one composition decision the director was
    # never allowed to make.
    #
    # MEASURED 2026-08-06: this field did not exist, so every LLM-designed poster fell through to
    # `PosterDesignSpec.cta_style`'s default — `"pill"`. `poster/template.py` offers four real
    # treatments and its own comment explains why the fourth matters: "A premium poster often HAS
    # no button, and a system that can only draw buttons cannot make one." The system could only
    # draw buttons. The other three were reachable only from the no-LLM fallback, which the full
    # pipeline never takes. (Owner: «الزرار ده مش منطقي ينزل نفس الشكل مرة».)
    #
    # OPTIONAL, deliberately. Making it required means a model that skips the field fails
    # VALIDATION — the whole design call dies and the pipeline drops to its no-LLM fallback,
    # which is a far worse outcome than a missing button style. `None` lets the answer through
    # and the per-run seed decides, which is exactly the degrade this was built for.
    cta_style: Optional[Literal["pill", "block", "outline", "text_arrow"]] = None
    accent_word: Literal["last", "first", "none"]
    text_align: Literal["left", "center", "right"]
    scrim_strength: float
    show: list[str]
    accent_hex: str
    rationale: str
    # The marketing archetype the LLM commits to — a BEHAVIORAL guide that drives the
    # placement below (NOT a rigid CSS grid). Carried onto the spec for the renderer.
    marketing_archetype: Literal[
        "magazine_editorial", "product_hero", "typographic_anchor", "proof_and_trust",
    ]
    # FREE-FORM placement (continuous, 0..1 of the canvas) — the real template-breaker.
    text_box: list[float]    # [x, y, w]: top-left + width of the text cluster
    logo_xy: list[float]     # [x, y]: top-left of the brand mark


def _valid_coords(v: Any, n: int) -> Optional[list]:
    """Accept a list of exactly n floats in [0,1]; else None (renderer uses an archetype)."""
    try:
        vals = [float(x) for x in (v or [])]
    except Exception:
        return None
    if len(vals) != n or not all(0.0 <= x <= 1.0 for x in vals):
        return None
    return vals


def _zone_from_box(box: Optional[list]) -> str:
    """Map a free text_box to the image's calm zone (so Imagen leaves space where text lands)."""
    if not box:
        return "bottom"
    x, y, w = box
    cx, cy = x + w / 2.0, y + 0.12
    if cy < 0.40:
        return "top"
    if cy > 0.62:
        return "bottom"
    if cx < 0.40:
        return "left"
    if cx > 0.60:
        return "right"
    return "center"


def build_design_spec(
    brief: PosterBrief,
    caller: Optional[Any] = None,
    profile: dict[str, Any] | None = None,
    variation: dict | None = None,
    brand_dna: Any = None,
) -> PosterDesignSpec:
    """LLM art-director: decide the COMPOSITION for THIS brand from its scraped data.

    Returns a PosterDesignSpec. Falls back to the deterministic per-brand
    `default_design_spec` when no caller is supplied or the call fails. The spec is
    pure DESIGN (no factual claims); the renderer injects the evidence-grounded text
    and the real logo, so creativity is unbounded while facts stay grounded.

    GROUNDED COLOR: `accent_hex` is accepted ONLY if it exactly matches a scraped
    brand color, so even the lead color traces to evidence.
    """
    # Imported here (not at module top) to avoid any import-order coupling; template
    # imports only schemas, so there is no cycle.
    from poster.template import (
        default_design_spec, _is_rtl, _CTA_STYLES, _LOGO_CORNER_BY_LAYOUT, _ZONE_BY_LAYOUT,
    )
    from poster.variation import design_variation_cue, variation_seed_int

    if caller is None:
        # No LLM: vary the deterministic fallback per run via the variation seed.
        return default_design_spec(
            brief, density="full", variation_seed=variation_seed_int(variation)
        )

    persona = _persona_lines(profile)
    offerings = "; ".join([o for o in (brief.offerings or [])[:5] if o]) or (brief.category or "")
    palette = ", ".join(brief.palette_hex[:5]) or "(none)"
    is_rtl = _is_rtl(brief.headline) or _is_rtl(brief.business_name)

    system = (
        "You are an award-winning poster ART DIRECTOR. Decide the COMPOSITION for ONE "
        "premium vertical ad poster for THIS specific brand. You choose DESIGN ONLY "
        "(layout, emphasis, color role) — you never invent facts.\n"
        "Pick values that fit THIS brand's character so different brands look different:\n"
        "- layout: bottom_band | side_panel_left | side_panel_right | top_anchor | "
        "center_editorial | magazine_hero\n"
        "- headline_treatment: stacked_hero (a short punchy headline as a bold word-stack), "
        "block (a clean single line), or highlight (one key word sits in a brand-colored "
        "block — punchy/editorial)\n"
        "- cta_style: HOW the call to action is drawn — pick from the brand's REGISTER, not by "
        "habit. pill (a solid rounded button — loud, retail, direct-response); block (a "
        "sharp-cornered slab — editorial, modern, reads as designed rather than clicked); "
        "outline (the brand colour as a hairline border on transparent — quiet, premium); "
        "text_arrow (NO button at all: the words with a rule under them and an arrow — what a "
        "luxury or institutional poster actually does). A government institute, a heritage house "
        "or a clinic rarely wears a loud button; a retail offer usually does. Do not default to "
        "pill.\n"
        "- accent_word: which headline word takes the brand color (last/first/none)\n"
        "- text_align: left/center/right (Arabic/RTL reads right)\n"
        "- scrim_strength: 0..1 darkness behind the text for legibility (lower when the "
        "headline sits over a calm image area)\n"
        "- show: a CLEAN subset of [logo,kicker,headline,sub,offerings,cta,contact,social] "
        "— great posters show FEW elements; never list contact/social unless they truly help\n"
        "- accent_hex: the ONE brand color to lead, copied EXACTLY from the brand palette "
        "below (or empty string if unsure)\n"
        "- marketing_archetype: FIRST pick the ONE archetype that fits THIS brand, then let it "
        "DRIVE your text_box / logo_xy / headline_treatment / text_align below. These are "
        "creative BEHAVIORAL guides (you still output continuous coordinates), NOT rigid grids:\n"
        "   * magazine_editorial (premium/lifestyle): full-bleed atmospheric image; a MASSIVE "
        "bold typographic lockup elegantly on the LEFT or RIGHT third, interacting with the "
        "image's negative space as the visual anchor. (text_box = a tall side third; stacked_hero.)\n"
        "   * product_hero (e-commerce): the product/scene dominates center/top; the text block "
        "ANCHORS at the BOTTOM, appearing to EMERGE from the base of the product — clean, minimal, "
        "direct. (text_box low + wide; block or stacked_hero.)\n"
        "   * typographic_anchor (promo/flash sale): TEXT is the hero — the headline occupies "
        "~40-50% of the canvas (e.g. the top half); the image sits low / faded behind a strong "
        "scrim. (text_box large + high; stacked_hero; higher scrim_strength.)\n"
        "   * proof_and_trust (B2B/lead-gen): clean, structured, solid; a well-defined text block "
        "(centered or bottom-right) with a strong headline + the 2-3 evidence offerings as bullets. "
        "(block; include 'offerings' in show.)\n"
        "   Fit it to the brand: lifestyle->magazine_editorial, store/product->product_hero, "
        "sale/announcement->typographic_anchor, B2B/services->proof_and_trust.\n"
        "- text_box: [x, y, w] as FRACTIONS of the canvas (0..1) — the TOP-LEFT (x,y) and "
        "WIDTH of the text cluster. This is the real composition control: place it where the "
        "image is calmest and VARY it (corner, side, lower band, off-center) — do NOT default "
        "to the same spot. Keep margins (x>=0.04; x+w<=0.95; y<=0.74; 0.40<=w<=0.9).\n"
        "- logo_xy: [x, y] (0..1) top-left of the logo — a clean, uncluttered spot AWAY from "
        "the text_box.\n"
        "- rationale: one short line on why this composition fits the brand."
    )
    # When we know the brand's REAL design language, steer the composition to echo it.
    dna_lines = _brand_dna_lines(brand_dna)
    user = (
        f"Brand: {brief.business_name}\n"
        f"Category: {brief.category}\n"
        f"Headline (verbatim — do not change the words): {brief.headline}\n"
        f"Offerings: {offerings}\n"
        f"Brand palette (choose accent_hex from these EXACTLY): {palette}\n"
        f"Has a real logo image: {bool(brief.logo_url)}\n"
        f"RTL / Arabic copy: {is_rtl}\n"
        + (f"\nBrand persona (verbatim from the real site):\n{persona}\n" if persona else "")
        + (f"\nThe brand's REAL design language (echo where THEY place logo/headline, their "
           f"composition habits):\n{dna_lines}\n" if dna_lines else "")
        + (f"\n{design_variation_cue(variation)}\n" if variation else "")
    )

    try:
        resp, _usage = caller(system, user, _DesignSpecResponse, group_name="poster_design")
    except Exception:
        return default_design_spec(
            brief, density="full", variation_seed=variation_seed_int(variation)
        )

    layout = resp.layout
    show = [s for s in (resp.show or []) if s in _SHOW_ALLOWED]
    if "headline" not in show:
        show.insert(0, "headline")
    if brief.logo_url and "logo" not in show:
        show.insert(0, "logo")
    if not show:
        show = ["logo", "headline", "cta"]

    # GROUND the lead color: accept accent_hex only if it's a real scraped brand color.
    palette_set = {
        str(h).lower()
        for h in (list(brief.palette_hex or []) + ([brief.primary_color] if brief.primary_color else []))
        if h
    }
    accent = (resp.accent_hex or "").strip()
    accent = accent if (accent.startswith("#") and accent.lower() in palette_set) else None

    # FREE-FORM placement: use the LLM's continuous coords when valid (unbounded layouts);
    # else fall back to the archetype. The image's calm zone follows where the text lands.
    text_box = _valid_coords(getattr(resp, "text_box", None), 3)
    logo_xy = _valid_coords(getattr(resp, "logo_xy", None), 2)
    zone = _zone_from_box(text_box) if text_box else _ZONE_BY_LAYOUT.get(layout, "bottom")

    # The director's own choice, with the per-run seed as the fallback so an older model that
    # omits the field still VARIES instead of collapsing back to a pill on every poster.
    _cta = str(getattr(resp, "cta_style", "") or "").strip()
    if _cta not in _CTA_STYLES:
        _seeded = default_design_spec(brief, density="full",
                                      variation_seed=variation_seed_int(variation))
        _cta = _seeded.cta_style
    return PosterDesignSpec(
        cta_style=_cta,
        layout=layout,
        logo_corner=_LOGO_CORNER_BY_LAYOUT.get(layout, "top_left"),
        headline_treatment=resp.headline_treatment,
        accent_word=resp.accent_word,
        text_align=resp.text_align,
        scrim_strength=min(1.0, max(0.0, float(resp.scrim_strength or 0.82))),
        show=show,
        negative_space_zone=zone,
        accent_hex=accent,
        rationale=(resp.rationale or "")[:300],
        variation_seed=variation_seed_int(variation),
        text_box=text_box,
        logo_xy=logo_xy,
        marketing_archetype=getattr(resp, "marketing_archetype", None),
    )