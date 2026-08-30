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

import re
from pathlib import Path
from typing import Any, Optional

from pydantic import BaseModel, Field


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
    # PLACEMENT (owner-caught 2026-07-20: the medicine box landed anywhere, any way, floating and
    # mis-scaled). Permissive default so old verdicts/models don't retro-fail every poster.
    placement_logical: bool = True   # the product sits somewhere physically real for its category
    # EVERY STRING THE CRITIC COULD READ. Without this the stray-text gate downstream sees an
    # empty list and reports "could not verify" forever — a dead gate that looks alive, which is
    # the failure class this repo keeps paying for.
    text_lines: list = Field(default_factory=list)
    # MALFORMED TEXT, judged on the PIXELS (2026-07-26). String-distance on a transcription was
    # measured to fail both ways — real label words absent from the product name look "invented",
    # and «FIC. TRUSTWORSHY» looks plausible. The critic can see the difference; code cannot.
    garbled_text: bool = False
    # HOW MANY DISTINCT PRODUCTS are in frame. A named-product ad showing three is the "chaotic
    # composition of multiple floating products" the owner rejected; 0 means nothing was counted.
    product_count: int = 0
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
    placement_logical: bool = True   # additive (permissive default keeps older tests valid)
    text_lines: list[str] = []       # every readable string in the image, verbatim
    garbled_text: bool = False       # any malformed / nonsensical / misspelled string in frame
    product_count: int = 0           # distinct products visible
    score: int = 7


def _recompute_pass(*, logo_ok: bool, script_wellformed: bool, invented_product: bool,
                    third_party_mark: bool, score: int, model_pass: bool,
                    logo_composited: bool = False) -> bool:
    """PD-1: the verdict is recomputed in CODE, never trusted from the model. `logo_composited`
    drops `logo_ok` from the conjunction for the same reason the prompt does."""
    ok = (bool(model_pass) and bool(script_wellformed) and not bool(invented_product)
          and not bool(third_party_mark) and int(score or 0) >= 7)
    return ok and (True if logo_composited else bool(logo_ok))


def _logo_criterion(composited: bool) -> str:
    """What `logo_ok` means. When the REAL asset was composited deterministically, the model-garbles-
    the-logo failure this gate was built for cannot happen — and judging letterforms then rejects the
    brand's own design (MEASURED on Volume Qatar: an intentionally interlocking wordmark read as
    "completely garbled" three renders in a row). It becomes a VISIBILITY check instead."""
    if not composited:
        return ("- logo_ok = true ONLY if the brand logo/lockup is crisp, complete and correctly "
                "spaced with NO garbled/overlapping/duplicated glyphs or stray characters "
                "(HARD GATE).\n")
    return ("- logo_ok = true if the brand mark is VISIBLE and legible against what sits behind it "
            "(enough contrast, not cropped, not covered by other elements). It was composited "
            "pixel-exactly from the brand's OWN file, so judge ONLY its visibility and placement — "
            "NEVER its letterforms: a stylised wordmark with overlapping or interlocking letters is "
            "the brand's design, not damage.\n")


def _pass_criteria(arabic: bool, *, logo_composited: bool = False) -> str:
    """The `overall_pass` conjunction — language-aware.

    MEASURED (Volume Qatar, 2026-07-21): a clean ENGLISH poster (verbatim fidelity 1.0, critic
    score 8/10) was rejected with "The poster uses Latin script, which violates the pass criteria."
    "No Latin in the copy" is correct for an ARABIC brand and wrong for every other one — the
    per-field instruction already knew that; this line did not. A D-8 language-axis leak: a true
    rule applied to a question it does not belong to.
    """
    logo_part = "" if logo_composited else "logo_ok AND "
    if arabic:
        return (f"- overall_pass = true ONLY if {logo_part}script_wellformed AND no Latin in the "
                "copy AND no clipping AND on_brand_color AND no candid violation AND no "
                "invented_product AND no third_party_mark AND score >= 7.\n")
    return (f"- overall_pass = true ONLY if {logo_part}script_wellformed AND no clipping AND "
            "on_brand_color AND no candid violation AND no invented_product AND no "
            "third_party_mark AND score >= 7. This brand's own language is NOT Arabic, so Latin "
            "copy is CORRECT and must never count against the poster.\n")


def poster_vision_qa(
    poster_png: "str | Path | bytes",
    *,
    caller: Any = None,
    brand_dna: Any = None,
    arabic: bool = True,
    expect_real_products: Optional[bool] = None,
    logo_composited: bool = False,
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
        # PLACEMENT (2026-07-20): the prompt now carries placement physics; the critic must be
        # able to SEE the failure, otherwise a floating box still ships. Phrased per CATEGORY, not
        # "on a surface" — shoes belong on the ground, a car on a road, a sofa in a room.
        + "- garbled_text = true if ANY text in the frame is malformed: misspelled, nonsensical, "
        "half-formed, or letters that only imitate writing. Judge the GLYPHS you can see. A "
        "correctly-spelled real brand or product name is NOT garbled, however unfamiliar.\n"
        + "- product_count: how many DISTINCT products (different items, not repeats of one) are "
        "visible in the frame. Count 0 if none.\n"
        + "- text_lines: EVERY readable string in the image, VERBATIM, one entry each — the "
        "headline, the subheadline, the button, any chip, any word on packaging, any sign, any "
        "fine print, in whatever script it appears. Transcribe exactly what is drawn, including "
        "misspelled or nonsensical strings; do NOT correct, translate, tidy or omit anything. "
        "This list is compared in code against the copy that was actually authored.\n"
        + "- placement_logical = true if any product shown sits somewhere physically real for a "
        "product OF ITS KIND: supported by something that would truly carry it (counter, shelf, "
        "ground, road, room, hand, worn), at a believable SIZE next to the objects around it, with "
        "a contact shadow and lighting that match the scene. FALSE if it floats/hovers unsupported, "
        "is absurdly large or small, casts no shadow where it touches, or is placed somewhere "
        "nonsensical for that category.\n"
        + "- third_party_mark = true if ANY other company's logo or trademark appears anywhere "
        "in the scene — a swoosh, an apple mark, any recognizable brand symbol on objects or "
        "clothing (HARD GATE).\n"
        "- score = 1..10 overall creative quality as a premium brand poster.\n"
        + _pass_criteria(arabic, logo_composited=logo_composited)
        + "- reason = one short sentence naming the biggest problem (or 'clean')."
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
        placement_logical=bool(resp.placement_logical),
        score=int(resp.score or 0),
        # code-side hard gates re-ANDed (PD-1 — recompute, never trust the model's overall_pass):
        # a crisp logo, well-formed script, NO invented hero product, NO third-party mark, AND the
        # promised quality floor (score >= 7, prompt vision_qa.py:139). P-25: score was promised as a
        # pass condition but not enforced — a model saying overall_pass=true with a 5 still passed.
        overall_pass=_recompute_pass(
            logo_ok=bool(resp.logo_ok), script_wellformed=bool(resp.script_wellformed),
            invented_product=bool(resp.invented_product),
            third_party_mark=bool(resp.third_party_mark), score=int(resp.score or 0),
            model_pass=bool(resp.overall_pass), logo_composited=logo_composited),
        reason=(resp.reason or "").strip(),
        text_lines=[str(t).strip() for t in (getattr(resp, "text_lines", None) or [])
                    if str(t).strip()],
        garbled_text=bool(getattr(resp, "garbled_text", False)),
        product_count=int(getattr(resp, "product_count", 0) or 0),
        checked=True,
    )



def product_count_ok(count: int, *, product_named: bool) -> Optional[bool]:
    """Is this many products right for this ad?

    OWNER (2026-07-26), on an ad for ONE pasted product that came back full of other brands'
    boxes: «إيه المنتجات؟ أنا بعمل لاروش». A named-product ad shows ONE product. A brand-wide ad
    may legitimately show a range. Nothing counted -> unverified, never a pass."""
    n = int(count or 0)
    if n <= 0:
        return None
    if not product_named:
        return True
    return n == 1


def third_party_is_expected(reason: str, *, product_name: str = "") -> bool:
    """Is the "third-party mark" the very product the owner asked us to advertise?

    ROOT (Al Fouad, 2026-07-26). The owner pasted a La Roche-Posay product link; the poster
    correctly showed that product; the QA hard-gate then failed it — "the ad is for a pharmacy
    but is dominated by a third-party product brand" — and the result shipped stamped
    untrustworthy. The rule is right for an INVENTED swoosh on a model's shirt and wrong for a
    retailer advertising the stock it sells. A true statement answering a different question.

    So: when a product was explicitly requested, a mark whose name appears in that product's
    name is EXPECTED, not a violation. Everything else stays a violation, and with no requested
    product nothing is ever excused."""
    name = str(product_name or "").strip().lower()
    text = str(reason or "").lower()
    if not name or not text:
        return False
    # Compare on the product's distinctive words, not the whole string: the verdict says
    # "La Roche-Posay", the product is "La Roche-Posay Anthelios Anti-Dark Spot Fluid SPF50+".
    stop = {"the", "and", "for", "with", "spf", "ml", "gm", "pack", "size", "new", "kit"}
    words = [w for w in re.split(r"[^\w؀-ۿ]+", name) if len(w) > 2 and w not in stop]
    if not words:
        return False
    hits = sum(1 for w in words if w in text)
    # Two matching words (or one, for a single-word brand) is the brand being named.
    return hits >= min(2, len(words))
