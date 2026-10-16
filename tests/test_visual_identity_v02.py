from __future__ import annotations

import io

from PIL import Image

from business_profile.quality import compute_quality
from business_profile.rules.from_visual import extract_visual
from business_profile.schemas import (
    BusinessProfile,
    AudienceType,
    Confidence,
    ContactChannels,
    EvidencedField,
    ExtractionMeta,
    PricingPosture,
    ProfileQuality,
    SourceType,
    ToneOfVoice,
    VisualIdentitySummary,
)
from scraper.extractors.visual import build_visual_identity


def _png(color: str = "#f4ead8") -> bytes:
    img = Image.new("RGB", (300, 220), color)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _computed(candidates=None, colors=None, site_name="El Kbabgi Restaurant"):
    return {
        "body_bg": "rgb(244, 234, 216)",
        "header_bg": "rgb(14, 14, 14)",
        "button_bg": "rgb(190, 145, 49)",
        "body_font": "Inter",
        "heading_font": "Lora",
        "button_font": "Inter",
        "site_name": site_name,
        "logo_candidates": candidates or [],
        "color_signals": colors or [
            {"color": "rgb(244, 234, 216)", "role": "body_bg", "weight": 1},
            {"color": "rgb(14, 14, 14)", "role": "header", "weight": 6},
            {"color": "rgb(190, 145, 49)", "role": "button", "weight": 8},
        ],
    }


def _candidate(src, *, alt="Logo", source_type="img", in_header=True, near_nav=True,
               links_to_home=True, width=180, height=70, context="brand logo",
               is_social=False, is_hero_gallery=False, in_footer=False, href=None):
    return {
        "src": src,
        "alt": alt,
        "source_type": source_type,
        "context_text": context,
        "class_id": context,
        "in_header": in_header,
        "near_nav": near_nav,
        "in_footer": in_footer,
        "links_to_home": links_to_home,
        "width": width,
        "height": height,
        "is_social": is_social,
        "is_hero_gallery": is_hero_gallery,
        "href": href,
    }


def test_nti_like_svg_lazy_or_header_logo_is_preserved_as_candidate_and_primary():
    candidates = [
        _candidate(
            "https://nti.example/assets/nti-logo.svg",
            alt="NTI logo",
            source_type="svg_file",
            context="header brand logo nti",
        ),
        _candidate(
            "https://nti.example/assets/lazy-logo.png",
            alt="",
            source_type="lazy_img",
            context="header logo data-src nti",
        ),
    ]
    v = build_visual_identity(_png(), _computed(candidates, site_name="NTI"), "https://nti.example/")

    assert len(v.logo_candidates) == 2
    assert v.primary_logo is not None
    assert v.primary_logo.src.endswith("nti-logo.svg")
    assert v.logo is not None  # backward-compatible legacy field
    assert "no_confident_primary_logo" not in v.visual_warnings


def test_digilians_like_multiple_authority_partner_logos_sets_cobranding_warning():
    candidates = [
        _candidate(
            "https://digilians.example/digilians-logo.png",
            alt="Digilians logo",
            context="header digilians brand logo",
        ),
        _candidate(
            "https://digilians.example/ministry-logo.png",
            alt="Ministry of Communications logo",
            context="header ministry government authority logo",
        ),
        _candidate(
            "https://digilians.example/partner-logo.png",
            alt="Partner logo",
            context="header partner sponsor logo",
        ),
    ]
    v = build_visual_identity(_png(), _computed(candidates, site_name="Digilians"), "https://digilians.example/")

    assert v.primary_logo is not None
    assert "digilians-logo" in v.primary_logo.src
    assert len(v.authority_logos) == 1
    assert len(v.partner_logos) == 1
    assert v.co_branding_detected is True


def test_spa_named_logo_is_chosen_when_header_heuristic_misses_it():
    # ITI (Angular SPA): the real logo is <img src=ColoredLogo.svg class="header__image"> but the
    # custom <app-header> isn't recognized, so it gets NO in_header/near_nav/home signal and lands
    # as a sub-threshold unknown_candidate (score ~42). Its FILENAME ('...Logo.svg') is the rescue
    # signal. Prefer the coloured mark over the white/footer inverse.
    candidates = [
        _candidate("https://iti.example/assets/images/ColoredLogo.svg", alt="", source_type="svg_file",
                   context="", in_header=False, near_nav=False, links_to_home=False),
        _candidate("https://iti.example/assets/images/WhiteLogo.svg", alt="", source_type="svg_file",
                   context="", in_header=False, near_nav=False, links_to_home=False, in_footer=True),
        _candidate("https://iti.example/assets/parteners/Vodafone.png", alt="", source_type="img",
                   context="partner sponsor", in_header=False, near_nav=False, links_to_home=False),
    ]
    v = build_visual_identity(_png(), _computed(candidates, site_name="Information Technology Institute"),
                              "https://iti.example/")
    assert v.primary_logo is not None
    assert v.primary_logo.src.endswith("ColoredLogo.svg")   # the coloured mark, not the white footer one


def test_external_svg_logo_colors_drive_the_brand_palette(monkeypatch):
    # ITI: the logo is an EXTERNAL .svg whose real brand colour is RED, while the page background is
    # navy. _logo_svg_signals must fetch + parse the svg and surface the SATURATED red (not the grey
    # wordmark text or the white plate), so the brand's TRUE colour wins the palette.
    import scraper.extractors.visual as vis
    from scraper.extractors.visual import _hex_to_rgb, _is_low_saturation_gray, _is_near_white
    svg = ('<svg><path fill="#9a3333"/><path fill="#9d3433"/><path fill="#903332"/>'
           '<path fill="#424143"/><path fill="#ffffff"/><stop stop-color="#b03633"/></svg>')

    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self, *a):
            return svg.encode("utf-8")

    monkeypatch.setattr("scraper.url_utils.is_safe_public_url", lambda u: True)
    monkeypatch.setattr("scraper.url_utils.open_public_url", lambda *a, **k: _Resp())

    sig = vis._logo_svg_signals("https://x.example/ColoredLogo.svg", "https://x.example/")
    assert sig, "expected logo svg colour signals"
    top = sig[0]["color"]
    assert not _is_low_saturation_gray(top) and not _is_near_white(top)   # not the grey text / white
    r, g, b = _hex_to_rgb(top)
    assert r > g and r > b                                                # a RED is dominant
    # a non-svg / non-http logo yields nothing (no spurious fetch)
    assert vis._logo_svg_signals("inline-svg:0", "https://x/") == []
    assert vis._logo_svg_signals("https://x/logo.png", "https://x/") == []


def test_named_logo_rescue_excludes_partner_and_authority_svgs():
    # Even an SVG with a pure 'logo' filename is REFUSED when it is classified as a partner or an
    # authority mark — the rescue only promotes the brand's OWN mark, never a co-brand.
    from scraper.extractors.visual import _choose_primary_logo
    from scraper.schemas import LogoCandidate
    cands = [
        LogoCandidate(src="https://x.example/partners/logo.svg", page_url="https://x.example/",
                      source_type="svg_file", classification="partner_logo", score=42,
                      reasons=["logo_keyword", "svg_file"]),
        LogoCandidate(src="https://x.example/authority/logo.svg", page_url="https://x.example/",
                      source_type="svg_file", classification="government_logo", score=42,
                      reasons=["logo_keyword", "svg_file"]),
    ]
    assert _choose_primary_logo(cands, ["xbrand"]) is None


def test_social_icons_and_hero_banners_are_not_selected_as_primary_logo():
    candidates = [
        _candidate(
            "https://x.example/instagram.svg",
            alt="Instagram",
            source_type="svg_file",
            context="header social instagram icon",
            is_social=True,
            width=24,
            height=24,
        ),
        _candidate(
            "https://x.example/hero-banner.jpg",
            alt="Hero banner",
            context="hero banner slider gallery",
            is_hero_gallery=True,
            width=1200,
            height=600,
        ),
    ]
    v = build_visual_identity(_png(), _computed(candidates, site_name="Unknown"), "https://x.example/")

    assert v.primary_logo is None
    assert v.logo is None
    assert v.visual_extraction_note == "no_confident_primary_logo"
    assert "no_confident_primary_logo" in v.visual_warnings


def test_contact_link_icon_does_not_outrank_the_real_logo():
    # MEASURED on Assih (Squarespace): a header EMAIL icon (a `mailto:` link) scored 0.86
    # as primary_brand_logo and OUTRANKED the real raster logo (0.78), because the
    # is_social regex matches only social-network names, not mailto:/tel:/WhatsApp. The
    # contact-link penalty must demote it below the real logo. Neutral context here, so
    # the demotion comes ONLY from the href (not a social keyword).
    candidates = [
        _candidate("https://x.example/logo_brand.png", alt="Brand",
                   source_type="img", context="brand logo", href="/"),
        _candidate("inline-svg:9", alt="Email", source_type="inline_svg",
                   context="header icon", links_to_home=False, width=24, height=24,
                   href="mailto:info@x.example"),
    ]
    v = build_visual_identity(_png(), _computed(candidates, site_name="Brand"), "https://x.example/")
    assert v.primary_logo is not None
    assert v.primary_logo.src == "https://x.example/logo_brand.png"   # real logo, not the email icon


def test_background_dominant_palette_does_not_override_black_gold_restaurant_brand():
    v = build_visual_identity(
        _png("#f4ead8"),
        _computed(site_name="El Kbabgi Restaurant"),
        "https://elkbabgi.example/",
    )

    assert v.raw_palette
    assert v.brand_palette
    assert v.primary_brand_color in {"#0e0e0e", "#be9131"}
    assert "#be9131" in [c.hex for c in v.brand_palette]
    assert "palette_dominated_by_background" in v.visual_warnings


def test_low_confidence_candidates_keep_candidates_and_note_no_confident_primary():
    candidates = [
        _candidate(
            "https://example.com/favicon.ico",
            alt="icon",
            source_type="favicon",
            in_header=False,
            near_nav=False,
            links_to_home=False,
            width=16,
            height=16,
            context="favicon",
        )
    ]
    v = build_visual_identity(_png(), _computed(candidates, site_name="Example"), "https://example.com/")

    assert v.primary_logo is None
    assert len(v.logo_candidates) == 1
    assert v.logo_candidates[0].classification == "favicon"
    assert v.visual_extraction_note == "no_confident_primary_logo"


def test_business_profile_visual_projection_keeps_old_and_new_fields():
    from scraper.schemas import ReadinessReport, RobotsRecord, ScrapeManifest, ScrapeMeta
    from scraper.time_utils import utc_now

    candidates = [_candidate("https://brand.example/logo.svg", alt="Brand logo", context="brand logo")]
    visual = build_visual_identity(_png(), _computed(candidates, site_name="Brand"), "https://brand.example/")
    manifest = ScrapeManifest(
        scrape_meta=ScrapeMeta(input_url="https://brand.example", normalized_url="https://brand.example", scraped_at=utc_now()),
        robots=RobotsRecord(checked=False, allowed=True),
        readiness=ReadinessReport(
            has_homepage=True,
            has_internal_pages=False,
            has_contact_signals=False,
            has_visual_signals=True,
            has_cta_candidates=False,
            has_metadata=True,
            ready_for_extraction=True,
        ),
        visual=visual,
    )

    summary = extract_visual(manifest)

    assert summary.logo_url == summary.primary_logo.src
    assert summary.palette_hex == summary.brand_palette
    assert summary.primary_color == summary.primary_brand_color
    assert summary.logo_candidates
    assert summary.raw_palette


def _field(value, source=SourceType.EXTRACTED):
    return EvidencedField(value=value, source_type=source, confidence=Confidence.HIGH, evidence=[])


def _quality_placeholder():
    return ProfileQuality(
        has_name=False,
        has_category=False,
        has_offerings=False,
        has_audience=False,
        has_value_propositions=False,
        has_tone=False,
        has_contact=False,
        has_visual=False,
        fields_extracted=0,
        fields_inferred=0,
        fields_missing=20,
        major_missing=[],
        ready_for_strategy=False,
    )


def test_old_visual_fields_still_work_for_backward_compatibility():
    profile = BusinessProfile(
        source_url="https://legacy.example",
        final_url="https://legacy.example",
        name=_field("Legacy Brand"),
        tagline=_field("A useful tagline"),
        description=_field("A useful brand description."),
        category=_field("restaurant"),
        offerings=[],
        pricing_visible=_field(False),
        pricing_posture=_field(PricingPosture.UNKNOWN),
        audience_type=_field(AudienceType.B2C),
        audience_signals=[],
        value_propositions=[_field("Warm family dining")],
        tone_of_voice=_field(ToneOfVoice.FRIENDLY),
        locations=[],
        service_areas=[],
        languages=[],
        hours=None,
        contact_channels=ContactChannels(phones_e164=["+201000000000"]),
        existing_ctas=[],
        social_presence=[],
        trust_signals=[],
        visual=VisualIdentitySummary(palette_hex=["#111111"], logo_url=None),
        extraction_meta=ExtractionMeta(manifest_path="m.json", extracted_at="2026-05-17T00:00:00Z"),
        quality=_quality_placeholder(),
    )

    q = compute_quality(profile)

    assert q.has_visual is True
    assert q.ready_for_poster is True
    assert "no_visual_signals" not in q.poster_blockers

def test_elkbabgi_real_palette_does_not_choose_beige_background_as_primary():
    from scraper.extractors.visual import _build_brand_palette
    from scraper.schemas import ColorEntry

    raw_palette = [
        ColorEntry(hex="#d0cfcc", dominance=0.5116),
        ColorEntry(hex="#aca297", dominance=0.3402),
        ColorEntry(hex="#171818", dominance=0.1115),
        ColorEntry(hex="#cba656", dominance=0.0271),
        ColorEntry(hex="#7e4d34", dominance=0.0097),
    ]

    brand_palette, info = _build_brand_palette(
        raw_palette,
        {
            "color_signals": [
                {"color": "#f0f0f0", "role": "button", "weight": 8}
            ]
        },
    )

    brand_hex = [c.hex for c in brand_palette]

    assert info["primary_brand_color"] != "#aca297"
    assert "#aca297" in info["background_colors"]
    assert "#cba656" in brand_hex
    assert "#7e4d34" in brand_hex
    assert "#171818" in brand_hex
    assert info["background_dominance"] >= 0.65


def test_saturated_brand_color_beats_a_pale_high_dominance_button():
    # Pins the behaviour verified by a live re-scrape (te.eg / iti.gov.eg homepages): a
    # WASHED light color (a pale button/background tint) with high pixel dominance must NOT
    # win primary over a genuinely saturated brand color present in strong roles (header/nav).
    # An OLD-code snapshot mislabeled te's purple #54249c and iti's maroon as pale blue — that
    # was stale-manifest artifact; current code prefers the saturated mark, and this guards it.
    from scraper.extractors.visual import _build_brand_palette
    from scraper.schemas import ColorEntry

    raw_palette = [
        ColorEntry(hex="#c5d6e9", dominance=0.61),   # pale blue-gray, dominates the screenshot
        ColorEntry(hex="#ffffff", dominance=0.24),
        ColorEntry(hex="#54249c", dominance=0.06),   # the real (saturated purple) brand mark
    ]
    brand_palette, info = _build_brand_palette(
        raw_palette,
        {
            "color_signals": [
                {"color": "#54249c", "role": "header", "weight": 6},
                {"color": "#54249c", "role": "nav", "weight": 6},
                {"color": "#c5d6e9", "role": "button", "weight": 8},   # pale, high weight
                {"color": "#c5d6e9", "role": "body_bg", "weight": 1},
            ]
        },
    )
    prim = info["primary_brand_color"]
    assert prim is not None
    # The saturated purple wins; the pale near-background blue is not the primary.
    assert prim.lower() != "#c5d6e9"
    from scraper.extractors.visual import _saturation
    assert _saturation(prim) >= 0.30


# ---------------------------------------------------------------------
# Structural-independent logo floor (opaque-DOM SaaS-builder false-negative)
# ---------------------------------------------------------------------
# A SaaS site-builder (Strikingly/Wix) can render the header in a DOM the extractor doesn't
# recognize, denying every structural signal — so the REAL raster logo scores only 54
# (logo_keyword + raster + suitable_shape + repeated) and misses the 55 gate by one point.
# The floor recovers exactly that UNIQUE, site-wide-repeated content-only mark, while refusing
# its twin (a third-party press/client/payment logo-WALL) via a uniqueness guard, and running
# AFTER the footer-rescue so a real footer logo is never suppressed.

def _floor_logo(src, *, context="brand logo", alt="logo", source_type="img",
                width=50, height=60, in_footer=False):
    """An opaque-DOM logo candidate: logo-named/shaped raster with NO structural signals."""
    return _candidate(src, alt=alt, source_type=source_type, in_header=False, near_nav=False,
                      links_to_home=False, width=width, height=height, context=context,
                      in_footer=in_footer)


def test_opaque_dom_unique_repeated_logo_is_selected_via_floor():
    # elkbabgi profile: one logo, no structure, repeated x4 -> 54 -> now selected by the floor.
    candidates = [_floor_logo("https://cdn.example.com/13987186/961198_854390.png")
                  for _ in range(4)]
    v = build_visual_identity(_png(), _computed(candidates, site_name="Kbabgi"),
                              "https://kbabgi.example/")
    assert v.primary_logo is not None
    assert v.primary_logo.src.endswith("961198_854390.png")
    assert "no_confident_primary_logo" not in v.visual_warnings


def test_floor_refuses_third_party_logo_wall():
    # A press/client/payment WALL: 3 DISTINCT hosted *-logo tiles, each repeated x4. All score
    # 54 and are floor-eligible, but there is no way to tell the brand's own from a third
    # party's without structure -> the uniqueness guard returns UNKNOWN honestly (no FP).
    wall = []
    for name in ("press/techcrunch-logo.png", "press/forbes-logo.png", "pay/visa-logo.png"):
        wall += [_floor_logo(f"https://cdn.example.com/{name}", context="", alt="")
                 for _ in range(4)]
    v = build_visual_identity(_png(), _computed(wall, site_name="Kbabgi"),
                              "https://kbabgi.example/")
    assert v.primary_logo is None
    assert "no_confident_primary_logo" in v.visual_warnings


def test_floor_does_not_suppress_a_real_footer_logo():
    # A genuine footer logo (rescue-eligible) coexisting with a floor-eligible tile: the
    # footer-rescue runs FIRST, so the real footer mark wins — the floor never steals it.
    footer = [_candidate("https://acme.example/acme-logo.png", alt="Acme logo",
                         context="acme brand logo footer", in_header=False, near_nav=False,
                         links_to_home=False, in_footer=True, width=160, height=60)]
    tile = [_floor_logo("https://cdn.example.com/feature-badge.png") for _ in range(4)]
    v = build_visual_identity(_png(), _computed(footer + tile, site_name="Acme"),
                              "https://acme.example/")
    assert v.primary_logo is not None
    assert v.primary_logo.src.endswith("acme-logo.png")


def test_floor_refuses_data_uri_placeholder_twin():
    # marasim's "Client Five logo" case: a data: placeholder with elkbabgi's EXACT reason set.
    # The hosted-asset guard refuses it (a brand logo is a real file, not an inline blob).
    twin = [_floor_logo("data:image/png;base64,AAAA", context="brand logo", alt="Client logo")
            for _ in range(4)]
    v = build_visual_identity(_png(), _computed(twin, site_name="Kbabgi"),
                              "https://kbabgi.example/")
    assert v.primary_logo is None


def test_floor_requires_site_wide_repetition():
    # A single-emit logo (repeated x2 < 4) is not confidently site chrome -> floor declines.
    candidates = [_floor_logo("https://cdn.example.com/logo.png") for _ in range(2)]
    v = build_visual_identity(_png(), _computed(candidates, site_name="Kbabgi"),
                              "https://kbabgi.example/")
    assert v.primary_logo is None


def test_floor_is_voided_by_a_penalty_signal():
    # A hero/gallery-penalized image with an otherwise-matching signature is refused.
    candidates = [_floor_logo("https://cdn.example.com/logo.png") for _ in range(4)]
    for c in candidates:
        c["is_hero_gallery"] = True
    v = build_visual_identity(_png(), _computed(candidates, site_name="Kbabgi"),
                              "https://kbabgi.example/")
    assert v.primary_logo is None


def test_floor_does_not_alter_a_structurally_placed_passer():
    # No regression: a fully-structural logo still wins normally (the floor path is untouched).
    candidates = [_candidate("https://brand.example/brand-logo.png", alt="Brand logo",
                             context="header brand logo", in_header=True, near_nav=True,
                             links_to_home=True)]
    v = build_visual_identity(_png(), _computed(candidates, site_name="Brand"),
                              "https://brand.example/")
    assert v.primary_logo is not None
    assert v.primary_logo.src.endswith("brand-logo.png")
