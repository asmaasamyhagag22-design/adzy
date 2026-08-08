"""ffmpeg compositor: storyboard -> final .mp4.

Text is rendered by Chromium (reel.textlayer) as transparent PNGs — the browser
shapes Arabic correctly (connected, RTL), which the bundled ffmpeg/libass does
NOT. So ffmpeg's only text job is to OVERLAY those PNGs (with an alpha fade) on
each scene clip; it never shapes text itself.

Stages (kept separate so a failure is legible):
  1. Render one transparent text PNG per scene (single browser session).
  2. Per scene: the provider generates a text-free clip; normalize to a uniform
     WxH / fps / timebase / pix_fmt and overlay the scene's text PNG (alpha fade).
  3. Concat the scene clips (hard cuts; identical params -> stream copy).
  4. Mux optional music with fades; finalize H.264 + faststart.

Hard cuts + per-scene text fades (robust) rather than xfade crossfades (a future
polish item).
"""
from __future__ import annotations

import os as _os
import re as _re
import sys
import tempfile
from pathlib import Path
from typing import Any, Optional

from .ffmpeg_tools import ffmpeg_exe, run_ffmpeg
from .motion_contract import FRAME_IS_UNPRINTED
from .retry_policy import should_retry
from .schemas import REEL_H, REEL_W, ReelRenderResult, Storyboard
from .textlayer import render_text_sequences
from .video_provider import KenBurnsProvider, VideoProvider

# C1: a per-run Veo budget refusal must be distinguishable from a provider failure. Imported
# defensively so the compositor keeps working if the spend guard is ever absent — in that case
# SpendRefused simply cannot be raised, and the generic handler below still catches everything.
try:
    from vertex_spend import SpendRefused as _SpendRefused
except Exception:  # noqa: BLE001 — pragma: no cover
    class _SpendRefused(Exception):  # type: ignore[no-redef]
        """Placeholder: never raised when vertex_spend is unavailable."""


# Words a QA verdict uses when the defect is LETTERING the generator invented. Matched on the
# verdict's own free-form prose because that is what `check_scene` returns — it was never given a
# taxonomy, and inventing one here would be a second vocabulary to keep in step with the first.
_TEXT_FAILURE_WORDS = ("garbled", "junk text", "unreadable text", "malformed", "illegible",
                       "brand name", "label", "lettering", "writing")


# A third-party mark is a BRAND-SAFETY defect (P-23), not a cosmetic one, and it fails on its own
# vocabulary — a verdict says "logo", "trademark" or "competitor", none of which are lettering
# words. Without a clause of its own the retry policy (Axis 3) correctly refuses to re-buy the shot
# — there would be nothing new to ask — and the clip ships WITH the mark on it. So it gets one.
_MARK_FAILURE_WORDS = ("third-party", "third party", "trademark", "competitor", "logo",
                       "branded badge", "brand mark")


def _escalate(reason: str) -> str:
    """The extra clause a retry adds when the last attempt broke a ban the prompt already carried.

    Not louder — DIFFERENT. `_NO_TEXT` already forbids readable text and the generator did it
    anyway, so the retry removes the surface the letters kept landing on. The product still reaches
    the film: the insert beat composites the brand's real photograph, which cannot garble.

    Returns "" when the failure is not a ban violation — a morphing hand is not answered by
    stripping every package from the shot, and Axis 3 reads that "" as "there is nothing new to
    ask" (see `reel/retry_policy.py`).
    """
    low = str(reason or "").lower()
    if any(w in low for w in _TEXT_FAILURE_WORDS):
        return (" The previous attempt invented unreadable lettering. Remove every branded surface "
                "from this shot: no shopping bags, cartons, tubes, jars, bottles or packaging of any "
                "kind in frame, and no printed matter. Show only the person, the setting and their "
                "hands; the product is added separately and must not appear here.")
    if any(w in low for w in _MARK_FAILURE_WORDS):
        return (" The previous attempt rendered a company's mark. Remove every logo, badge, "
                "emblem and branded garment or object from the frame — no marks on clothing, "
                "cups, bags, signage, screens or vehicles. Plain, unbranded surfaces only.")
    return ""


# How long one shot blooms into the next. Short enough that a 4-second beat still reads as a beat,
# long enough to hide the seam a hard cut exposes.
_SWEEP_S = 0.35


def _crossfade(clips: list, out: Path, *, fps: int, dur: float) -> bool:
    """Chain the clips with a white light sweep. True on success, False to fall back to concat.

    Each `xfade` OVERLAPS its two inputs by `dur`, so the film shortens by `dur` per seam. The
    offsets below account for that — without it every later transition fires early and the last
    shot is cut off, which is the classic way this filter is got wrong.
    """
    if len(clips) < 2:
        return False
    try:
        import subprocess
        durs = []
        for c in clips:
            r = subprocess.run([ffmpeg_exe(), "-i", str(c)], capture_output=True, text=True).stderr
            m = _re.search(r"Duration: \d+:(\d+):([\d.]+)", r)
            if not m:
                return False
            durs.append(int(m.group(1)) * 60 + float(m.group(2)))
        args: list = []
        for c in clips:
            args += ["-i", str(c)]
        chain, prev, acc = [], "0:v", 0.0
        for i in range(1, len(clips)):
            acc += durs[i - 1] - dur
            lbl = f"x{i}"
            chain.append(f"[{prev}][{i}:v]xfade=transition=fadewhite:"
                         f"duration={dur:.2f}:offset={acc:.3f}[{lbl}]")
            prev = lbl
        run_ffmpeg(args + ["-filter_complex", ";".join(chain), "-map", f"[{prev}]",
                           "-r", str(fps), "-c:v", "libx264", "-pix_fmt", "yuv420p",
                           "-profile:v", "high", "-preset", "medium", "-crf", "20", str(out)])
        return out.is_file() and out.stat().st_size > 0
    except Exception as exc:  # noqa: BLE001 — a finish must never cost the film
        print(f"[reel] light-sweep transitions unavailable ({type(exc).__name__}: {exc}); "
              "falling back to hard cuts", file=sys.stderr)
        return False


def _even(n: float) -> int:
    return int(n) // 2 * 2          # libx264 yuv420p needs even dimensions


def render_reel(
    storyboard: Storyboard,
    *,
    provider: VideoProvider,
    out_path: str | Path,
    fps: int = 30,
    scale: float = 1.0,
    music_path: Optional[str | Path] = None,
    voiceover_path: Optional[str | Path] = None,
    include_logo: bool = True,
    fallback_provider: Optional[VideoProvider] = None,
    qa_caller: Any = None,
    qa_product_hint: Optional[str] = None,
    qa_reference_image: Optional[bytes] = None,
    grade: str = "",
    music_tail_s: float = 0.0,
    identity_refs: Optional[list] = None,
) -> ReelRenderResult:
    """Render the storyboard to `out_path`. `scale` (<1) renders a faster, smaller
    preview; the text PNGs are rendered at the same scaled size so overlay is 1:1.

    `fallback_provider` saves the reel when the primary provider fails on a scene
    (e.g. Veo 404 on an unprovisioned model, 429/quota, or a timeout): instead of
    crashing the whole render, that ONE scene is regenerated by the fallback.
    Default = a KenBurns pass over the brand's REAL scraped photos (degrade to the
    actual place, not a blank gradient), which itself degrades to a palette gradient
    when there are no usable photos. So a render NEVER hard-fails on a provider error.

    `qa_caller` (a multimodal caller) turns on the SCENE QA gate: each generated clip is
    inspected against `qa_reference_image` (the real product photo) + `qa_product_hint`
    (its name); a clip where the product is unfaithful, vanishes, or does an impossible
    action is regenerated ONCE, then falls back to the faithful real-photo KenBurns — so a
    Veo hallucination never ships (the owner: 'it presses a sealed pump and then vanishes').
    QA is off (identical old behaviour) when qa_caller is None.

    `grade` is the brand's colour grade as an ffmpeg fragment (`reel.grade.grade_filter`), applied
    to the FOOTAGE of every generated scene. "" means no grade — the exact behaviour every render
    had before 2026-08-01, when `reel/grade.py` was complete, tested and imported by nothing.
    """
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    W, H = _even(REEL_W * scale), _even(REEL_H * scale)
    fade = 0.4
    # CALIBRATION FINDING (2026-07-12): Veo draws garbled text onto signs/equipment and
    # the motion QA rightly kills the clip — a wasted render. Ban it at the prompt, for
    # every scene, retry and fallback — including OLD plans and HITL-edited ones.
    # PHRASED AS A DESCRIPTION, NOT AS A LIST OF NEGATIONS.
    #
    # Google's own Veo guide is explicit: "describe what you wish to exclude" AFFIRMATIVELY —
    # their example is "a desolate landscape with no buildings or roads" rather than "no man-made
    # structures". This clause was five stacked negations ("no signage, screen text, labels,
    # posters or logos"), which is the exact form the guide calls weaker, and `_motion_directive`
    # in `reel/creative.py` states the same ban a second time in the same prompt.
    #
    # It is the most plausible cause of the measured failure that has cost the most: three shots
    # of the Al Fouad reel all failed `no_junk_generated_text`, all retried, all failed the same
    # way — $12.80 for one outcome. The model was not ignoring the ban so much as being handed it
    # in the form the vendor says lands worst.
    _NO_TEXT = FRAME_IS_UNPRINTED
    fallback_used = (provider.name == "stub")
    budget_stopped = False
    budget_note = ""
    scene_qa: list = []   # per-scene motion-QA verdicts (exported on the audit trail)
    fb = fallback_provider or KenBurnsProvider(
        storyboard.content_images, fallback_palette=storyboard.palette_hex or None
    )

    with tempfile.TemporaryDirectory(prefix="reel_") as tmp:
        tmpd = Path(tmp)

        # 1) KINETIC text layer — one PNG SEQUENCE per scene (Chromium shapes Arabic
        # correctly AND drives the per-element entrance: headline pop -> sublines -> CTA).
        text_seqs = render_text_sequences(
            storyboard, width=W, height=H, fps=fps, out_dir=tmpd, include_logo=include_logo,
        )

        # 2) per scene: text-free clip -> normalize + overlay text (alpha fade).
        # GROUNDING: every scene is SEEDED from its own REAL scraped photo
        # (scene.seed_image_url, a distinct content photo). KenBurns animates it;
        # Veo 3.1 brings it to life. So the whole reel comes from the real place,
        # not one repeated frame. Falls back to the hero only if a scene has no seed.
        norm_clips: list[Path] = []
        # CONTINUITY (owner, 2026-07-28, asking what makes a coherent scenario). Scenes were
        # seeded independently, so scene 1 and scene 3 were two takes that resembled each other
        # rather than one continuous moment — measured: `grep -rn last_frame reel/` returned zero.
        # The previous clip's final frame can now seed the next scene, bounded and logged; see
        # `reel/continuity.py` for what it refuses to override and why.
        prev_tail: Optional[str] = None
        prev_scene = None
        chain_len = 0
        # The film's look, taken from its opening shot and never updated — see the anchor note in
        # the loop. `capped_shots` is the honesty signal: if most shots need more correction than
        # the caps allow, the LOOK LOCK failed upstream and the grade is papering over a generation
        # problem rather than finishing a film.
        anchor_frame: Optional[str] = None
        capped_shots = 0
        def _reserve_for(idx: int) -> float:
            """What the scenes AFTER `idx` still owe in base clips.

            MEASURED 2026-08-06 on the live NTI reel: scene 0 failed QA twice, bought three clips,
            hit the $12 ceiling, and scenes 1, 2 AND 3 were then refused their FIRST clip and fell
            back to Ken Burns. Three quarters of a four-scene film became panned stills because one
            shot was re-rolled. The ceiling was right; spending it first-come-first-served was not.
            Holding this back means a re-roll can only ever use money nobody else has claimed.
            """
            try:
                from vertex_spend import est_video_cost_usd

                from .video_provider import _VEO_DURATIONS
            except Exception:  # noqa: BLE001 — no guard available means nothing to reserve
                return 0.0
            total = 0.0
            for s in storyboard.scenes[idx + 1:]:
                if s.insert_product_urls:
                    continue                      # rendered here, costs no Veo seconds
                d = float(s.duration_s or 0.0)
                total += est_video_cost_usd(
                    min([v for v in _VEO_DURATIONS if v >= d] or [max(_VEO_DURATIONS)]))
            return round(total, 4)

        for i, scene in enumerate(storyboard.scenes):
            # Set BEFORE any generate for this scene — the first attempt and the retry both have to
            # leave the later scenes their footage.
            try:
                provider.reserve_usd = _reserve_for(i)     # type: ignore[attr-defined]
                # WHO THE FILM IS ABOUT, handed to every shot. One protagonist for the whole reel
                # is a run-level fact, not a per-scene one, so it is set here rather than threaded
                # through `gen_kwargs` — and a provider that has no use for it simply ignores the
                # attribute (`reel/video_provider.py:_reference_images`).
                provider.identity_refs = list(identity_refs or [])   # type: ignore[attr-defined]
            except Exception:  # noqa: BLE001 — a provider that will not hold it simply does not
                pass
            own_seed = scene.seed_image_url or (
                storyboard.reference_image_url if scene.kind in ("intro", "outro") else None
            )
            from .continuity import decide_seed, tail_frame
            _cont = decide_seed(scene, prev_tail, own_seed=own_seed, chain_len=chain_len,
                                previous_scene=prev_scene)
            ref = _cont.seed
            chain_len = chain_len + 1 if _cont.chained else 0
            if _cont.chained:
                print(f"[reel] scene {i} seed: {_cont.reason}", file=sys.stderr)
            gen_kwargs = dict(
                out_path=tmpd / f"raw{i}.mp4",
                duration_s=scene.duration_s,
                width=W, height=H,
                palette=storyboard.palette_hex,
                reference_image=ref,
            )
            # THE PRODUCT INSERT — rendered here, not generated anywhere. See `reel/insert_shot.py`
            # for the measured reason: a video model cannot hold a legible pack, and pasting one
            # onto moving footage reads as a paste. The beat cuts to the product alone, so it has
            # the previous shot's own last frame behind it (defocused) and the brand's real
            # photograph in front. `None` means the insert could not be built — the scene then
            # generates exactly as it always did, because a beat that says nothing is worse than
            # a generated one.
            insert = None
            if scene.insert_product_urls:
                from .insert_shot import insert_clip
                insert = insert_clip(
                    prev_tail or ref or storyboard.reference_image_url,
                    list(scene.insert_product_urls),
                    out_path=tmpd / f"raw{i}.mp4",
                    duration_s=scene.duration_s, width=W, height=H, fps=fps,
                    work_dir=tmpd / f"insert{i}",
                )
                print(f"[reel] scene {i} insert: "
                      + ("rendered from the real packshot (no model, no spend)" if insert
                         else "could not be built — generating this scene instead"),
                      file=sys.stderr)
            try:
                if insert:
                    raw = Path(insert)
                    # NOT a fallback and NOT unjudged-by-accident: nothing was generated, so there
                    # is no hallucination for the scene gate to look for. Saying so on the audit
                    # trail keeps "nothing to check" from reading as "checked and fine".
                    scene_qa.append({"scene": i, "checked": False, "overall_pass": None,
                                     "reason": "product insert — rendered from the brand's real "
                                               "photograph, not generated"})
                else:
                    raw = provider.generate(scene.visual_prompt + _NO_TEXT, **gen_kwargs)
            except _SpendRefused as exc:                   # C1 — a DECISION, not a failure
                # The run hit its Veo budget. Reporting this as "provider failed" would be a lie
                # (the provider is fine) and would hide the one thing a human can act on. The reel
                # still completes on the fallback — a budget stop must not kill the deliverable —
                # but the result SAYS it stopped, so HITL can offer "approve an override or
                # shorten the reel" instead of showing an infra error.
                print(f"[reel][budget] scene {i}: per-run Veo budget reached — remaining scenes "
                      f"use '{fb.name}'. {exc}", file=sys.stderr)
                budget_stopped = True
                budget_note = str(exc)
                fallback_used = True
                raw = fb.generate(scene.visual_prompt + _NO_TEXT, **gen_kwargs)
            except Exception as exc:                       # noqa: BLE001
                # A provider failure on ONE scene must not kill the whole reel.
                # Regenerate just this scene with the fallback (real-photo KenBurns
                # -> palette gradient). Observable in the log so a degraded reel is
                # never silently passed off as a full provider render.
                print(
                    f"[reel] scene {i} provider '{provider.name}' failed "
                    f"({type(exc).__name__}: {exc}); falling back to '{fb.name}'",
                    file=sys.stderr,
                )
                fallback_used = True
                raw = fb.generate(scene.visual_prompt + _NO_TEXT, **gen_kwargs)
            else:
                # SCENE QA GATE (opt-in via qa_caller): reject a Veo clip where the product is
                # unfaithful / vanishes / does an impossible action — or (motion-QA extension,
                # 2026-07-12) faces warp, objects morph, junk pseudo-text appears, the footage
                # isn't ad-grade, or the WORLD drifts from the real seed (calibrated: the
                # owner's 'old school' scene fails against its real seed) — regenerate ONCE,
                # then fall back to the FAITHFUL real-photo KenBurns so a hallucination never
                # ships. Service brands too: with no product reference, the scene's OWN
                # real-photo seed is the reference.
                # PRODUCT OVERLAY (overhaul Phase D), now BEFORE the judge. The composite
                # guard deliberately leaves the product's place empty in the generated clip, so
                # judging the raw plate could only ever refuse it — MEASURED: "the advertised
                # product is completely absent from all clip frames". QA must see what ships.
                def _paste_product(clip_path, tag=""):
                    pa = getattr(scene, "product_asset_url", None)
                    if not pa:
                        return clip_path
                    asset_path = None
                    if str(pa).startswith(("http://", "https://")):
                        try:
                            from reel.video_provider import _load_reference_image
                            loaded = _load_reference_image(str(pa))
                            if loaded:
                                asset_path = tmpd / f"asset{i}.jpg"
                                asset_path.write_bytes(loaded[0])
                        except Exception:  # noqa: BLE001
                            asset_path = None
                    elif Path(str(pa)).is_file():
                        asset_path = Path(str(pa))
                    if not asset_path:
                        return clip_path
                    from reel.product_overlay import composite_product_screen
                    return composite_product_screen(
                        clip_path, asset_path, tmpd / f"prod{i}{tag}.mp4",
                        placement=getattr(scene, "product_placement", "") or "inset",
                        fps=fps, width=W, height=H)

                # An insert has no generated plate to overlay onto and nothing to judge: the
                # product is already in the frame, at full fidelity, because it IS the photograph.
                raw = raw if insert else _paste_product(raw)
                if qa_caller is not None and not insert:
                    from .scene_qa import check_scene, seed_may_animate
                    _v_final = None
                    qa_ref = qa_reference_image
                    # owner ruling 2026-07-12: generated seeds — the judge compares the
                    # clip against the brand's REAL photo, not the generated still.
                    ref_for_qa = getattr(scene, "place_ref_url", None) or ref
                    if qa_ref is None and ref_for_qa:
                        try:
                            from .video_provider import _load_reference_image
                            loaded = _load_reference_image(ref_for_qa)
                            qa_ref = loaded[0] if loaded else None
                        except Exception:  # noqa: BLE001 — no seed bytes: judge frames alone
                            qa_ref = None
                    # ONLY A SHOT THAT CARRIES THE PRODUCT IS JUDGED ON THE PRODUCT.
                    #
                    # `qa_product_hint` was passed for EVERY scene, so a lifestyle shot — one the
                    # prompt explicitly forbids from showing any packaging — was failed for the
                    # absence of the thing we banned. MEASURED 2026-08-02, twice in one reel:
                    # «The advertised product is completely absent from the generated clip». The
                    # gate was asking a question the brief had already answered, and each answer
                    # cost a $2.40 regeneration that could never satisfy it.
                    #
                    # Under Axis 1 no generated scene carries the product at all — it arrives
                    # through the composited insert — so the hint is dropped for generated shots
                    # and the gate judges what those shots ARE: motion, faces, artefacts, text.
                    _scene_hint = qa_product_hint if getattr(scene, "hero_product", False) else None
                    v = check_scene(raw, product_hint=_scene_hint,
                                    reference_image=qa_ref, caller=qa_caller)
                    _v_final = v
                    # FAIL-CLOSED (P-22): regenerate/fall back on anything that is NOT an explicit
                    # PASS. This was `if v.checked and not v.overall_pass`, which shipped a
                    # NOT_CHECKED clip (unreadable frames / a QA-call timeout -> overall_pass=None)
                    # straight through — the same fail-open hole seed_gen.py:172 already closed.
                    # AXIS 3 — THE BLIND RETRY IS OVER (owner: «إلغاء الإعادة العمياء»).
                    #
                    # `seed_may_animate` is the right law for deciding whether to SPEND on a seed:
                    # fail-closed, before any money moves. It is the wrong question HERE, because
                    # the money for this clip is already spent and the only thing left to decide is
                    # whether spending AGAIN can change anything. Three times it provably cannot —
                    # nobody looked, nothing new to ask, or the complaint is taste — and each of
                    # those cost $2.40 for an outcome identical to not having tried.
                    # `reel/retry_policy.py` holds the table and the measurements.
                    _retry, _why = should_retry(
                        v, escalation=_escalate(v.reason),
                        scene_has_product=bool(getattr(scene, "hero_product", False)))
                    _declined = ""
                    if not seed_may_animate(v) and not _retry:
                        _declined = _why
                        print(f"[reel] scene {i} QA not a clean PASS ({v.reason}); NOT regenerating "
                              f"— {_why}", file=sys.stderr)
                    if _retry:
                        print(f"[reel] scene {i} QA not a clean PASS ({v.reason}); regenerating once "
                              f"— {_why}", file=sys.stderr)
                        good = False
                        # `should_retry` above ALREADY refused every case where the prompt would
                        # be identical, so inside this branch `_escalate` is non-empty by
                        # construction. A second copy of that decision here would be two rules for
                        # one question — the shape that lets them drift apart.
                        try:
                            # THE RETRY LEARNS FROM THE REASON IT FIRED ON.
                            #
                            # It used to re-send the identical prompt. A generator that just wrote
                            # junk on a bag, asked the same question again, writes junk on the bag
                            # again — and the second clip is bought at full price. MEASURED
                            # 2026-08-02 on the Al Fouad reel: all three shots failed on
                            # `no_junk_generated_text`, all three retried on the same prompt, all
                            # three failed the same way. Six Veo purchases, $12.80, one outcome.
                            #
                            # `_NO_TEXT` already forbids readable text and Veo did it anyway, so
                            # repeating it louder is not the fix — REMOVING THE SURFACE is. The
                            # product reaches the film through the insert, which is the brand's
                            # real photograph and cannot garble.
                            _retry_prompt = scene.visual_prompt + _NO_TEXT + _escalate(v.reason)
                            raw = _paste_product(
                                provider.generate(_retry_prompt, **gen_kwargs),
                                tag="r")
                            v2 = check_scene(raw, product_hint=_scene_hint,
                                             reference_image=qa_ref, caller=qa_caller)
                            _v_final = v2
                            # FAIL-CLOSED (P-22): the retry clip ships ONLY on an explicit PASS.
                            # This scene ALREADY failed QA once, so an unjudged retry (overall_pass
                            # in {None}) must lose to the FREE faithful KenBurns fallback below —
                            # never prefer an unvetted Veo clip over the grounded real-photo pass.
                            good = seed_may_animate(v2)
                        except Exception:  # noqa: BLE001 — a failed retry drops to the fallback
                            good = False
                        if not good:
                            # MOTION OVER THE STILL (owner, 2026-08-01: «انا مش عايزه Ken Burns
                            # في الريل»), and the reasoning is not just preference.
                            #
                            # The fallback DOES NOT CURE THE DEFECT IT FIRES ON. Measured across
                            # three live runs, the QA verdicts that trigger it are about the label
                            # MOVING — "the text on the product packaging morphs into garbled
                            # junk across the frames". Ken Burns answers that by removing the
                            # motion entirely: it trades the whole reason the clip exists for a
                            # slow zoom on a still, and the viewer gets a slideshow instead of a
                            # film. The judged clip is kept, its verdict rides the audit trail,
                            # and a human decides.
                            #
                            # A still is still the answer when there is NO clip at all — a
                            # provider exception, an empty render. That is a different failure:
                            # nothing to ship rather than something imperfect.
                            if _os.environ.get("REEL_ALLOW_KENBURNS") == "1" or raw is None:
                                print(f"[reel] scene {i} still failed QA; faithful seed-frame "
                                      "KenBurns fallback", file=sys.stderr)
                                fallback_used = True
                                raw = _paste_product(
                                    fb.generate(scene.visual_prompt + _NO_TEXT, **gen_kwargs),
                                    tag="f")
                            else:
                                print(f"[reel] scene {i} failed QA twice — KEEPING THE MOVING "
                                      f"CLIP ({v2.reason if _v_final else 'unjudged'}); the "
                                      "verdict ships on the audit trail",
                                      file=sys.stderr)
                    if _v_final is not None:
                        # `retry_declined` rides the audit trail so a run that DID NOT re-render
                        # says why. A saving nobody can see reads as a gate that stopped working.
                        _rec = {"scene": i, **_v_final.model_dump()}
                        if _declined:
                            _rec["retry_declined"] = _declined
                        scene_qa.append(_rec)

            # THE TAIL COMES FROM THE TEXT-FREE CLIP, deliberately. `raw` is the scene as the
            # provider rendered it and QA passed it; the captions are burnt on further down. A
            # tail taken after that would carry a headline into the next scene's generation — the
            # next clip would open with the previous scene's words baked into the world.
            prev_tail = tail_frame(raw, tmpd / f"tail{i}.png")
            prev_scene = scene

            # THE ANCHOR. Every shot is pulled toward the SAME opening frame, never toward the one
            # before it — a neighbour chain compounds, and the film walks one cut at a time to
            # somewhere nobody chose. See `reel/shot_match.py` for the measurement and the caps.
            # Measured from the UNGRADED clip, because the brand grade below is identical for every
            # scene and so cannot change which shots agree; matching in graded space would only
            # mean the match partly fights the grade.
            _match = ""
            if not insert:
                from .continuity import head_frame
                from .shot_match import match_filter, measure
                _head_png = head_frame(raw, tmpd / f"head{i}.png")
                if anchor_frame is None:
                    anchor_frame = _head_png          # scene 0 IS the film's look
                _m = measure(anchor_frame, _head_png)
                _match = match_filter(_m)
                if _m.checked and _m.capped:
                    capped_shots += 1
                if _match:
                    print(f"[reel] scene {i} matched to the anchor "
                          f"(luma {_m.src_luma:.0f}->{_m.dst_luma:.0f}"
                          f"{', capped' if _m.capped else ''})", file=sys.stderr)
                elif not _m.checked and i > 0:
                    print(f"[reel] scene {i} not matched: {_m.reason}", file=sys.stderr)

            norm = tmpd / f"norm{i}.mp4"
            d = scene.duration_s
            fout = max(0.0, d - fade)
            # The kinetic PNG SEQUENCE already carries the per-element entrance (pop /
            # slide-up / staggered fade), so the overlay no longer block-slides. We feed
            # the sequence via the image2 demuxer at `fps`, HOLD its last frame for the
            # rest of the scene (tpad clone), and keep the scene-end alpha fade-out for a
            # clean cut. The sequence starts at the scene's first frame (t=0).
            # THE EDITOR'S CHEAT SHEET (owner, 2026-07-27), applied per scene:
            #   * head trim — an AI clip warms up; cutting 0.5s opens the shot MID-action.
            #     Guarded on the measured clip length, so a clip with no slack is never trimmed
            #     into being shorter than its scene.
            #   * handheld shake — generation artefacts read as flaws on a locked-off frame and
            #     as camera motion on a moving one.
            #   * intra-scene cuts — one clip re-cut into <=1.8s shots with alternating 6%
            #     punch-ins. Same footage, so the pace costs nothing; the AUDIO track is built
            #     on the scene's full duration, which the recut preserves exactly.
            from reel.cinematic import handheld_shake_filter, head_trim_for, intra_scene_chain
            _len = 0.0          # unmeasured until proven otherwise — see the `except` below
            try:
                from reel.text_overlay import _probe_duration as _clip_len
                _len = float(_clip_len(raw) or 0.0)
                _trim = head_trim_for(clip_s=_len, scene_s=float(d))
                # A CLIP SHORTER THAN ITS SCENE IS A FROZEN TAIL, AND IT USED TO BE SILENT.
                # The scene still comes out the right LENGTH — `-t` and the text overlay see to
                # that — so nothing downstream could tell the difference. MEASURED 2026-08-01: the
                # deficit shows up as 11% identical consecutive frames in the scene's last second
                # (0% when the clip covers it), and `intra_scene_chain`'s final shot gets 40% of
                # the footage its pace was designed around. Saying it out loud is what turns that
                # from an invisible defect into a decision.
                if _len and _len < float(d) - 1.0 / float(fps):
                    print(f"[reel] scene {i} clip is {_len:.2f}s for a {float(d):.2f}s scene "
                          f"({float(d) - _len:.2f}s short) — its tail will hold a frozen frame",
                          file=sys.stderr)
                    scene_qa.append({"scene": i, "checked": False, "overall_pass": None,
                                     "reason": f"clip {_len:.2f}s < scene {float(d):.2f}s — "
                                               "frozen tail"})
            except Exception:  # noqa: BLE001 — unmeasured length means no trim, never a guess
                _trim = 0.0
            _head = (f"trim=start={_trim:.2f},setpts=PTS-STARTPTS," if _trim > 0 else "")
            # AN INSERT IS LOCKED OFF. Both of these exist to rescue GENERATED footage — the shake
            # sells generation artefacts as camera life, and the intra-cut hides that one long AI
            # take has no edit in it. Applied to a product insert they do the opposite: a real
            # packshot jittering on a defocused plate reads as a badly pasted sticker, and cutting
            # a 1.4-second beat in half destroys the only shot in the film whose job is to HOLD.
            # Every commercial shoots its pack shot on a locked head for exactly this reason.
            _shake = handheld_shake_filter(W, H) if not insert else "null"
            # THE COVERAGE THE SCENE ACTUALLY HAS, handed to the editor. `_len` is the purchased
            # clip and `_trim` the head we discard, so what reaches [bg] is the difference — and
            # it is normally LONGER than the scene, because the model sells 4/6/8s and this scene
            # may be 2.97. That surplus was being thrown away by `-t`; it is what lets the cut
            # remove time instead of running the action straight through the join.
            _intra = ("" if insert else
                      intra_scene_chain("bg", "bgc", duration_s=float(d), width=W, height=H,
                                        clip_s=max(0.0, _len - _trim)))
            _bg_label = "bgc" if _intra else "bg"
            # THE BRAND GRADE, ON THE FOOTAGE ONLY.
            #
            # Placed here — after the crop and the shake, BEFORE the text overlay at the end of
            # this chain — for two reasons. The captions carry the brand's REGISTERED colours, and
            # a grade that shifts them is moving the one thing the whole poster pipeline caps its
            # harmonisation to protect. And riding the encode that already happens below costs no
            # extra pass; a separate grading render would double the encode time per scene.
            #
            # An INSERT is skipped: it is the brand's real packshot, already light-matched to its
            # plate by `poster/product_composite._harmonize` under caps of 12% luma / 6% channel.
            # Grading it again would move a registered colour a second time, uncapped.
            _grade = "" if (insert or not str(grade or "").strip()) else str(grade).strip() + ","
            # MATCH FIRST, THEN GRADE. The match measures ungraded frames, so it belongs in
            # ungraded space; and the brand grade is the same for every scene, so applying it after
            # preserves the ordering the match just established.
            _fix = (_match + ",") if _match else ""
            fc = (
                f"[0:v]{_head}scale={W}:{H}:force_original_aspect_ratio=increase,"
                # `setsar=1` ON EVERY SCENE, and this is not belt-and-braces — it is a MEASURED
                # bug that ate the most important beat in the film.
                #
                # `intra_scene_chain` pins the SAR on each shot it emits (a fractional `scale`
                # leaves a SAR a hair off, and `concat` refuses inputs whose SARs differ). But a
                # scene SHORT enough to already be at pace gets NO chain — and therefore no
                # `setsar` — so it reached the concat declaring a different SAR from every other
                # scene. Stream-copy concat does not fail loudly on that; it misbehaves.
                #
                # MEASURED 2026-08-06 through this function with the stub provider:
                #     [4.0, 4.0, 4.0]   -> 11.93s   (fine; every scene got a chain)
                #     [4.0, 1.7]        ->  3.99s   THE 1.7s SCENE IS SIMPLY GONE
                #     [1.7, 4.0]        -> 366.78s  a 5.7-second plan, six minutes of file
                #
                # The scene this hits in production is the one that matters most: the last beat of
                # a 15-second reel is the PRODUCT INSERT at ~1.4-1.7s (`reel/insert_shot.py`), and
                # it is short by design and never chained. The film's product resolve — the shot
                # before the logo — was the one shot that could vanish.
                f"crop={W}:{H},{_shake},{_fix}{_grade}settb=AVTB,fps={fps},"
                f"setsar=1,format=yuv420p[bg];"
                + (_intra + ";" if _intra else "")
                + f"[1:v]format=rgba,fps={fps},tpad=stop_mode=clone:stop_duration={d:.2f},"
                f"settb=AVTB,fade=t=out:st={fout:.2f}:d={fade}:alpha=1[tx];"
                # NORMALISE AFTER THE OVERLAY, not only before it — the last filter in the chain
                # is what the encoder sees, and the two branches do not arrive the same way.
                #
                # A scene long enough to be re-cut goes through `concat`, which emits its OWN
                # timebase; a scene already at pace skips the chain entirely and keeps the one set
                # above. Both encode to the same codec and pixel format, so the concat DEMUXER
                # accepts them for stream copy — and then writes nonsense, because the timestamps
                # in the two files mean different things.
                #
                # MEASURED 2026-08-06 through this function (stub provider, so the only variable
                # is the graph): `[4.0, 1.7]` delivered 3.99s — the short scene simply gone — and
                # `[1.7, 4.0]` delivered 366.78s from a 5.7-second plan. The beat this destroys in
                # production is the product insert before the logo, which is short BY DESIGN.
                f"[{_bg_label}][tx]overlay=x=0:y=0:format=auto,"
                f"settb=AVTB,fps={fps},setsar=1,format=yuv420p[v]"
            )
            run_ffmpeg([
                "-i", str(raw),
                "-framerate", str(fps), "-start_number", "0", "-i", str(text_seqs[i]),
                "-filter_complex", fc, "-map", "[v]", "-t", f"{d:.2f}",
                "-c:v", "libx264", "-pix_fmt", "yuv420p",
                "-profile:v", "high", "-preset", "medium", "-crf", "20",
                str(norm),
            ])
            norm_clips.append(norm)

        # THE THIRD DRIFT DEFENCE, said out loud. A shot needing more correction than the caps
        # allow is a shot that was LIT differently, and `brand/look.py` opens by explaining that
        # no colour pass repairs that — the shadows are already in the pixels. One capped shot is
        # normal; most of them means the look lock did not reach the generator and this grade is
        # hiding a generation problem instead of finishing a film.
        _gen = sum(1 for s in storyboard.scenes if not s.insert_product_urls)
        if _gen >= 2 and capped_shots * 2 > _gen:
            print(f"[reel] {capped_shots}/{_gen} shots hit the match cap — the shots were LIT "
                  "differently; check the look lock reached the seed generator", file=sys.stderr)

        # 3) concat (identical params -> stream copy).
        listfile = tmpd / "concat.txt"
        listfile.write_text(
            "".join(f"file '{c.as_posix()}'\n" for c in norm_clips), encoding="utf-8"
        )
        bg = tmpd / "bg.mp4"
        # THE SHOTS BLEND, THEY DO NOT SNAP.
        #
        # MEASURED on the reference reel the owner supplied (a NanoCare Liposomal Iron spot,
        # 2026-08-03): ffmpeg's scene detector finds ZERO cuts in ten seconds. The shots change —
        # tired woman, product, recovered woman — but each change rides a warm light sweep, not a
        # hard cut. That single difference is why it reads as a film and ours reads as four clips
        # in a row: every hard cut is a place where the colour, the light and the face have to
        # match exactly, and Veo cannot guarantee any of the three.
        #
        # `xfade=transition=fadewhite` is that sweep: the outgoing shot blooms to white and the
        # next one emerges from it. It also HIDES the mismatch a cut exposes — which is the honest
        # reason to use it here rather than an aesthetic one.
        #
        # Falls back to the stream-copy concat: a transition is a finish, and a film that cannot
        # be cross-faded must still be delivered.
        _swept = _crossfade(norm_clips, bg, fps=fps, dur=_SWEEP_S)
        if not _swept:
            run_ffmpeg(["-f", "concat", "-safe", "0", "-i", str(listfile), "-c", "copy", str(bg)])

        # 4) finalize, muxing optional voice-over (primary) + music (ducked under it).
        # THE SAME NUMBER THE PICTURE IS. `total_duration_s` is a stored field and the clips are
        # cut to `scene.duration_s`; when they disagree the music fade is placed for a film that
        # does not exist. MEASURED 2026-08-02: picture 13.03s, audio 15.46s, fade scheduled at
        # 13.96s — i.e. after the last frame. Summing the scenes cannot drift from what was cut.
        total = round(sum(float(getattr(s, "duration_s", 0.0) or 0.0)
                          for s in storyboard.scenes), 3) or storyboard.total_duration_s
        # EVERY SWEEP COSTS ITS OWN LENGTH. `xfade` OVERLAPS the two shots it joins, so a film of
        # N clips comes out (N-1) x sweep SHORTER than the sum of its scenes. Leaving `total` at
        # the sum would schedule the music fade past the last frame and let the container run
        # longer than the picture — the exact defect measured this morning (container 15.46s,
        # picture 13.03s) and fixed hours ago. A new feature must not re-open it.
        if _swept and len(norm_clips) > 1:
            total = round(total - (len(norm_clips) - 1) * _SWEEP_S, 3)
        has_vo = bool(voiceover_path and Path(voiceover_path).is_file())
        has_music = bool(music_path and Path(music_path).is_file())
        has_audio = has_vo or has_music
        # THE FADE BELONGS AT THE END OF THE FILM, NOT THE END OF THE PICTURE.
        #
        # An end card is appended after this function returns, so the delivered film is longer than
        # `total`. Fading the music out 1.5s before the PICTURE ends dips the track just as the card
        # arrives — the logo lands in a decrescendo that has already finished. `music_tail_s` is how
        # much film comes after the picture; scheduling past the `-t total` cut simply means the
        # fade never fires here and the card's own tail carries it (see `reel/endcard.py`).
        _fade_at = max(0.0, total + float(music_tail_s or 0.0) - 1.5)
        if has_vo and has_music:
            # VO at full level; music faded + ducked to a quiet bed beneath it.
            run_ffmpeg([
                "-i", str(bg), "-i", str(Path(voiceover_path).resolve()),
                "-i", str(Path(music_path).resolve()),
                "-filter_complex",
                f"[2:a]volume=0.18,afade=t=in:st=0:d=1.0,"
                f"afade=t=out:st={_fade_at:.2f}:d=1.5[m];"
                f"[1:a][m]amix=inputs=2:duration=first:dropout_transition=0[a]",
                "-map", "0:v", "-map", "[a]",
                "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-shortest",
                "-t", f"{total:.3f}",
                "-movflags", "+faststart", str(out_path.resolve()),
            ])
        elif has_vo:
            run_ffmpeg([
                "-i", str(bg), "-i", str(Path(voiceover_path).resolve()),
                "-map", "0:v", "-map", "1:a",
                "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-shortest",
                "-t", f"{total:.3f}",
                "-movflags", "+faststart", str(out_path.resolve()),
            ])
        elif has_music:
            run_ffmpeg([
                "-i", str(bg), "-i", str(Path(music_path).resolve()),
                "-filter_complex",
                f"[1:a]afade=t=in:st=0:d=1.0,"
                f"afade=t=out:st={_fade_at:.2f}:d=1.5[a]",
                "-map", "0:v", "-map", "[a]",
                "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-shortest",
                "-t", f"{total:.3f}",
                "-movflags", "+faststart", str(out_path.resolve()),
            ])
        else:
            run_ffmpeg([
                "-i", str(bg), "-c", "copy", "-movflags", "+faststart",
                str(out_path.resolve()),
            ])

    return ReelRenderResult(
        reel_path=str(out_path), filename=out_path.name,
        width=W, height=H, duration_s=storyboard.total_duration_s, fps=fps,
        provider=provider.name, fallback_used=fallback_used, has_audio=has_audio,
        scene_qa=scene_qa, budget_stopped=budget_stopped, budget_note=budget_note,
    )
