# -*- coding: utf-8 -*-
"""Deterministic enforcement of a brand's EXPLICIT, machine-checkable palette constraints.

WHY (measured, verification render `poster_e3a5e608`, marasimltd.com, 2026-08-31). The resolved
visual authority was correct and reached the provider request in full — warm/orange/charcoal,
"deep black field background", and an explicit `NO blue, teal, green…` block. The model rendered a
pale mint/teal frame anyway, and the model-based `on_brand_color` check returned **true**:

    saturated frame  61.7% cool / 17.3% warm
    background margin 76% cool
    prohibited ratio  76.5%

Asking a language model "does the dominant colour match this prose?" is a different question from
"is a prohibited hue dominating the frame?". The first is a judgement; the second is a measurement.
So the measurement becomes authoritative for the constraints that ARE measurable, and the model
keeps the semantic checks it is actually good at (composition, context, typography, stray text).

DELIBERATELY NOT a whole-image "any blue > X" rule. That fails a poster for antialiasing, a small
UI chip, a shadow, a legitimate sky, or a logo's own colours. Regions and roles are separated:
DOMINANT_PALETTE, BACKGROUND_FIELD, ACCENT_PRESENCE, INCIDENTAL_COLOR — and which of them is
checked at all is decided by the brand's own ResolvedVisualDirection, never by this file.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Optional

# --- typed defects ----------------------------------------------------------
BRAND_PALETTE_PROHIBITION_VIOLATION = "BRAND_PALETTE_PROHIBITION_VIOLATION"
BACKGROUND_PREFERENCE_VIOLATION = "BACKGROUND_PREFERENCE_VIOLATION"
MISSING_REQUIRED_ACCENT = "MISSING_REQUIRED_ACCENT"
GENERATION_REQUEST_CONTRADICTS_VISUAL_AUTHORITY = (
    "GENERATION_REQUEST_CONTRADICTS_VISUAL_AUTHORITY")

PASS, FAIL, NOT_APPLICABLE, NOT_ASSESSED = "PASS", "FAIL", "NOT_APPLICABLE", "NOT_ASSESSED"

# --- versioned threshold policy --------------------------------------------
#
# Named and versioned on purpose. The Marasim evidence (0.4% prohibited on the accepted
# hand-made poster, 76.5% on the rejected render) shows the incident separates by a wide margin,
# which is exactly why the thresholds must NOT be fitted to those two numbers. They are set at
# defensible round values with headroom on both sides, and boundary cases are tested.
PALETTE_POLICY_ID = "palette_compliance"
PALETTE_POLICY_VERSION = 1
POLICY = {
    # A prohibited hue owning this much of the SATURATED frame is the palette, not an accent.
    "DOMINANT_PROHIBITED_PALETTE": 0.35,
    # When the direction explicitly asks for a dark field, this much of the background margin
    # must actually be dark. Not "pure black" — materially dark.
    "BACKGROUND_CONTRADICTION_MIN_DARK": 0.45,
    # An accent is small by definition — a rule, an eyebrow, a button edge. MEASURED across
    # three real Marasim posters at 960px: a poster whose accent is TYPOGRAPHIC scores 0.142%,
    # one with a large orange subject 1.42%, and a warm-dominant one 6.79%. A floor of 0.05%
    # keeps ~3x headroom under the thinnest legitimate case. Measured at `accent_width`, not
    # at the coarse palette resolution: a 240px downsample erases hairline strokes and failed
    # the hand-made poster that visibly uses #F04B1E in four places.
    "MISSING_REQUIRED_ACCENT_MIN": 0.0005,
    "accent_width": 960,
    # Below this, a prohibited hue is incidental: antialiasing, a chip, a reflection.
    "INCIDENTAL_MAX": 0.08,
    "min_saturation": 0.12,
    "dark_luma_max": 0.30,
    # Perceptual distance (CIE76 dE in Lab) within which a generated orange still counts as the
    # brand accent. Generators drift in tone; exact RGB equality would never match.
    "accent_delta_e_max": 30.0,
    "background_margin": 0.14,
    # Above this share of near-white margin the sample is the compositor's page, not the
    # generated background, and the background verdict is withheld rather than guessed.
    "TEMPLATE_FRAME_MAX": 0.35,
}

_COOL_HUE = (150.0, 270.0)          # blue / cyan / teal / green / violet
_WARM_HUE_LO, _WARM_HUE_HI = 330.0, 60.0


@dataclass
class PaletteComplianceResult:
    dominant_palette_distribution: dict = field(default_factory=dict)
    warm_ratio: float = 0.0
    cool_ratio: float = 0.0
    neutral_ratio: float = 0.0
    prohibited_ratio: float = 0.0
    background_dark_ratio: float = 0.0
    template_frame_ratio: float = 0.0
    background_check_state: str = NOT_ASSESSED
    required_accent_presence: bool = False
    required_accent_ratio: float = 0.0
    analyzed_region: str = "full_frame+background_margin"
    threshold_policy_id: str = f"{PALETTE_POLICY_ID}@v{PALETTE_POLICY_VERSION}"
    constraints_checked: list = field(default_factory=list)
    constraint_lineage: list = field(default_factory=list)
    verdict: str = NOT_ASSESSED
    defects: list = field(default_factory=list)

    def as_dict(self) -> dict:
        return dict(self.__dict__)


def _rgb_to_lab(rgb):
    """sRGB (0..255) -> CIE Lab D65. Vectorised."""
    import numpy as np
    c = np.asarray(rgb, dtype=float) / 255.0
    c = np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)
    m = np.array([[0.4124, 0.3576, 0.1805],
                  [0.2126, 0.7152, 0.0722],
                  [0.0193, 0.1192, 0.9505]])
    xyz = c @ m.T
    xyz = xyz / np.array([0.95047, 1.0, 1.08883])
    f = np.where(xyz > 0.008856, np.cbrt(xyz), 7.787 * xyz + 16.0 / 116.0)
    L = 116.0 * f[..., 1] - 16.0
    a = 500.0 * (f[..., 0] - f[..., 1])
    b = 200.0 * (f[..., 1] - f[..., 2])
    return np.stack([L, a, b], axis=-1)


def _hex_to_rgb(h: str):
    h = str(h).strip().lstrip("#")
    if len(h) == 3:
        h = "".join(ch * 2 for ch in h)
    return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))


def analyze_palette(image_path: Any, direction: Any, *,
                    policy: Optional[dict] = None) -> PaletteComplianceResult:
    """Measure one render against the brand's own explicit palette constraints.

    Every check is switched on by the ResolvedVisualDirection, so a brand that prohibits nothing
    is NOT_APPLICABLE rather than silently passed, and a brand whose primary IS blue is judged
    against its own constraints.
    """
    import numpy as np
    from PIL import Image

    pol = dict(POLICY)
    pol.update(policy or {})
    res = PaletteComplianceResult()

    prohibited_terms = [str(t).lower() for t in
                        (getattr(direction, "palette_prohibited", None) or [])]
    bg_pref = str(getattr(direction, "background_preferences", "") or "").lower()
    accents = [a for a in (getattr(direction, "accent_colors", None) or [])
               if str(a).startswith("#")]
    rid = getattr(direction, "resolution_hash", "") or ""
    src = getattr(direction, "dna_source_artifact", "") or "ResolvedVisualDirection"

    cool_prohibited = any(t in prohibited_terms for t in
                          ("blue", "teal", "cyan", "green", "navy", "turquoise", "mint",
                           "olive", "purple", "violet", "lilac", "lavender"))
    warm_prohibited = any(t in prohibited_terms for t in
                          ("orange", "amber", "gold", "red", "crimson", "rust", "sepia"))
    wants_dark_bg = any(w in bg_pref for w in ("black", "charcoal", "dark", "deep"))

    if not (cool_prohibited or warm_prohibited or wants_dark_bg or accents):
        res.verdict = NOT_APPLICABLE
        res.defects.append("no machine-checkable palette constraint supplied")
        return res

    im = Image.open(image_path).convert("RGB")
    im = im.resize((240, 300))
    arr = np.asarray(im, dtype=float)
    h, w, _ = arr.shape

    mx = arr.max(axis=2) / 255.0
    mn = arr.min(axis=2) / 255.0
    light = (mx + mn) / 2.0
    delta = mx - mn
    with np.errstate(divide="ignore", invalid="ignore"):
        sat = np.where(delta == 0, 0.0,
                       delta / np.where(light < 0.5, (mx + mn), (2.0 - mx - mn)))
    sat = np.nan_to_num(sat)

    r, g, b = arr[..., 0] / 255.0, arr[..., 1] / 255.0, arr[..., 2] / 255.0
    hue = np.zeros_like(mx)
    nz = delta > 0
    rmax, gmax, bmax = (mx == r) & nz, (mx == g) & nz, (mx == b) & nz
    hue[rmax] = (((g - b)[rmax] / delta[rmax]) % 6.0)
    hue[gmax] = (((b - r)[gmax] / delta[gmax]) + 2.0)
    hue[bmax] = (((r - g)[bmax] / delta[bmax]) + 4.0)
    hue = hue * 60.0

    saturated = sat >= pol["min_saturation"]
    n_sat = int(saturated.sum())
    cool = saturated & (hue >= _COOL_HUE[0]) & (hue <= _COOL_HUE[1])
    warm = saturated & ((hue < _WARM_HUE_HI) | (hue > _WARM_HUE_LO))

    total = h * w
    res.cool_ratio = round(float(cool.sum()) / max(n_sat, 1), 4)
    res.warm_ratio = round(float(warm.sum()) / max(n_sat, 1), 4)
    res.neutral_ratio = round(1.0 - (n_sat / total), 4)
    res.dominant_palette_distribution = {
        "warm": res.warm_ratio, "cool": res.cool_ratio,
        "neutral_unsaturated": res.neutral_ratio, "saturated_pixels": n_sat}

    # BACKGROUND_FIELD — the outer margin, where the field lives and the subject does not.
    m = pol["background_margin"]
    mask_bg = np.zeros((h, w), dtype=bool)
    mask_bg[: int(h * m), :] = True
    mask_bg[int(h * (1 - m)):, :] = True
    mask_bg[:, : int(w * m)] = True
    mask_bg[:, int(w * (1 - m)):] = True
    luma = (0.2126 * r + 0.7152 * g + 0.0722 * b)
    res.background_dark_ratio = round(
        float((mask_bg & (luma <= pol["dark_luma_max"])).sum()) / max(int(mask_bg.sum()), 1), 4)
    # HOW MUCH OF THAT MARGIN IS THE COMPOSITOR'S FRAME, not the generated image?
    #
    # MEASURED on `poster_bac9ec3a` (2026-09-01): this check reported BACKGROUND_PREFERENCE_
    # VIOLATION at 28.7% dark against a 45% floor — while 58% of the sampled margin was the HTML
    # template's near-white page border. The generated region of that same poster is 60.7% dark
    # and would pass. The check was measuring the compositor and blaming the image model.
    #
    # Cropping the frame was tried and rejected: the frame carries the logo and footer, so a
    # uniformity crop stops early and still under-reports. So when the frame owns too much of the
    # margin this returns a THIRD STATE instead of a verdict it cannot support — an unmeasurable
    # background is not a passing background, and it is not a failing one either.
    #
    # "Near-white margin" alone is NOT enough to call it a frame: a poster with a genuinely light
    # background is also near-white at the edges, and withholding there would hide the very
    # violation this check exists to find. A compositor frame is near-white AND markedly LIGHTER
    # than the picture it surrounds; a light background is uniform all the way through.
    _white_margin = float((mask_bg & (luma >= 0.85)).sum()) / max(int(mask_bg.sum()), 1)
    _inner = ~mask_bg
    _margin_mean = float(luma[mask_bg].mean()) if int(mask_bg.sum()) else 0.0
    _inner_mean = float(luma[_inner].mean()) if int(_inner.sum()) else 0.0
    res.template_frame_ratio = round(
        _white_margin if (_margin_mean - _inner_mean) > 0.25 else 0.0, 4)

    # PROHIBITED — measured over the saturated frame only.
    prohibited_mask = cool if cool_prohibited else (warm if warm_prohibited else None)
    if prohibited_mask is not None:
        # DOMINANCE IS A FRACTION OF THE FRAME, not of the saturated subset. Measuring against
        # `n_sat` made a mostly-black poster hypersensitive: a near-black field is unsaturated, so
        # it leaves the denominator, and a small teal band then reads as 76% of "the palette". A
        # legitimate small sky and an incidental blue chip both failed that way. Dominance means
        # "owns the picture", which is area.
        res.prohibited_ratio = round(float(prohibited_mask.sum()) / total, 4)
        res.constraints_checked.append(BRAND_PALETTE_PROHIBITION_VIOLATION)
        res.constraint_lineage.append({
            "constraint": "palette_prohibited", "terms": prohibited_terms[:6],
            "source_type": "ResolvedVisualDirection", "source_field": "palette_prohibited",
            "source_id": rid, "source_artifact": src})

    # ACCENT_PRESENCE — perceptual distance, not RGB equality.
    if accents:
        res.constraints_checked.append(MISSING_REQUIRED_ACCENT)
        res.constraint_lineage.append({
            "constraint": "accent_colors", "terms": accents,
            "source_type": "ResolvedVisualDirection", "source_field": "accent_colors",
            "source_id": rid, "source_artifact": src})
        aw = int(pol["accent_width"])
        acc_im = Image.open(image_path).convert("RGB").resize((aw, int(aw * h / w)))
        acc_arr = np.asarray(acc_im, dtype=float)
        lab_img = _rgb_to_lab(acc_arr)
        hit = np.zeros(lab_img.shape[:2], dtype=bool)
        for a in accents:
            lab_a = _rgb_to_lab(np.array(_hex_to_rgb(a), dtype=float))
            d = np.sqrt(((lab_img - lab_a) ** 2).sum(axis=-1))
            hit |= d <= pol["accent_delta_e_max"]
        res.required_accent_ratio = round(float(hit.sum()) / hit.size, 5)
        res.required_accent_presence = res.required_accent_ratio >= pol["MISSING_REQUIRED_ACCENT_MIN"]

    if wants_dark_bg:
        res.constraints_checked.append(BACKGROUND_PREFERENCE_VIOLATION)
        res.constraint_lineage.append({
            "constraint": "background_preferences", "value": bg_pref,
            "source_type": "ResolvedVisualDirection", "source_field": "background_preferences",
            "source_id": rid, "source_artifact": src})

    # --- verdict ----------------------------------------------------------
    defects: list = []
    if prohibited_mask is not None and res.prohibited_ratio > pol["DOMINANT_PROHIBITED_PALETTE"]:
        defects.append({"defect": BRAND_PALETTE_PROHIBITION_VIOLATION,
                        "measured": res.prohibited_ratio,
                        "threshold": pol["DOMINANT_PROHIBITED_PALETTE"],
                        "region": "DOMINANT_PALETTE"})
    if wants_dark_bg:
        if res.template_frame_ratio > pol["TEMPLATE_FRAME_MAX"]:
            res.background_check_state = NOT_ASSESSED
        elif res.background_dark_ratio < pol["BACKGROUND_CONTRADICTION_MIN_DARK"]:
            res.background_check_state = FAIL
            defects.append({"defect": BACKGROUND_PREFERENCE_VIOLATION,
                            "measured": res.background_dark_ratio,
                            "threshold": pol["BACKGROUND_CONTRADICTION_MIN_DARK"],
                            "region": "BACKGROUND_FIELD"})
        else:
            res.background_check_state = PASS
    if accents and not res.required_accent_presence:
        defects.append({"defect": MISSING_REQUIRED_ACCENT,
                        "measured": res.required_accent_ratio,
                        "threshold": pol["MISSING_REQUIRED_ACCENT_MIN"],
                        "region": "ACCENT_PRESENCE"})

    res.defects = defects
    res.verdict = FAIL if defects else PASS
    return res


# --- §8: audit the FINAL serialized request, not the spec -------------------
_CONTRADICTION_PHRASES = {
    "cool": ("cool colour palette", "cool color palette", "cool clean colour palette",
             "cool clean color palette", "cool palette", "cool tones"),
    "pastel": ("airy pastel", "pastel palette", "soft pastel"),
    "clinical_daylight": ("bright soft daylight", "crisp and airy", "bright clinical daylight",
                          "clean bright daylight"),
}


def scan_request_for_contradictions(prompt: str, direction: Any) -> list[dict]:
    """Find generic language in the FINAL provider request that the brand explicitly forbids.

    Inspecting `PosterVisualSpec` alone was not enough: the spec was correct and the contradiction
    entered later, from a prompt helper's generic cinematic boilerplate. This reads the exact
    string about to be sent, so a contradiction is caught BEFORE spend.
    """
    low = (prompt or "").lower()
    prohibited = {str(t).lower() for t in (getattr(direction, "palette_prohibited", None) or [])}
    cool_forbidden = bool(prohibited & {"blue", "teal", "cyan", "green", "navy", "turquoise",
                                        "mint", "olive", "purple", "violet"})
    out: list[dict] = []
    if not cool_forbidden:
        return out
    for group, phrases in _CONTRADICTION_PHRASES.items():
        for ph in phrases:
            # Only flag the phrase where it is ASSERTED, not where it is prohibited
            # ("NO cool palette" must not trip the detector that exists to enforce it).
            idx = 0
            while True:
                i = low.find(ph, idx)
                if i < 0:
                    break
                window = low[max(0, i - 40):i]
                negated = any(k in window for k in ("no ", "not ", "avoid", "never", "without",
                                                    "prohibited", "do not"))
                if not negated:
                    out.append({"defect": GENERATION_REQUEST_CONTRADICTS_VISUAL_AUTHORITY,
                                "phrase": ph, "group": group, "offset": i,
                                "source_field": "palette_prohibited",
                                "source_id": getattr(direction, "resolution_hash", "")})
                    break
                idx = i + len(ph)
            if out and out[-1].get("phrase") == ph:
                continue
    return out


def request_hash(prompt: str) -> str:
    return hashlib.sha256((prompt or "").encode("utf-8")).hexdigest()


# --- §11 finding precedence -------------------------------------------------
def merge_findings(palette: Optional[PaletteComplianceResult],
                   qa_on_brand_color: Optional[bool],
                   qa_grounded: Optional[bool]) -> dict:
    """Combine the deterministic measurement with the model's judgement.

    A model cannot cancel a deterministic grounded hard failure. That is the whole point: the
    render that started this returned `on_brand_color=true` over a 76.5%-prohibited frame.
    """
    det = palette.verdict if palette is not None else NOT_ASSESSED
    if det == FAIL:
        return {"verdict": FAIL, "authority": "deterministic",
                "reason_codes": [d["defect"] for d in (palette.defects or [])],
                "note": "deterministic grounded violation — model judgement cannot override"}
    if qa_on_brand_color is False and qa_grounded is True:
        return {"verdict": FAIL, "authority": "model_grounded",
                "reason_codes": ["ON_BRAND_COLOR_FAIL"], "note": "model cited a supplied constraint"}
    if qa_on_brand_color is False and qa_grounded is False:
        return {"verdict": PASS, "authority": "non_blocking",
                "reason_codes": ["BRAND_CONSTRAINT_NOT_GROUNDED"],
                "note": "model cited a constraint no authority supplied — not blocking"}
    if det == PASS and qa_on_brand_color is True:
        return {"verdict": PASS, "authority": "deterministic+model", "reason_codes": [], "note": ""}
    if det in (NOT_APPLICABLE, NOT_ASSESSED) and qa_on_brand_color is True:
        return {"verdict": PASS, "authority": "model", "reason_codes": [], "note": ""}
    return {"verdict": "REVIEW", "authority": "uncertain", "reason_codes": [],
            "note": f"deterministic={det}, model_on_brand_color={qa_on_brand_color}"}


# --- §12 repair routing -----------------------------------------------------
_REPAIR_LANE = {
    BRAND_PALETTE_PROHIBITION_VIOLATION: "BACKGROUND_GENERATION/PALETTE_REPAIR",
    BACKGROUND_PREFERENCE_VIOLATION: "BACKGROUND_GENERATION",
    MISSING_REQUIRED_ACCENT: "PALETTE_REPAIR",
    GENERATION_REQUEST_CONTRADICTS_VISUAL_AUTHORITY: "PROMPT_COMPILER",
}


def repair_lane_for(defect: str) -> str:
    """Narrow lane per defect. Never 'regenerate the concept' — the strategy is not at fault."""
    return _REPAIR_LANE.get(defect, "")
