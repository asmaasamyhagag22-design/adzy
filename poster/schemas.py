from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, Field


class PosterSocial(BaseModel):
    platform: str = Field(max_length=40)
    url: str = Field(max_length=500)


class PosterBrief(BaseModel):
    business_name: str = Field(min_length=2, max_length=120)
    category: Optional[str] = None

    headline: str = Field(min_length=2, max_length=160)
    subheadline: Optional[str] = Field(default=None, max_length=260)

    offerings: list[str] = Field(default_factory=list, max_length=3)

    cta_text: str = Field(default="Learn more", max_length=80)
    cta_url: Optional[str] = Field(default=None, max_length=500)
    contact_line: Optional[str] = Field(default=None, max_length=160)

    palette_hex: list[str] = Field(default_factory=list, max_length=5)
    primary_color: Optional[str] = None
    tone: Optional[str] = None

    # Generous cap: a logo may be an inlined SVG `data:` URI (resolved from an inline
    # <svg>/<use> sprite), which is far larger than an http(s) URL.
    logo_url: Optional[str] = Field(default=None, max_length=300_000)
    logo_text: Optional[str] = Field(default=None, max_length=120)
    logo_source_type: Optional[str] = Field(default=None, max_length=60)
    logo_confidence: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    # "light" (default white plate) or "dark" — a light/white logo needs a dark plate.
    logo_chip: Optional[str] = Field(default="light", max_length=8)

    pricing_note: Optional[str] = None
    warnings: list[str] = Field(default_factory=list)

    # Evidence-backed social profiles (platform + url) from profile.social_presence.
    social: list[PosterSocial] = Field(default_factory=list, max_length=6)

    # Brand typography (the site's own fonts, from manifest.visual.fonts). Used by
    # the poster when the named family is loadable (Google Fonts); else the poster
    # falls back to a curated modern default. May be a private/generic name.
    heading_font: Optional[str] = Field(default=None, max_length=80)
    body_font: Optional[str] = Field(default=None, max_length=80)


class PosterDesignSpec(BaseModel):
    """The DESIGN decisions for one poster — a structured layout/composition spec.

    This is the contract between the art director (the LLM, which fills it per brand
    from the scraped data) and the renderer (which turns it into HTML/CSS safely).
    It carries ZERO factual claims: it is pure design (where things sit, alignment,
    emphasis, color roles). The factual text + the real logo are injected by the
    renderer from the evidence-grounded PosterBrief — so creativity is unbounded
    while the facts stay grounded.

    `negative_space_zone` is shared with the background prompt so the image leaves
    its calm area exactly where the text block lands (the "big-brand" look).
    """
    layout: Literal[
        "bottom_band",        # text band across the bottom (logo opposite, top)
        "side_panel_left",    # text in a left vertical panel; image breathes right
        "side_panel_right",   # text in a right vertical panel; image breathes left
        "top_anchor",         # logo + text anchored top; image breathes below
        "center_editorial",   # centered headline mid-frame, radial scrim
        "magazine_hero",      # big stacked-hero headline, editorial kicker
    ] = "bottom_band"
    logo_corner: Literal["top_left", "top_right", "bottom_left", "bottom_right"] = "top_left"
    headline_treatment: Literal["stacked_hero", "block", "highlight"] = "stacked_hero"
    accent_word: Literal["last", "first", "none"] = "last"
    # HOW THE CALL TO ACTION IS DRAWN — and it is a design decision, not a constant.
    #
    # OWNER (2026-08-02), after four posters side by side: «نفس طريقه كتابه البوستر ثانيه في كل
    # البوسترز؟ زرار وكلام بيحاول يتحط كويس». She was right and the CSS proved it: the CTA was
    # hardcoded to `border-radius:999px` with a solid fill, one padding and one font size, on
    # every poster of every brand. The layout archetype varied, the headline treatment varied,
    # the CTA never did — an art-critic note had made it LOUDER ("the pill was a tiny footnote")
    # and nobody made it DIFFERENT.
    #
    # `text_arrow` is the one that matters most: a premium poster often has no button at all.
    cta_style: Literal["pill", "block", "outline", "text_arrow"] = "pill"
    text_align: Literal["left", "center", "right"] = "left"
    scrim_strength: float = Field(default=0.82, ge=0.0, le=1.0)
    show: list[str] = Field(default_factory=lambda: ["logo", "headline", "cta"])
    negative_space_zone: Literal["bottom", "top", "left", "right", "center"] = "bottom"
    accent_hex: Optional[str] = None       # LLM may pick which brand color leads
    rationale: Optional[str] = None        # why this design (transparency/debug)
    # Per-RUN variation seed. The renderer mixes it into the font-pairing choice so the
    # TYPOGRAPHY (not just the layout) differs between runs of the same brand. None ->
    # the stable per-brand pairing (backward-compatible).
    variation_seed: Optional[int] = None

    # FREE-FORM layout (Phase 2): the LLM places the text cluster + logo at CONTINUOUS
    # normalized coordinates instead of one of the 6 fixed `layout` archetypes — so the
    # composition is unbounded (kills the "same template" feel), driven by the brand's
    # learned design language. When None, the `layout` archetype path is used (backward
    # compatible). All values are 0..1 fractions of the 1080x1350 canvas; the renderer
    # CLAMPS them to safe margins.
    text_box: Optional[list[float]] = None    # [x, y, w] — top-left + width of the text cluster
    logo_xy: Optional[list[float]] = None     # [x, y] — top-left anchor of the brand mark

    # The marketing archetype that shaped this composition (a behavioral guide from the LLM
    # art-director). Drives the renderer's treatment (emerging scrim / lockup) and the Imagen
    # calm-zone instruction. Optional/None keeps old specs valid (backward-compatible).
    marketing_archetype: Optional[Literal[
        "magazine_editorial", "product_hero", "typographic_anchor", "proof_and_trust",
    ]] = None


class PosterArtDirection(BaseModel):
    provider_prompt: str
    negative_prompt: str
    concept: str
    category: str
    mood: str
    layout: Literal[
        "magazine_cover",
        "split_editorial",
        "bottom_band",
        "clean_institutional",
        "hero_overlay",
    ]
    background_style: str
    safe_overlay_copy: str
    color_notes: list[str] = Field(default_factory=list)
    source_fields_used: list[str] = Field(default_factory=list)


class BackgroundImageResult(BaseModel):
    provider: str
    model: Optional[str] = None
    prompt: str
    background_path: str
    filename: str
    width: int
    height: int
    fallback_used: bool = False
    fallback_reason: Optional[str] = None


class PosterRenderResult(BaseModel):
    poster_path: str
    filename: str
    width: int
    height: int
    mime_type: Literal["image/png"] = "image/png"


class PosterFromProfileRequest(BaseModel):
    profile: dict[str, Any]
    output_format: Literal["poster_1080x1350", "a4_print"] = "poster_1080x1350"
    # "oneshot": the Gemini image model designs the WHOLE creative (per-run layout
    # diversity, designed typography) behind a blocking verbatim-text gate, falling
    # back to "classic" (bg + HTML overlay) on any gate failure.
    engine: Literal["oneshot", "classic"] = "oneshot"
    # Output copy language: "ar"/"en" force it (owner's choice); "auto" infers from
    # the brand (the old behavior).
    language: Literal["auto", "ar", "en"] = "auto"


class PosterFromProfileResponse(BaseModel):
    brief: PosterBrief
    art_direction: PosterArtDirection
    background: BackgroundImageResult
    render: PosterRenderResult
    image_base64: str
    # Brand-safety proof (Evidence Ledger): the raw per-asset audit trail + the
    # client-facing Ad Compliance Sheet derived from it. None when the trail could
    # not be built — never fabricated.
    audit: Optional[dict] = None
    compliance_sheet: Optional[dict] = None
    # Which engine actually shipped this poster ("oneshot" | "classic").
    engine_used: str = "classic"