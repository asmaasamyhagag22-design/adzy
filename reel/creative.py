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


def _motion_directive(scene, *, look: str = "") -> str:
    """The per-scene MOTION CONTRACT appended to the i2v prompt (owner v2 verdict: Kling
    garbled the pack label in 4/8 scenes and they all fell to Ken Burns). A hero scene pins
    the pack and moves the CAMERA; a free scene must have no text to garble at all.

    `look` is the run's LOOK LOCK, repeated here because the animation model re-grades what it
    is given: the still and the clip it becomes must ask for the same light, or the film splits
    into two looks between the seed and the motion."""
    if getattr(scene, "hero_product", False):
        base = ("The product pack stays perfectly STATIC, sharp and fully legible exactly as "
                "in the first frame — never redraw, warp or animate any label text or logo. "
                "Only the camera moves (slow push-in or gentle orbit) and any person moves "
                "naturally around the still pack.")
    else:
        base = ("Free, dynamic, cinematic motion. NO readable text, labels, logos, screens or "
                "signage anywhere in the frame at any point.")
    if str(look or "").strip():
        base += f"\nTHE LOOK (identical in every shot of this film): {look.strip()}"
    return base


def _product_asset(profile: Optional[dict], scene, real: Optional[str]) -> Optional[str]:
    """The REAL asset to composite for a hero-product scene (Phase D): the scene's own real photo
    for real_photo, else the brand's homepage/app screenshot for real_screen. None -> no composite."""
    if not getattr(scene, "hero_product", False):
        return None
    ov = (getattr(scene, "product_overlay", "") or "").lower()
    if ov == "real_photo" and real:
        return real
    if ov not in ("real_screen", "real_photo"):
        # THE DIRECTOR FORGETTING A FIELD MUST NOT COST THE CURE. MEASURED on Nano Care: the plan
        # marked the scene `hero_product` but left `product_overlay` empty, so no real asset was
        # composited, the packaging went through Kling, and QA refused it — "the brand name and
        # other details replaced by garbled, illegible text" — twice, then fell back to Ken Burns.
        # A real photograph of the product exists; when a scene is ABOUT the product, use it.
        return real or None
    vis = (profile or {}).get("visual") if isinstance(profile, dict) else None
    shot = vis.get("homepage_screenshot_path") if isinstance(vis, dict) else None
    if isinstance(shot, dict):
        shot = shot.get("value")
    return (str(shot) if shot else None) or real


def _composited_product_guard(composited: bool) -> str:
    """Tell the video model to leave the product's place EMPTY when we paste the real one.

    Without this the frame carries BOTH: a garbled invented box from the generator and the real
    packshot composited on top. The poster learned this first (`_product_scene_guard`), and the
    reel needs it for the same reason — a video model repaints a label every frame.
    """
    if not composited:
        return ""
    return ("\nDO NOT DRAW THE PRODUCT, its packaging, its label or any text on it. The real "
            "product photograph is composited into this shot afterwards, so leave its place "
            "EMPTY: a clean, well-lit surface with room for it and nothing standing there.")


def build_creative_storyboard(reel: CreativeReel, brief: PosterBrief,
                              captions: Optional[bool] = None,
                              seeds: Optional[list] = None,
                              profile: Optional[dict] = None,
                              verbatim_prompts: bool = False,
                              look: str = "") -> Storyboard:
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
        # Resolved BEFORE the prompt is built: when the real product is going to be pasted on top,
        # the generator must be told to leave its place empty, or the shot carries a garbled
        # invented box underneath a real one.
        _asset = _product_asset(profile, s, real)
        scenes.append(ReelScene(
            kind=kind,
            duration_s=round(dur, 2),
            # HITL law: an APPROVED plan renders VERBATIM — the motion contract is only
            # appended to prompts the DIRECTOR just wrote (verbatim_prompts=False).
            visual_prompt=(str(s.veo_prompt or "") if verbatim_prompts
                           else str(s.veo_prompt or "").rstrip() + "\n"
                                + _motion_directive(s, look=look)
                                + _composited_product_guard(bool(_asset))),
            seed_image_url=seed,
            place_ref_url=place_ref,
            product_asset_url=_asset,   # Phase D: real screen/photo to composite
            product_placement=(s.product_overlay or "").strip(),
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
    # HITL SANCTITY (owner, 2026-07-12 — "you wrote a prompt that was never executed"): a
    # user-APPROVED plan is FINAL creative. The coherence gate may CHECK and LOG it, but NOTHING
    # may swap in a regenerated plan the user never saw — that betrayal is what the law forbids.
    if plan_override:
        from reel.creative_director import CreativeReel
        try:
            creative = CreativeReel.model_validate(plan_override)
        except Exception:
            creative = None
        if not creative or not creative.scenes:
            return None, None
        try:
            from reel.coherence import evaluate_reel_coherence
            crep = evaluate_reel_coherence(creative, profile=profile,
                                           featured=bool(featured_product))
            if not crep.ok:
                logger.info("reel coherence on APPROVED plan: issues=%s -> advisory only, the "
                            "user's plan renders verbatim", crep.issues)
        except Exception:  # noqa: BLE001 — the gate never blocks a render
            pass
    else:
        # No approved plan -> the coherence gate AUTHORS + auto-corrects before the 10-15 min Veo
        # render (product shown, one CTA, one dialect, sound, brand-specific, feasible). Bounded
        # retries of a cheap text call; the compositor's scene_qa still checks each rendered clip.
        from reel.coherence import plan_reel_gated
        try:
            from business_profile.llm import default_caller
            _critic = default_caller(strong=False)
        except Exception:  # noqa: BLE001
            _critic = None
        creative, crep = plan_reel_gated(
            profile, photos, n_scenes=n_scenes, language=language,
            featured_product=featured_product, photo_subjects=photo_subjects, critic_caller=_critic)
        if not creative or not creative.scenes:
            return None, None
        if not crep.ok:
            logger.info("reel coherence (final): issues=%s", crep.issues)

    # GROUNDING GATE (2026-07-12 — closes the reel's LAST ungated copy surface; the owner's
    # pre-EXECUTE check found the creative path audit-only): the spoken voiceover, hook, cta
    # and any caption now pass the shared drop-to-grounded policy — a line carrying an
    # UNSOURCED hard claim is BLANKED (ambience instead of speech), never rewritten. Runs on
    # APPROVED plans too: the HITL law lets gates veto/log — blanking is not a plan swap.
    _blanked_vo = _had_vo = 0
    _had_hook = _had_cta = False
    try:
        from reel.grounding import grounded_captions
        vo = [s.voiceover for s in creative.scenes]
        _had_vo = sum(1 for a in vo if (a or "").strip())
        kept_vo = grounded_captions(profile, vo)
        blanked = sum(1 for a, b in zip(vo, kept_vo) if a and not b)
        _blanked_vo = blanked
        if blanked:
            logger.info("[reel] %d voiceover line(s) blanked (unsourced hard claim)", blanked)
        for s, k in zip(creative.scenes, kept_vo):
            s.voiceover = k
        caps = grounded_captions(profile, [s.on_screen_text for s in creative.scenes])
        for s, k in zip(creative.scenes, caps):
            s.on_screen_text = k
        _had_hook = bool((creative.hook or "").strip())
        _had_cta = bool((creative.cta or "").strip())
        hc = grounded_captions(profile, [creative.hook, creative.cta])
        creative.hook, creative.cta = hc[0], hc[1]
    except Exception:  # noqa: BLE001 — the gate never loses a reel
        pass

    # البند 5 (owner: "مينفعش التكلفة تروح على الفاضي") — PRE-SPEND HARD GATE: this is the last
    # point before the first PAID call (seed images -> TTS -> Veo). Blanking a line or two is
    # normal remediation, but a reel whose copy was GUTTED BY THE GROUNDING GATE (hook+cta both
    # stripped, or most of an existing voiceover killed) is a creative corpse — rendering it
    # burns real money for an unusable video. Abort HERE, before a single dollar, with the real
    # reason. NOTE: measures what grounding REMOVED, never what the design left silent — an
    # ambience-only reel with no voiceover by design passes untouched.
    _gutted_hc = bool(_had_hook and not (creative.hook or "").strip()) \
        and bool(_had_cta and not (creative.cta or "").strip())
    if _gutted_hc or (_had_vo and _blanked_vo * 2 > _had_vo):
        raise RuntimeError(
            "PRE-SPEND GATE: معظم كوبي الريل سقط في بوابة التوثيق (ادّعاءات بلا مصدر) — "
            "أوقفنا التوليد قبل صرف أي رندر مدفوع. راجعي البريف/البيانات وأعيدي المحاولة.")

    # FULLY-GENERATED SCENES (owner ruling 2026-07-12): real photos exit the display path
    # and become conditioning + judging references. REEL_SEED_MODE=real restores legacy.
    seeds = None
    import os as _os
    if (_os.environ.get("REEL_SEED_MODE") or "generated").lower() != "real":
        try:
            from reel.seed_gen import generate_scene_seeds
            # البند 5 index-basis fix: the director assigns scene.image_index relative to the
            # photos it actually RECEIVED (creative.images = the fetched subset) — indexing the
            # pre-fetch list here resolved the SAME index to a DIFFERENT photo whenever a URL
            # was dropped, seeding scenes with the wrong product. One basis everywhere.
            _seed_photos = list(getattr(creative, "images", None) or photos or [])
            # THE DNA WAS ON DISK AND NEVER LOADED. `brand_dna=None` here meant the seed prompts'
            # `dna_lines` were always empty — the brand book existed (outputs/brandbooks/) and the
            # stills were generated with no imagery style, mood or motifs. Best-effort: no DNA
            # file degrades to exactly the old behaviour.
            _dna = None
            try:
                # The poster's own resolver: cached brandbook by slug, else None (never builds
                # here — a reel render must not trigger a Serper+vision DNA build as a side effect).
                from poster.pipeline import load_or_build_dna
                _dna = load_or_build_dna(profile, None)
            except Exception:  # noqa: BLE001
                _dna = None
            # ONE LOOK FOR THE WHOLE FILM (owner 2026-07-26: «ثبّت كلمات وصف الإضاءة والألوان في
            # كل البرومبتات»). Resolved ONCE here and passed down to the seeds AND the motion
            # prompts — recomputing it per scene is how one film ends up with two grades.
            try:
                from brand.look import look_lock
                _look = look_lock(brand_dna=_dna, profile=profile)
            except Exception:  # noqa: BLE001
                _look = ""
            # THE UNGATED REFERENCE POOL — the fix for «فين المنتجات»: when the display gate
            # rejects every photo, real brand imagery still reaches the stills as conditioning.
            _pool: list = []
            try:
                from reel.image_quality import reference_pool
                _pool = reference_pool(profile)
            except Exception:  # noqa: BLE001
                _pool = []
            # THE BRAND'S SELECTED PRESENTER fronts every scene (the owner's influencer flow) —
            # the same face in the poster and the reel, which is the whole point of picking one.
            _face = None
            try:
                # The brand's SELECTED presenter, resolved through the webapp's own store on disk
                # (the CLI holds only the profile). `selected_face_bytes` is strict: it returns
                # the CHOSEN candidate or nothing — never "whoever was generated last".
                from api.brand_translate import domain_key_from_url
                from api.brand_store import BrandStore
                from api.webapp_influencer import selected_face_bytes
                _src = profile.get("source_url")
                _src = _src.get("value") if isinstance(_src, dict) else _src
                _dk = domain_key_from_url(str(_src or ""))
                if _dk:
                    _b = BrandStore().get(_dk)
                    if _b:
                        _fb, _fm = selected_face_bytes(_b)
                        _face = (_fb, _fm) if _fb else None
            except Exception:  # noqa: BLE001 — no store / no selection -> no face, never a crash
                _face = None
            seeds = generate_scene_seeds(creative, _seed_photos, profile=profile,
                                         brand_dna=_dna, ref_pool=_pool, identity_ref=_face,
                                         out_dir=Path(out_path).parent, caller=qa_caller_seed(),
                                         look=_look,
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

    storyboard = build_creative_storyboard(creative, brief, seeds=seeds, profile=profile,
                                           verbatim_prompts=bool(plan_override),
                                           look=locals().get("_look", "") or "")

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
                                  deliveries=vo_deliveries, tone=_brand_tone(profile),
                                  profile=profile)
        if vo_path:
            logger.info("voice-over track ready: %s", vo_path)
            # ZERO-LATENCY CAPTIONS: map each scene's caption onto the narration's word schedule
            # (written by tts_with_timestamps as a sidecar). Best-effort by design — a caption the
            # voice never says, or a run with no sidecar, keeps the designed stagger.
            try:
                import json as _json
                _wp = Path(str(vo_path) + ".words.json")
                if not _wp.is_file():
                    _wp = Path(str(vo_path)).with_suffix(".mp3.words.json")
                if _wp.is_file():
                    _words = _json.loads(_wp.read_text(encoding="utf-8"))
                    from reel.word_sync import caption_delays
                    _t, _cursor = 0.0, 0
                    for _sc in storyboard.scenes:
                        if (_sc.headline or "").strip():
                            _got = caption_delays(_sc.headline, _words,
                                                  scene_start_s=_t, search_from=_cursor)
                            if _got:
                                _sc.caption_word_delays, _cursor = list(_got[0]), _got[1]
                        _t += float(_sc.duration_s or 0.0)
                    _n = sum(1 for _sc in storyboard.scenes if _sc.caption_word_delays)
                    logger.info("[reel] word-sync: %d scene caption(s) on the spoken schedule", _n)
            except Exception:  # noqa: BLE001 — the schedule is a bonus, never a dependency
                logger.exception("word-sync unavailable; captions keep the designed stagger")

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

    # SOUND DESIGN (overhaul Phase C): the creative reel was SILENT but for the VO. When no real
    # music is supplied, synthesize a mood-tuned bed + the plan's authored per-scene sfx (whoosh /
    # impact / chime) so the sound follows the story arc and no scene is dead air. Free ffmpeg synth;
    # the compositor ducks it under the VO. Best-effort — a synth failure just leaves today's behavior.
    if music_path is None:
        try:
            from .sound import synth_sound_bed
            starts, acc = [], 0.0
            for s in storyboard.scenes:
                starts.append(acc)
                acc += float(getattr(s, "duration_s", 0.0) or 0.0)
            sfx_beats = [(starts[i], (creative.scenes[i].sfx or "").strip())
                         for i in range(min(len(starts), len(creative.scenes)))
                         if (creative.scenes[i].sfx or "").strip()]
            mood = (creative.sound_design or creative.music_mood or "")
            bed = synth_sound_bed(Path(out_path).with_suffix(".bed.m4a"),
                                  storyboard.total_duration_s or acc,
                                  cut_times=starts[1:], mood=mood, sfx_beats=sfx_beats)
            if bed:
                music_path = str(bed)
                logger.info("[reel] sound bed synthesized (mood=%r, %d sfx beats)",
                            mood[:40], len(sfx_beats))
        except Exception:  # noqa: BLE001 — never break a render on the sound bed
            pass

    result = render_reel(
        storyboard, provider=provider, out_path=out_path,
        scale=scale, include_logo=include_logo,
        music_path=music_path, voiceover_path=vo_path,
        qa_caller=qa_caller, qa_product_hint=featured_product, qa_reference_image=qa_ref,
    )
    return result, creative
