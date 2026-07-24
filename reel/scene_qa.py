"""Vision QA for a GENERATED reel scene — rejects Veo image-to-video hallucinations.

The owner watched a real render and caught three failures the prompt alone can't stop
(Veo drifts away from the seed over a few seconds): the product is NOT faithful to the
real photo, it performs an IMPOSSIBLE action (pressing a sealed pump), and it "magically"
VANISHES mid-scene. A system prompt asks Veo not to do these; it does them anyway.

So we inspect the actual clip: extract a few frames, hand them (plus the REAL product photo
as the reference) to a vision model, and get a structured verdict. On fail the compositor
regenerates the scene once, then falls back to a FAITHFUL real-photo KenBurns pass — so the
worst case still shows the true product instead of a hallucinated one.

Degrades: no caller -> a permissive pass with checked=False (we can't gate without vision).
Never raises."""
from __future__ import annotations

import logging
import subprocess
from pathlib import Path
from typing import Any, Optional

from pydantic import BaseModel

logger = logging.getLogger(__name__)


class SceneQAVerdict(BaseModel):
    product_faithful: bool = True     # same product as the reference (shape / cap / colour / label)
    product_persists: bool = True     # present in every frame — does NOT vanish / melt / morph away
    action_plausible: bool = True     # no physically impossible action (a sealed pump pressed, etc.)
    # MOTION-QA extension (owner directive 2026-07-12 — service-brand reels rendered visually
    # UNGATED; the NTI 'old school' hallucination shipped). ADDITIVE fields, permissive
    # defaults so pre-extension callers/tests stay valid:
    faces_intact_across_motion: bool = True   # no warped/melted faces, no identity morphing
    no_morphing_artifacts: bool = True        # limbs/objects keep structure across frames
    no_junk_generated_text: bool = True       # no garbled pseudo-text rendered INTO the scene
    no_third_party_marks: bool = True         # P-23: no competitor logo/trademark/branded product in frame
    ad_grade: bool = True                     # a professional brand would actually run this
    setting_faithful: bool = True             # the generated WORLD stays true to the seed photo
    # TRI-STATE (P6, 2026-07-15): True = PASS, False = FAIL, None = NOT_CHECKED.
    # It used to default to True, so a verdict could read `overall_pass=True` next to
    # `reason='scene QA call failed'` — "cannot verify" spelled as "verified". None is falsy, so
    # `if v.overall_pass` now fails CLOSED; a caller that ships anyway must say so explicitly.
    overall_pass: Optional[bool] = None
    reason: str = ""
    checked: bool = False             # True only when a vision model actually inspected the clip


class _QAResponse(BaseModel):
    product_faithful: bool
    product_persists: bool
    action_plausible: bool
    faces_intact_across_motion: bool = True   # additive (permissive default until calibrated)
    no_morphing_artifacts: bool = True
    no_junk_generated_text: bool = True
    no_third_party_marks: bool = True         # P-23 (additive, permissive default)
    ad_grade: bool = True
    setting_faithful: bool = True
    overall_pass: bool
    reason: str


def _extract_frames(clip_path: "str | Path", n: int = 3) -> list[bytes]:
    """Grab `n` evenly-spaced JPEG frames from the clip (start / middle / end) so the model
    can see the product's whole trajectory — a vanish shows as present-then-gone. [] on any
    failure (missing ffmpeg / unreadable clip)."""
    try:
        import imageio_ffmpeg
        ff = imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return []
    p = Path(clip_path)
    if not p.is_file() or p.stat().st_size == 0:
        return []
    # probe duration; fall back to sampling by frame if unknown
    fracs = [0.1, 0.5, 0.9][:max(1, n)]
    out: list[bytes] = []
    import tempfile
    for frac in fracs:
        try:
            with tempfile.TemporaryDirectory() as td:
                dst = Path(td) / "f.jpg"
                # -ss before -i is fast; use a fraction of a nominal 4s clip if seek overshoots,
                # ffmpeg clamps to the last frame, which is exactly what we want for the tail.
                subprocess.run(
                    [ff, "-y", "-ss", f"{frac * 4.0:.2f}", "-i", str(p),
                     "-frames:v", "1", "-q:v", "4", str(dst)],
                    capture_output=True, timeout=30,
                )
                if dst.is_file() and dst.stat().st_size > 0:
                    out.append(dst.read_bytes())
        except Exception:
            continue
    return out


def check_scene(
    clip_path: "str | Path",
    *,
    product_hint: Optional[str] = None,
    reference_image: Optional[bytes] = None,
    caller: Any = None,
    frames: int = 3,
    log=logger.info,
) -> SceneQAVerdict:
    """Inspect a generated scene clip. Returns a verdict; checked=False (permissive pass) when
    no caller / no readable frames — never blocks a render on infrastructure gaps."""
    if caller is None:
        return SceneQAVerdict(reason="no vision caller: scene QA skipped", checked=False)
    imgs = _extract_frames(clip_path, n=frames)
    if not imgs:
        return SceneQAVerdict(reason="could not read clip frames", checked=False)

    images: list[tuple[bytes, str]] = []
    ref_note = ""
    if reference_image:
        images.append((reference_image, "image/jpeg"))
        ref_note = ("The FIRST image is the REAL product photo (the reference). The remaining "
                    "images are frames from the generated clip, in time order.\n")
    else:
        ref_note = "The images are frames from the generated clip, in time order.\n"
    images += [(f, "image/jpeg") for f in imgs]

    prod = (product_hint or "the brand's product").strip()
    system = (
        "You are a STRICT quality reviewer for a short product ad clip. " + ref_note +
        f"The product being advertised is: {prod}. Judge ONLY these, and be strict:\n"
        "- product_faithful: do the clip frames show the SAME product as the reference — same "
        "shape, cap/pump, colour and label? false if it is redrawn into a different product or a "
        "garbled//invented label replaces the real one.\n"
        "- product_persists: is the product clearly visible in EVERY clip frame and roughly the "
        "same object throughout? false if it disappears, dissolves, melts or morphs away by a "
        "later frame (the 'it magically vanishes' failure).\n"
        "- action_plausible: is every depicted action physically possible in reality? false for "
        "an IMPOSSIBLE action — a sealed/closed pump being pressed and spraying, pouring from a "
        "closed bottle, liquid appearing with no source, or the product changing state by magic.\n"
        "- overall_pass = product_faithful AND product_persists AND action_plausible.\n"
        # ADDITIVE motion-QA criteria (2026-07-12) — appended after the measurement-locked
        # wording above; the compound gate is re-computed in CODE below regardless.
        "- faces_intact_across_motion: every human face stays anatomically intact and the SAME "
        "person across the frames — false for warped/melted features, extra or missing parts, "
        "or an identity that morphs between frames.\n"
        "- no_morphing_artifacts: bodies and objects keep their structure across frames — false "
        "when limbs/objects blend into each other, duplicate, or dissolve.\n"
        "- no_junk_generated_text: the scene itself contains NO garbled pseudo-text or gibberish "
        "lettering (on signs, screens, packaging, walls). Clean DESIGNED caption overlays are "
        "allowed — judge only text rendered INTO the scene.\n"
        "- no_third_party_marks: the frame shows NO third-party brand's logo, trademark or clearly "
        "branded product (a competitor's swoosh, an Apple/Nike/Coca-Cola mark, a recognisable "
        "rival product). false when any brand OTHER than the advertised one is visibly marked. "
        "Only the advertised brand's OWN mark is allowed.\n"
        "- ad_grade: would a professional brand actually run footage of this visual quality — a "
        "coherent, believable, real-looking setting for this brand, no uncanny artifacts, no "
        "decayed/implausible environment presented as the brand's real premises? Be strict.\n"
        "- setting_faithful: when the reference photo shows a PLACE (premises, interior, "
        "storefront) rather than a product: does the generated environment stay true to that "
        "real place — its era, upkeep and character? false when a modern, well-kept real place "
        "is rendered old/decayed/ruined, or the environment is a DIFFERENT kind of place than "
        "the reference (the owner's caught failure: her institute drawn as an ancient school). "
        "true when there is no reference or it shows a product.\n"
        "- reason = one short sentence naming the WORST problem (or 'clean')."
    )
    user = "Review the clip against the reference and return the structured verdict."
    try:
        resp, _u = caller(system, user, _QAResponse, group_name="reel_scene_qa", images=images)
    except Exception as exc:  # noqa: BLE001
        return SceneQAVerdict(reason=f"scene QA call failed ({type(exc).__name__})", checked=False)

    v = SceneQAVerdict(
        product_faithful=bool(resp.product_faithful),
        product_persists=bool(resp.product_persists),
        action_plausible=bool(resp.action_plausible),
        faces_intact_across_motion=bool(resp.faces_intact_across_motion),
        no_morphing_artifacts=bool(resp.no_morphing_artifacts),
        no_junk_generated_text=bool(resp.no_junk_generated_text),
        no_third_party_marks=bool(resp.no_third_party_marks),
        ad_grade=bool(resp.ad_grade),
        setting_faithful=bool(resp.setting_faithful),
        # COMPOUND GATE COMPUTED IN CODE (owner directive: the model never self-passes) —
        # the conjunction of every criterion AND the model's own overall verdict.
        overall_pass=bool(resp.overall_pass) and bool(resp.product_faithful)
        and bool(resp.product_persists) and bool(resp.action_plausible)
        and bool(resp.faces_intact_across_motion) and bool(resp.no_morphing_artifacts)
        and bool(resp.no_junk_generated_text) and bool(resp.no_third_party_marks)
        and bool(resp.ad_grade) and bool(resp.setting_faithful),
        reason=str(resp.reason or "")[:200],
        checked=True,
    )
    return v


# ---------------------------------------------------------------------------------
# STAGE A — SEED GATE (owner directive 2026-07-12): judge a GENERATED seed still
# BEFORE any Veo spend — regenerating an image costs cents, animating a bad seed
# costs dollars and ships the first-frame failure class the owner caught live.
# Generalizes the poster art-critic criteria (anatomy/lighting/composition/score);
# real-photo seeds don't pass through here (they are real — the director's image
# bans + motion-QA setting_faithful cover them).
# ---------------------------------------------------------------------------------

class SeedVerdict(BaseModel):
    anatomy_ok: bool = True           # hands/faces/bodies anatomically correct
    lighting_real: bool = True        # believable, photographic light — not renderish glow
    composition_clean: bool = True    # one focal point, no clutter/garbled elements
    ad_grade: bool = True             # a professional brand would run this frame
    no_text: bool = True              # NO readable/pseudo text, signage, labels, logos
    score: int = 7                    # art-director score 1..10
    # TRI-STATE (P6): True = PASS, False = FAIL, None = NOT_CHECKED. This gate guards a PAID Veo
    # render — an unjudged seed must never animate, so NOT_CHECKED cannot read as a pass.
    overall_pass: Optional[bool] = None
    reason: str = ""
    checked: bool = False


class _SeedResponse(BaseModel):
    anatomy_ok: bool
    lighting_real: bool
    composition_clean: bool
    ad_grade: bool = True
    no_text: bool = True      # calibration finding 2026-07-12: text in a seed = Veo garbles it
    score: int = 7
    reason: str = ""


def check_seed_frame(image: "str | Path | bytes", *, caller: Any = None,
                     brand_hint: str = "", log=logger.info) -> SeedVerdict:
    """One generated still -> pass/fail BEFORE Veo. Degrades permissive (checked=False)
    with no caller / unreadable image / call failure — the gate never blocks a render
    by itself; it only stops KNOWN-bad seeds from being animated."""
    if caller is None:
        return SeedVerdict(reason="no vision caller: seed gate skipped", checked=False)
    try:
        data = image if isinstance(image, bytes) else Path(image).read_bytes()
    except Exception:  # noqa: BLE001
        return SeedVerdict(reason="could not read the seed image", checked=False)
    hint = f" The brand context: {brand_hint.strip()}." if brand_hint.strip() else ""
    system = (
        "You are a WORLD-CLASS advertising ART DIRECTOR reviewing ONE frame that is about to "
        "be animated into a brand video. Your bar: would a top brand run this frame?" + hint +
        " Judge strictly:\n"
        "- anatomy_ok: hands, faces and bodies are anatomically correct — false for extra/"
        "fused fingers, warped faces, impossible limbs.\n"
        "- lighting_real: photographic, believable light — false for a synthetic render glow "
        "or physically impossible lighting.\n"
        "- composition_clean: ONE clear focal point, no clutter, no half-formed or garbled "
        "elements.\n"
        "- ad_grade: overall, professional-campaign quality for this brand.\n"
        # CALIBRATION FINDING (NTI run 2026-07-12): 4/5 motion-QA failures were garbled text
        # Veo drew over text-bearing surfaces — kill those seeds HERE, at cents, not there.
        "- no_text: the frame contains NO readable or pseudo-text of any kind — no signage, "
        "screen text, labels, posters or logos; text-bearing surfaces must be abstract, "
        "blurred or out of frame (the motion model garbles any text it inherits).\n"
        "- score: 1..10 as an art director.\n"
        "- reason: one short sentence naming the worst problem (or 'clean')."
    )
    try:
        resp, _u = caller(system, "Review this seed frame and return the structured verdict.",
                          _SeedResponse, group_name="reel_seed_gate",
                          images=[(data, "image/png")])
    except Exception as exc:  # noqa: BLE001
        return SeedVerdict(reason=f"seed gate call failed ({type(exc).__name__})", checked=False)
    score = int(resp.score or 0)
    # the compound gate is computed in CODE (same law as the motion QA): every criterion
    # AND the score threshold — the model never self-passes.
    ok = (bool(resp.anatomy_ok) and bool(resp.lighting_real)
          and bool(resp.composition_clean) and bool(resp.ad_grade)
          and bool(resp.no_text) and score >= 6)
    return SeedVerdict(anatomy_ok=bool(resp.anatomy_ok), lighting_real=bool(resp.lighting_real),
                       composition_clean=bool(resp.composition_clean),
                       ad_grade=bool(resp.ad_grade), no_text=bool(resp.no_text),
                       score=score, overall_pass=ok,
                       reason=str(resp.reason or "")[:200], checked=True)


def seed_may_animate(verdict: Any) -> bool:
    """The ONE fail-CLOSED rule for spending a (paid) Veo render on a seed/clip: only an explicit
    PASS proceeds. A NOT_CHECKED verdict (no caller, unreadable frames, or a call timeout —
    `overall_pass=None`) and an explicit FAIL (`overall_pass=False`) both return False, so an
    UNVETTED seed never costs Veo money (P-21 / P6 #7). This is the law seed_gen.py:172 already
    applies inline; generate.py and compositor.py route through THIS predicate so the three
    seed/scene gates can never drift back into a fail-OPEN reading (`if v.checked and not
    v.overall_pass`, which skipped a NOT_CHECKED verdict and animated it). `None` -> False."""
    return getattr(verdict, "overall_pass", None) is True
