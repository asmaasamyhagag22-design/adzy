"""Vision QA gate — a REAL self-critique that rejects and triggers regeneration.

Per the build brief (Step H): after rendering the FINAL poster, a vision model inspects the
actual rendered image and returns a structured verdict. On fail the pipeline regenerates
(bounded retries); if it still fails it returns a CLEAR ERROR, never a broken poster.

Checks (on the rendered poster, not just the background):
  * has_latin_text   — Latin letters in the HEADLINE / CHIPS / CTA (the brand LOGO is exempt)
  * cta_clipped      — the CTA / any element cut off at the frame edge
  * on_brand_color   — the dominant colour matches the brand (e.g. WE = purple), not random
  * candid_violation — candid/documentary look or flat natural lighting when the brand DNA
                       forbids it
  * overall_pass + reason

Side-effect free (multimodal caller injected). Degrades: no caller → a permissive PASS with a
note (we can't gate without vision). Never raises.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from pydantic import BaseModel


class PosterQAVerdict(BaseModel):
    has_latin_text: bool = False
    cta_clipped: bool = False
    on_brand_color: bool = True
    candid_violation: bool = False
    logo_ok: bool = True            # brand mark crisp/complete (hard gate)
    single_focal: bool = True       # one clear focal point, not competing ones
    strong_typography: bool = True  # an intentional display lockup, not a default font
    cta_prominent: bool = True      # a confident button, not a footnote
    script_wellformed: bool = True  # copy well-formed for its script (Arabic ligatures/dots) — hard gate
    # PRODUCT AUTHENTICITY (owner-caught: topshoes' poster showed an INVENTED sneaker with a
    # swoosh — a false product claim invisible to every text gate). Additive, hard gates:
    invented_product: bool = False  # a fabricated hero product presented as the brand's merchandise
    third_party_mark: bool = False  # any other company's logo/trademark rendered in the scene
    score: int = 7                  # art-director score 1..10
    # TRI-STATE (P6, 2026-07-15): True = PASS, False = FAIL, None = NOT_CHECKED.
    # It used to default to True, so a single Vertex 429 on the QA call silently converted the art
    # critic from strict to absent while the verdict still said "passed" — "cannot verify" spelled
    # as "verified". None is falsy, so `if qa.overall_pass` fails CLOSED.
    overall_pass: Optional[bool] = None
    reason: str = ""
    checked: bool = False           # True only when a vision model actually inspected it


class _QAResponse(BaseModel):
    has_latin_text: bool
    cta_clipped: bool
    on_brand_color: bool
    candid_violation: bool
    overall_pass: bool
    reason: str
    # Art-director rubric (defaults keep older callers/tests valid; the model fills them).
    logo_ok: bool = True
    single_focal: bool = True
    strong_typography: bool = True
    cta_prominent: bool = True
    script_wellformed: bool = True
    invented_product: bool = False   # additive (permissive default keeps older tests valid)
    third_party_mark: bool = False
    score: int = 7


def poster_vision_qa(
    poster_png: "str | Path | bytes",
    *,
    caller: Any = None,
    brand_dna: Any = None,
    arabic: bool = True,
    expect_real_products: Optional[bool] = None,
) -> PosterQAVerdict:
    """Inspect the FINAL rendered poster. Returns a verdict; `checked=False` (permissive pass)
    when no multimodal caller is available."""
    if caller is None:
        return PosterQAVerdict(reason="no vision caller: QA skipped", checked=False)

    try:
        data = poster_png if isinstance(poster_png, bytes) else Path(poster_png).read_bytes()
    except Exception:
        return PosterQAVerdict(reason="could not read poster image", checked=False)

    brand_color = ""
    if brand_dna is not None:
        brand_color = (getattr(brand_dna, "color_usage", "") or "").strip()
    lang_line = (
        "The brand is ARABIC: the headline, chips and CTA must be Arabic. has_latin_text = true "
        "if ANY of the HEADLINE / CHIPS / CTA contain Latin letters. IGNORE the brand logo "
        "(it may contain Latin — that is fine)."
        if arabic else
        "has_latin_text = false unless there is clearly broken/garbled text."
    )
    color_line = (f"The brand's colour language is: {brand_color}. on_brand_color = true only if "
                  "the poster's DOMINANT colour matches that." if brand_color else
                  "on_brand_color = true unless the colours look random/off for a brand poster.")

    system = (
        "You are a WORLD-CLASS advertising ART DIRECTOR reviewing a FINAL rendered poster. "
        "Your bar: 'Would a top brand actually run this?' Be strict — return a structured verdict.\n"
        f"- {lang_line}\n"
        "- logo_ok = true ONLY if the brand logo/lockup is crisp, complete and correctly spaced "
        "with NO garbled/overlapping/duplicated glyphs or stray characters (HARD GATE).\n"
        "- cta_clipped = true if the CTA, or any text/chip/logo, is cut off at / outside the frame.\n"
        "- cta_prominent = true if the CTA reads as a confident, high-contrast button (not a "
        "tiny low-contrast footnote).\n"
        "- single_focal = true if there is ONE clear focal point with a clean eye-path to the "
        "headline (false if focal points compete).\n"
        "- strong_typography = true if the headline reads as an intentional display LOCKUP "
        "(deliberate weight/size hierarchy), not a flat default font.\n"
        "- script_wellformed = true ONLY if every rendered copy line is WELL-FORMED for its "
        "script — this is stricter than 'no Latin'. For Arabic: letters must be correctly "
        "SHAPED and CONNECTED (no isolated/disjointed glyphs, no broken ligatures), with correct "
        "dots (ت=2, ث=3) and ة/ه as intended. A poster with NO Latin but broken/garbled/"
        "disconnected Arabic is script_wellformed = FALSE (HARD GATE).\n"
        f"- {color_line}\n"
        "- candid_violation = true if the imagery looks candid/documentary or flat natural "
        "lighting (amateur/stock) rather than a polished brand campaign.\n"
        # PRODUCT AUTHENTICITY (additive, 2026-07-12 — the topshoes invented-sneaker class).
        # FAIL-CLOSED (P6 #9): ask unless we KNOW real product photos were supplied. This was
        # `if expect_real_products is False`, but poster/pipeline.py calls QA without the argument
        # => None => the instruction was OMITTED => invented_product kept its permissive default
        # => the hard gate could never fire, while the conjunction below still named it and
        # overall_pass=True certified the poster clean. So the owner-caught fabricated hero sneaker
        # shipped on every classic poster. None (unknown) must ask; only True may skip.
        + ("- invented_product = true if the poster features a SPECIFIC hero product presented "
           "as this brand's merchandise even though NO real product photo was provided — a "
           "fabricated shoe/bottle/device/dish shown as theirs (HARD GATE: a fake product is a "
           "false claim). Generic out-of-focus background objects are fine.\n"
           if expect_real_products is not True else "")
        + "- third_party_mark = true if ANY other company's logo or trademark appears anywhere "
        "in the scene — a swoosh, an apple mark, any recognizable brand symbol on objects or "
        "clothing (HARD GATE).\n"
        "- score = 1..10 overall creative quality as a premium brand poster.\n"
        "- overall_pass = true ONLY if logo_ok AND script_wellformed AND no Latin in the copy AND "
        "no clipping AND on_brand_color AND no candid violation AND no invented_product AND no "
        "third_party_mark AND score >= 7.\n"
        "- reason = one short sentence naming the biggest problem (or 'clean')."
    )
    user = "Review this rendered poster as an art director and return the verdict."

    try:
        resp, _u = caller(system, user, _QAResponse, group_name="poster_vision_qa",
                          images=[(data, "image/png")])
    except Exception as exc:  # noqa: BLE001
        return PosterQAVerdict(reason=f"QA call failed ({type(exc).__name__})", checked=False)

    return PosterQAVerdict(
        has_latin_text=bool(resp.has_latin_text),
        cta_clipped=bool(resp.cta_clipped),
        on_brand_color=bool(resp.on_brand_color),
        candid_violation=bool(resp.candid_violation),
        logo_ok=bool(resp.logo_ok),
        single_focal=bool(resp.single_focal),
        strong_typography=bool(resp.strong_typography),
        cta_prominent=bool(resp.cta_prominent),
        script_wellformed=bool(resp.script_wellformed),
        invented_product=bool(resp.invented_product),
        third_party_mark=bool(resp.third_party_mark),
        score=int(resp.score or 0),
        # code-side hard gates re-ANDed: a crisp logo, well-formed script, NO invented hero
        # product and NO third-party mark are non-negotiable — a model that answers
        # overall_pass=true but flags any of them still FAILS.
        overall_pass=bool(resp.overall_pass) and bool(resp.logo_ok)
        and bool(resp.script_wellformed) and not bool(resp.invented_product)
        and not bool(resp.third_party_mark),
        reason=(resp.reason or "").strip(),
        checked=True,
    )
