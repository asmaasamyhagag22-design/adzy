"""Creative reel orchestration: Opus director -> Veo 3.1 -> voice-over.

Ties the pieces into the "complete reel" the user asked for:
  1. Opus (vision) designs the reel from the identity + real photos
     (reel.creative_director) — per-scene Veo prompts + voice-over + captions.
  2. Each scene's REAL photo is seeded into Veo 3.1 image-to-video with Opus's
     cinematic motion prompt (so the motion happens INSIDE the real scene).
  3. The voice-over lines are synthesized (reel.voiceover) into a timed track and
     muxed under the visuals (with optional music ducked beneath).

The visuals stay grounded in the brand's real photos; only the motion + narration
are generated. Honest-degrades: no Opus key -> caller should fall back to the
deterministic storyboard; no TTS key -> the reel renders without narration.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Optional

from poster.schemas import PosterBrief

from .compositor import render_reel
from .creative_director import CreativeReel, design_creative_reel
from .from_profile import is_rtl
from .schemas import ReelScene, Storyboard
from .video_provider import VideoProvider
from .voiceover import synth_voiceover

logger = logging.getLogger(__name__)


def _brand_tone(profile: Any) -> str:
    """The brand's tone-of-voice value (e.g. 'luxury') from the profile, so the voice-over
    delivery fits the brand instead of a hardcoded read. Robust to the {'profile': {...}} wrapper."""
    try:
        p = profile.get("profile", profile) if isinstance(profile, dict) else {}
        t = p.get("tone_of_voice")
        if isinstance(t, dict):
            t = t.get("value")
        return str(t or "").lower()
    except Exception:
        return ""


def build_creative_storyboard(reel: CreativeReel, brief: PosterBrief,
                              captions: Optional[bool] = None,
                              seeds: Optional[list] = None) -> Storyboard:
    """Map an Opus CreativeReel onto the render Storyboard. Each scene carries its
    real-photo seed + Opus's Veo prompt.

    OWNER REVERSAL (2026-07-12, "الغي الكلام اللي ع الريل خالص خليه صور"): per-scene kinetic
    captions are OFF by default — the reel is pure footage + voice-over; the branded END-CARD
    (real logo, deterministic) still closes it. Re-enable with captions=True or
    REEL_CAPTIONS=on (the routing logic is preserved, not deleted)."""
    import os
    if captions is None:
        captions = os.environ.get("REEL_CAPTIONS", "").lower() in ("on", "1", "true")
    scenes: list[ReelScene] = []
    n = len(reel.scenes)
    for idx, s in enumerate(reel.scenes):
        real = reel.images[s.image_index] if 0 <= s.image_index < len(reel.images) else None
        seed, place_ref = real, None
        if seeds is not None and idx < len(seeds):
            # FULLY-GENERATED mode (owner ruling 2026-07-12): the generated still is the
            # visible seed; the real photo becomes the place-fidelity judging reference.
            gen_path, ref_url = seeds[idx]
            if gen_path:
                seed, place_ref = gen_path, (ref_url or real)
            else:
                seed, place_ref = (ref_url or real), None   # legacy fallback, logged upstream
        cap = (s.on_screen_text or "").strip() if captions else ""
        words = len(cap.split())
        # READABLE PACING (owner: 'the text goes by too fast'): a captioned scene must hold long
        # enough to READ — research CPS rule ~ 1.5s + 0.35s/word, hard 3s floor for a phrase.
        base = max(1.5, min(s.duration_s, 8.0))
        dur = min(8.0, max(base, 3.0, 1.5 + 0.35 * words)) if cap else base
        # LISTENABLE PACING (owner, 2026-07-12: 'سرعة الصوت صعبة أوي منلحقش نفهم'): a narrated
        # scene must hold long enough to SAY its line at a natural pace (~2.2 words/sec + a
        # breath). The VIDEO stretches to fit the words — never the audio sped to fit the video.
        vo_words = len((s.voiceover or "").split())
        if vo_words:
            dur = min(8.0, max(dur, round(0.3 + vo_words / 2.2, 2)))
        # DESIGNED captions (owner: 'text with no design'): route the caption into the fields that
        # trigger the template's DESIGNED styles — scene 0 -> the hero LOCKUP + logo (kind=intro),
        # the last scene -> the accent CTA CHIP + logo (kind=outro), middle captions -> the big
        # display .headline (NOT the tiny flat .item subline the gallery path used before).
        kind, headline, cta_text = "gallery", "", ""
        if cap and idx == 0:
            kind, headline = "intro", cap
        elif cap and idx == n - 1:
            kind, cta_text = "outro", cap
        elif cap:
            headline = cap
        scenes.append(ReelScene(
            kind=kind,
            duration_s=round(dur, 2),
            visual_prompt=s.veo_prompt,
            seed_image_url=seed,
            place_ref_url=place_ref,
            headline=headline,
            cta_text=cta_text,
            source_field="creative",
        ))
    primary_dir = "rtl" if (reel.language.startswith("ar") or is_rtl(reel.hook)
                            or is_rtl(brief.business_name)) else "ltr"
    return Storyboard(
        business_name=brief.business_name,
        primary_dir=primary_dir,
        total_duration_s=round(sum(s.duration_s for s in scenes), 2),
        scenes=scenes,
        palette_hex=list(brief.palette_hex[:6]),
        primary_color=brief.primary_color,
        tone=brief.tone,
        logo_url=brief.logo_url,
        logo_text=brief.logo_text,
        heading_font=brief.heading_font,
        body_font=brief.body_font,
        content_images=list(reel.images),
        warnings=list(brief.warnings),
    )


def qa_caller_seed():
    """The vision caller for the seed gate (None degrades permissive)."""
    try:
        from business_profile.llm import default_caller
        return default_caller(strong=True)
    except Exception:  # noqa: BLE001
        return None


def render_creative_reel(
    profile: dict,
    brief: PosterBrief,
    photos: list[str],
    *,
    provider: VideoProvider,
    out_path: str | Path,
    n_scenes: int = 6,
    language: Optional[str] = None,
    scale: float = 1.0,
    include_logo: bool = True,
    music_path: Optional[str | Path] = None,
    with_voiceover: bool = True,
    featured_product: Optional[str] = None,
    plan_override: Optional[dict] = None,
    photo_subjects: Optional[list[str]] = None,
):
    """Full creative pipeline. Returns (render_result, creative_reel) or (None, None)
    when Opus could not design a reel (caller falls back to the normal storyboard).
    `featured_product` (set when the user picked one) makes the WHOLE reel about that item.
    `photo_subjects` (aligned to `photos`) tells the director what each photo depicts."""
    # HITL (owner law 2026-07-12): a user-APPROVED plan replaces the director call
    # verbatim; every downstream gate (plan_eval, scene_qa, grounding) still runs on it.
    if plan_override:
        from reel.creative_director import CreativeReel
        try:
            creative = CreativeReel.model_validate(plan_override)
        except Exception:
            creative = None
    else:
        creative = design_creative_reel(profile, photos, n_scenes=n_scenes, language=language,
                                        featured_product=featured_product,
                                        photo_subjects=photo_subjects)
    if not creative or not creative.scenes:
        return None, None

    # PRE-RENDER EVAL (owner: 'evaluate the reel before it comes out'): score the PLAN against the
    # stranger test + caption rules BEFORE the 10-15 min Veo render. A weak plan is regenerated once
    # (a cheap Opus call) rather than rendered; the compositor's scene_qa still checks each rendered
    # clip afterwards. Deterministic, so it never adds a network dependency.
    # HITL SANCTITY (owner, 2026-07-12 — "you wrote a prompt that was never executed"): a
    # user-APPROVED plan is FINAL creative. Gates may check and LOG it, but NOTHING may swap in
    # a regenerated plan the user never saw — that is exactly the betrayal the law forbids.
    try:
        from reel.plan_eval import evaluate_reel_plan
        verdict = evaluate_reel_plan(creative, profile=profile, featured=bool(featured_product))
        if not verdict.ok and plan_override:
            logger.info("reel plan eval on APPROVED plan: score=%d issues=%s -> advisory only, "
                        "the user's plan renders verbatim", verdict.score, verdict.issues)
        elif not verdict.ok:
            logger.info("reel plan eval: score=%d issues=%s -> regenerating once",
                        verdict.score, verdict.issues)
            retry = design_creative_reel(profile, photos, n_scenes=n_scenes, language=language,
                                         featured_product=featured_product,
                                         photo_subjects=photo_subjects)
            if retry and retry.scenes:
                v2 = evaluate_reel_plan(retry, profile=profile, featured=bool(featured_product))
                if v2.score >= verdict.score:                 # keep the stronger plan
                    creative, verdict = retry, v2
            logger.info("reel plan eval (final): score=%d issues=%s", verdict.score, verdict.issues)
    except Exception:  # noqa: BLE001 — the eval must never block a render
        pass

    # GROUNDING GATE (2026-07-12 — closes the reel's LAST ungated copy surface; the owner's
    # pre-EXECUTE check found the creative path audit-only): the spoken voiceover, hook, cta
    # and any caption now pass the shared drop-to-grounded policy — a line carrying an
    # UNSOURCED hard claim is BLANKED (ambience instead of speech), never rewritten. Runs on
    # APPROVED plans too: the HITL law lets gates veto/log — blanking is not a plan swap.
    try:
        from reel.grounding import grounded_captions
        vo = [s.voiceover for s in creative.scenes]
        kept_vo = grounded_captions(profile, vo)
        blanked = sum(1 for a, b in zip(vo, kept_vo) if a and not b)
        if blanked:
            logger.info("[reel] %d voiceover line(s) blanked (unsourced hard claim)", blanked)
        for s, k in zip(creative.scenes, kept_vo):
            s.voiceover = k
        caps = grounded_captions(profile, [s.on_screen_text for s in creative.scenes])
        for s, k in zip(creative.scenes, caps):
            s.on_screen_text = k
        hc = grounded_captions(profile, [creative.hook, creative.cta])
        creative.hook, creative.cta = hc[0], hc[1]
    except Exception:  # noqa: BLE001 — the gate never loses a reel
        pass

    # FULLY-GENERATED SCENES (owner ruling 2026-07-12): real photos exit the display path
    # and become conditioning + judging references. REEL_SEED_MODE=real restores legacy.
    seeds = None
    import os as _os
    if (_os.environ.get("REEL_SEED_MODE") or "generated").lower() != "real":
        try:
            from reel.seed_gen import generate_scene_seeds
            seeds = generate_scene_seeds(creative, list(photos or []), profile=profile,
                                         brand_dna=None,
                                         out_dir=Path(out_path).parent, caller=qa_caller_seed(),
                                         log=print)   # the CLI shows [seedgen] stats
        except Exception as exc:  # noqa: BLE001 — seed generation failing = legacy mode
            logger.warning("seed generation unavailable (%s) -> legacy real-photo seeds",
                           type(exc).__name__)
            seeds = None

    # OWNER VERDICT (calibration #1: the raw distant photo read as "قطع خالص"): in
    # fully-generated mode a scene whose seed could not be generated is DROPPED (a gate
    # veto, logged) rather than displayed as a raw photo — unless fewer than 3 scenes
    # would remain (the absolute last resort keeps the reel alive).
    if seeds is not None:
        keep = [i for i, (sp, _r) in enumerate(seeds) if sp]
        if len(keep) >= 3 and len(keep) < len(seeds):
            dropped = [i for i in range(len(seeds)) if i not in keep]
            print(f"[reel] {len(dropped)} scene(s) dropped — generated seed unavailable "
                  f"(the ruling keeps raw photos off the display path): {dropped}")
            creative.scenes = [creative.scenes[i] for i in keep]
            seeds = [seeds[i] for i in keep]

    storyboard = build_creative_storyboard(creative, brief, seeds=seeds)

    # DEAD-TAIL FIT (owner, twice: "الكلام خلص من 10 ثواني والفيديو مكمل"): the video's
    # total must track the SURVIVING speech (post-grounding). Estimate the read at the
    # same natural pace the pacing stretch uses, and SHRINK scenes proportionally when
    # the video would outlive the narration by more than ~2s (2.0s per-scene floor).
    if with_voiceover:
        _lines = [(s_.voiceover or "").strip() for s_ in creative.scenes]
        _words = sum(len(l.split()) for l in _lines if l)
        _n = sum(1 for l in _lines if l)
        if _words:
            speech_est = _words / 2.2 + 0.4 * max(0, _n - 1) + 0.5
            total = storyboard.total_duration_s or 0.0
            target = speech_est + 2.0
            if total > target > 0:
                scale = target / total
                for sc_ in storyboard.scenes:
                    sc_.duration_s = round(max(2.0, sc_.duration_s * scale), 2)
                storyboard.total_duration_s = round(
                    sum(s_.duration_s for s_ in storyboard.scenes), 2)
                print(f"[reel] dead-tail fit: video {total:.1f}s -> "
                      f"{storyboard.total_duration_s:.1f}s (speech ~ {speech_est:.1f}s)")

    vo_path = None
    if with_voiceover:
        vo_lines = [s.voiceover for s in creative.scenes]
        vo_durs = [s.duration_s for s in storyboard.scenes]
        vo_deliveries = [s.voiceover_delivery for s in creative.scenes]
        vo_path = synth_voiceover(vo_lines, vo_durs, Path(out_path).with_suffix(".vo.m4a"),
                                  deliveries=vo_deliveries, tone=_brand_tone(profile))
        if vo_path:
            logger.info("voice-over track ready: %s", vo_path)

    # SCENE QA — ALL creative reels (owner finding 2026-07-12: service-brand reels rendered
    # visually UNGATED and the NTI 'old school' hallucination shipped). A Gemini vision caller
    # inspects each generated clip: product fidelity when a product is featured (its photo is
    # the reference), and for every reel the motion-QA criteria (faces, morphing, junk text,
    # ad-grade, setting vs the scene's OWN real seed). Regenerate once, then faithful KenBurns.
    try:
        from business_profile.llm import default_caller
        qa_caller = default_caller(strong=True)
    except Exception:
        qa_caller = None
    qa_ref = None
    if featured_product and qa_caller is not None:
        seed_url = storyboard.content_images[0] if storyboard.content_images else None
        if seed_url:
            try:
                from reel.video_provider import _load_reference_image
                loaded = _load_reference_image(seed_url)
                qa_ref = loaded[0] if loaded else None
            except Exception:
                qa_ref = None

    result = render_reel(
        storyboard, provider=provider, out_path=out_path,
        scale=scale, include_logo=include_logo,
        music_path=music_path, voiceover_path=vo_path,
        qa_caller=qa_caller, qa_product_hint=featured_product, qa_reference_image=qa_ref,
    )
    return result, creative
