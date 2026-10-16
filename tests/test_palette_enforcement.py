# -*- coding: utf-8 -*-
"""Deterministic palette enforcement — the measurement outranks the model, per brand.

THE INCIDENT (verification render `poster_e3a5e608`, marasimltd.com, 2026-08-31). The resolved
authority was correct and reached the provider request in full, the model rendered a pale
mint/teal frame anyway, and model-based Vision QA returned `on_brand_color=true` over a frame
that was 76.5% the hues this brand's `dont_list` forbids, with a background 3.9% dark against an
explicit "deep black field".

These lock the enforcement AND its restraint: incidental prohibited colour must not fail a
poster, and a brand that legitimately IS blue must not be forced warm.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from brand.visual_direction import resolve_visual_direction
from poster.palette_compliance import (BACKGROUND_PREFERENCE_VIOLATION,
                                       BRAND_PALETTE_PROHIBITION_VIOLATION, FAIL,
                                       GENERATION_REQUEST_CONTRADICTS_VISUAL_AUTHORITY,
                                       MISSING_REQUIRED_ACCENT, NOT_APPLICABLE, PASS, POLICY,
                                       analyze_palette, merge_findings, repair_lane_for,
                                       scan_request_for_contradictions)

REPO = Path(__file__).resolve().parent.parent
DNA_PATH = REPO / "outputs" / "brandbooks" / "marasim_digital_solution_agency_dna.json"
BRAND_PATH = REPO / "outputs" / "webapp" / "brands" / "marasimltd_com.json"
_HAVE = DNA_PATH.is_file() and BRAND_PATH.is_file()

ORANGE = (240, 75, 30)          # #F04B1E
BLACK = (11, 11, 11)
TEAL = (60, 170, 175)
PASTEL = (208, 226, 226)
WHITE = (250, 246, 234)


class FakeDNA:
    def __init__(self, **kw):
        self.color_usage = kw.get("color_usage", "")
        self.imagery_style = kw.get("imagery_style", "")
        self.mood = kw.get("mood", "")
        self.motifs = kw.get("motifs", "")
        self.signature_moves = kw.get("signature_moves", "")
        self.typographic_character = kw.get("typographic_character", "")
        self.do_list = kw.get("do_list", [])
        self.dont_list = kw.get("dont_list", [])


def _img(tmp_path, name, fill, patches=()):
    """Build a deterministic fixture: a field colour plus optional rectangular patches."""
    from PIL import Image, ImageDraw
    im = Image.new("RGB", (600, 750), fill)
    d = ImageDraw.Draw(im)
    for box, colour in patches:
        d.rectangle(box, fill=colour)
    p = tmp_path / name
    im.save(p)
    return p


@pytest.fixture(scope="module")
def marasim_direction():
    if not _HAVE:
        pytest.skip("Marasim artifacts not present")
    from brand.creative_dna import load_creative_dna
    profile = json.loads(BRAND_PATH.read_text(encoding="utf-8")).get("_profile") or {}
    return resolve_visual_direction(brand_dna=load_creative_dna(DNA_PATH), profile=profile,
                                    dna_path=str(DNA_PATH))


# ===================== the 18 deterministic fixtures =========================
def test_01_warm_compliant_marasim_like(tmp_path, marasim_direction):
    p = _img(tmp_path, "warm.png", BLACK, [((100, 300, 500, 420), ORANGE)])
    r = analyze_palette(p, marasim_direction)
    assert r.verdict == PASS, r.defects


def test_02_strongly_cool_violates(tmp_path, marasim_direction):
    p = _img(tmp_path, "cool.png", TEAL)
    r = analyze_palette(p, marasim_direction)
    assert r.verdict == FAIL
    assert BRAND_PALETTE_PROHIBITION_VIOLATION in [d["defect"] for d in r.defects]


def test_03_mostly_dark_with_tiny_blue_detail_passes(tmp_path, marasim_direction):
    """INCIDENTAL_COLOR: a small blue chip must not fail a poster."""
    p = _img(tmp_path, "tinyblue.png", BLACK,
             [((100, 300, 500, 420), ORANGE), ((10, 10, 40, 40), (40, 90, 220))])
    r = analyze_palette(p, marasim_direction)
    assert r.verdict == PASS, r.defects
    assert r.prohibited_ratio < POLICY["DOMINANT_PROHIBITED_PALETTE"]


def test_04_dark_plus_orange_compliant(tmp_path, marasim_direction):
    p = _img(tmp_path, "darkorange.png", BLACK, [((50, 50, 550, 700), (20, 18, 16)),
                                                 ((200, 350, 400, 400), ORANGE)])
    assert analyze_palette(p, marasim_direction).verdict == PASS


def test_05_light_pastel_cool_violation(tmp_path, marasim_direction):
    p = _img(tmp_path, "pastel.png", PASTEL, [((200, 300, 400, 400), ORANGE)])
    r = analyze_palette(p, marasim_direction)
    assert r.verdict == FAIL
    assert BACKGROUND_PREFERENCE_VIOLATION in [d["defect"] for d in r.defects]


def test_06_legitimate_small_sky_region_passes(tmp_path, marasim_direction):
    """A small sky is incidental; it must not read as the palette."""
    p = _img(tmp_path, "sky.png", BLACK, [((0, 0, 600, 70), (90, 150, 210)),
                                          ((150, 350, 450, 430), ORANGE)])
    r = analyze_palette(p, marasim_direction)
    assert r.verdict == PASS, r.defects


def test_07_exact_accent_hex_is_detected(tmp_path, marasim_direction):
    p = _img(tmp_path, "exact.png", BLACK, [((100, 300, 500, 420), ORANGE)])
    r = analyze_palette(p, marasim_direction)
    assert r.required_accent_presence is True


def test_08_nearby_perceptual_orange_is_detected(tmp_path, marasim_direction):
    """A generator drifts in tone; exact RGB equality would never match."""
    p = _img(tmp_path, "near.png", BLACK, [((100, 300, 500, 420), (232, 96, 44))])
    r = analyze_palette(p, marasim_direction)
    assert r.required_accent_presence is True, r.required_accent_ratio


def test_09_no_orange_accent_is_flagged(tmp_path, marasim_direction):
    p = _img(tmp_path, "noaccent.png", BLACK, [((100, 300, 500, 420), WHITE)])
    r = analyze_palette(p, marasim_direction)
    assert r.required_accent_presence is False
    assert MISSING_REQUIRED_ACCENT in [d["defect"] for d in r.defects]


def test_10_forbidden_teal_dominant_image(tmp_path, marasim_direction):
    p = _img(tmp_path, "teal.png", TEAL, [((100, 300, 500, 420), ORANGE)])
    r = analyze_palette(p, marasim_direction)
    assert r.verdict == FAIL
    assert BRAND_PALETTE_PROHIBITION_VIOLATION in [d["defect"] for d in r.defects]


def test_11_final_prompt_with_hidden_cool_clean_palette_is_caught(marasim_direction):
    prompt = ("A poster background.\n\nTHE LOOK: cinematic lighting, cool clean colour palette, "
              "bright soft daylight, crisp and airy, 50mm lens")
    bad = scan_request_for_contradictions(prompt, marasim_direction)
    assert bad, "the buried contradiction must be caught before spend"
    assert bad[0]["defect"] == GENERATION_REQUEST_CONTRADICTS_VISUAL_AUTHORITY


def test_12_final_prompt_fully_aligned_is_clean(marasim_direction):
    from poster.visual_spec import compile_visual_spec
    pos, neg = compile_visual_spec(marasim_direction).to_provider_directives()
    prompt = (f"A poster background.\n\nBRAND VISUAL AUTHORITY:\n{pos}\n\n"
              f"PROHIBITED BY THIS BRAND:\n{neg}")
    assert scan_request_for_contradictions(prompt, marasim_direction) == []


def test_13_model_passes_but_deterministic_gate_fails(tmp_path, marasim_direction):
    """The exact incident: model says on_brand_color=true over a prohibited-dominant frame."""
    p = _img(tmp_path, "incident.png", TEAL)
    pal = analyze_palette(p, marasim_direction)
    merged = merge_findings(pal, qa_on_brand_color=True, qa_grounded=None)
    assert merged["verdict"] == FAIL
    assert merged["authority"] == "deterministic"
    assert BRAND_PALETTE_PROHIBITION_VIOLATION in merged["reason_codes"]


def test_14_model_fails_with_an_ungrounded_rule_is_non_blocking(tmp_path, marasim_direction):
    p = _img(tmp_path, "ok.png", BLACK, [((100, 300, 500, 420), ORANGE)])
    pal = analyze_palette(p, marasim_direction)
    merged = merge_findings(pal, qa_on_brand_color=False, qa_grounded=False)
    assert merged["verdict"] == PASS
    assert merged["reason_codes"] == ["BRAND_CONSTRAINT_NOT_GROUNDED"]


def test_15_both_pass(tmp_path, marasim_direction):
    p = _img(tmp_path, "both.png", BLACK, [((100, 300, 500, 420), ORANGE)])
    merged = merge_findings(analyze_palette(p, marasim_direction), True, None)
    assert merged["verdict"] == PASS and merged["authority"] == "deterministic+model"


def test_16_background_black_requirement_missing_is_flagged(tmp_path, marasim_direction):
    p = _img(tmp_path, "lightbg.png", WHITE, [((100, 300, 500, 420), ORANGE)])
    r = analyze_palette(p, marasim_direction)
    assert BACKGROUND_PREFERENCE_VIOLATION in [d["defect"] for d in r.defects]
    lin = [c for c in r.constraint_lineage if c["constraint"] == "background_preferences"]
    assert lin and lin[0]["source_field"] == "background_preferences"
    assert lin[0]["source_id"] == marasim_direction.resolution_hash


def test_17_incidental_prohibited_pixels_below_threshold_pass(tmp_path, marasim_direction):
    """Boundary: prohibited present but well under the dominance threshold."""
    p = _img(tmp_path, "incidental.png", BLACK,
             [((100, 300, 500, 420), ORANGE), ((0, 700, 600, 750), TEAL)])
    r = analyze_palette(p, marasim_direction)
    assert r.prohibited_ratio < POLICY["DOMINANT_PROHIBITED_PALETTE"]
    assert BRAND_PALETTE_PROHIBITION_VIOLATION not in [d["defect"] for d in r.defects]


def test_18_contradictory_dna_conflicts_before_generation():
    dna = FakeDNA(color_usage="a deep blue field with blue accents",
                  dont_list=["Do not use blue anywhere."])
    rvd = resolve_visual_direction(brand_dna=dna, profile={})
    assert rvd.conflicts and rvd.conflicts[0]["type"] == "VISUAL_DNA_CONFLICT"


# ===================== boundary cases on the policy ==========================
def test_dominance_threshold_boundary(tmp_path, marasim_direction):
    """Just under and just over DOMINANT_PROHIBITED_PALETTE must land differently."""
    from PIL import ImageDraw
    from PIL import Image
    thr = POLICY["DOMINANT_PROHIBITED_PALETTE"]
    # `prohibited_ratio` is a straight fraction of the frame, so a teal band covering exactly
    # `frac` of the rows lands at `frac`. Both sides are placed 6 points off the threshold.
    for frac, expect_flag in ((thr - 0.06, False), (thr + 0.06, True)):
        im = Image.new("RGB", (600, 750), BLACK)
        d = ImageDraw.Draw(im)
        d.rectangle((0, 0, 600, int(750 * frac)), fill=TEAL)
        d.rectangle((100, 700, 500, 740), fill=ORANGE)      # keep the accent requirement met
        p = tmp_path / f"boundary_{frac:.2f}.png"
        im.save(p)
        r = analyze_palette(p, marasim_direction)
        flagged = BRAND_PALETTE_PROHIBITION_VIOLATION in [d2["defect"] for d2 in r.defects]
        assert flagged is expect_flag, (frac, r.prohibited_ratio, r.defects)
        assert abs(r.prohibited_ratio - frac) < 0.03, (frac, r.prohibited_ratio)


# ===================== §17 cross-brand safety ================================
def test_a_legitimately_cool_brand_is_not_forced_warm(tmp_path):
    dna = FakeDNA(color_usage="cool clinical blues and clean whites",
                  dont_list=["Do not use warm oranges."])
    rvd = resolve_visual_direction(brand_dna=dna, profile={"visual": {"palette_hex": ["#2a7fbf"]}})
    p = _img(tmp_path, "coolbrand.png", (42, 127, 191))
    r = analyze_palette(p, rvd)
    assert BRAND_PALETTE_PROHIBITION_VIOLATION not in [d["defect"] for d in r.defects]


def test_a_brand_with_a_blue_primary_keeps_it(tmp_path):
    rvd = resolve_visual_direction(brand_dna=None,
                                   profile={"visual": {"palette_hex": ["#2a7fbf"],
                                                       "primary_color": "#2a7fbf"}})
    assert "#2a7fbf" in rvd.primary_colors
    p = _img(tmp_path, "blueprimary.png", (42, 127, 191))
    r = analyze_palette(p, rvd)
    assert r.verdict in (PASS, NOT_APPLICABLE)


def test_a_monochrome_brand_with_no_prohibition_is_not_applicable(tmp_path):
    dna = FakeDNA(color_usage="strictly black and white", dont_list=[])
    rvd = resolve_visual_direction(brand_dna=dna, profile={})
    p = _img(tmp_path, "mono.png", (20, 20, 20))
    r = analyze_palette(p, rvd)
    assert r.verdict in (PASS, NOT_APPLICABLE)


def test_a_highly_saturated_brand_without_prohibition_passes(tmp_path):
    dna = FakeDNA(color_usage="vivid magenta and yellow", dont_list=[])
    rvd = resolve_visual_direction(brand_dna=dna, profile={})
    p = _img(tmp_path, "sat.png", (220, 40, 160))
    assert analyze_palette(p, rvd).verdict in (PASS, NOT_APPLICABLE)


def test_a_brand_with_no_explicit_palette_authority_is_not_assessed(tmp_path):
    rvd = resolve_visual_direction(brand_dna=None, profile={})
    p = _img(tmp_path, "none.png", (128, 128, 128))
    r = analyze_palette(p, rvd)
    assert r.verdict == NOT_APPLICABLE


# ===================== §12 repair routing ====================================
def test_repair_lanes_are_narrow():
    assert repair_lane_for(BRAND_PALETTE_PROHIBITION_VIOLATION) == "BACKGROUND_GENERATION/PALETTE_REPAIR"
    assert repair_lane_for(BACKGROUND_PREFERENCE_VIOLATION) == "BACKGROUND_GENERATION"
    assert repair_lane_for(MISSING_REQUIRED_ACCENT) == "PALETTE_REPAIR"
    assert repair_lane_for(GENERATION_REQUEST_CONTRADICTS_VISUAL_AUTHORITY) == "PROMPT_COMPILER"
    assert repair_lane_for("SOMETHING_ELSE") == ""


# ===================== the three REAL posters ================================
@pytest.mark.skipif(not _HAVE, reason="Marasim artifacts not present")
def test_the_three_real_marasim_posters_are_separated(marasim_direction):
    cases = [
        (REPO / "outputs" / "posters" / "poster_e3a5e608.png", FAIL, "cool verification render"),
        (REPO / "artifacts" / "marasim_app_generated.png", FAIL, "pastel Gulf render"),
        (REPO / ".claude" / "worktrees" / "marasim-poster-design-eb8305" / "artifacts" /
         "marasim" / "marasim_poster.png", PASS, "hand-made, audited"),
    ]
    seen = 0
    for path, expect, label in cases:
        if not path.is_file():
            continue
        r = analyze_palette(path, marasim_direction)
        assert r.verdict == expect, f"{label}: {r.verdict} {r.defects}"
        seen += 1
    assert seen >= 1


# ===================== §14 transport retry accounting ========================
def test_transport_retries_are_counted_separately_from_creative_attempts(monkeypatch):
    """A 429 re-send is transport, not a new creative attempt.

    The prior controlled run reported "1 provider call" while three HTTP requests were issued
    (one generation + two 429 retries). Logical and transport counts are now distinct.
    """
    import poster.oneshot as oneshot

    oneshot.reset_transport_stats()
    st = oneshot.transport_stats()
    assert st["provider_request_attempts"] == 0
    assert st["transport_retries"] == 0
    assert st["successful_billable_generations"] == 0
    assert "not observable here" in st["note"], "opacity must be stated, not implied"

    oneshot.TRANSPORT_STATS["provider_request_attempts"] += 3
    oneshot.TRANSPORT_STATS["transport_retries"] += 2
    oneshot.TRANSPORT_STATS["provider_errors"] += 2
    oneshot.TRANSPORT_STATS["successful_billable_generations"] += 1
    st = oneshot.transport_stats()
    # one logical generation, three HTTP attempts, one billable result
    assert st["provider_request_attempts"] == 3
    assert st["transport_retries"] == 2
    assert st["successful_billable_generations"] == 1
    assert st["provider_request_attempts"] != st["successful_billable_generations"]
    oneshot.reset_transport_stats()


def test_background_verdict_is_withheld_when_the_template_frame_dominates(tmp_path,
                                                                          marasim_direction):
    """A third state, not a guess.

    MEASURED on `poster_bac9ec3a`: 58% of the sampled margin was the compositor's near-white page
    border, so a 28.7% dark reading said nothing about the GENERATED background (60.7% dark on the
    image region). An unmeasurable background is not a passing background and not a failing one.
    """
    from PIL import Image, ImageDraw
    im = Image.new("RGB", (600, 750), (252, 250, 246))          # white page
    d = ImageDraw.Draw(im)
    d.rectangle((60, 150, 540, 560), fill=BLACK)                # the dark generated card
    d.rectangle((150, 300, 450, 360), fill=ORANGE)
    p = tmp_path / "framed.png"
    im.save(p)

    r = analyze_palette(p, marasim_direction)
    assert r.template_frame_ratio > POLICY["TEMPLATE_FRAME_MAX"]
    assert r.background_check_state == "NOT_ASSESSED"
    assert BACKGROUND_PREFERENCE_VIOLATION not in [x["defect"] for x in r.defects]


def test_background_verdict_is_given_when_there_is_no_template_frame(tmp_path, marasim_direction):
    p = _img(tmp_path, "nolight.png", WHITE, [((100, 300, 500, 420), ORANGE)])
    r = analyze_palette(p, marasim_direction)
    assert r.background_check_state == "FAIL"
    assert BACKGROUND_PREFERENCE_VIOLATION in [x["defect"] for x in r.defects]
