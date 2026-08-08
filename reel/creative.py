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
# MODULE-LEVEL, and the reason is the bug class this repo has already paid for twice (the missing
# `os` in reel/__main__.py, the missing `logger` in reel/endcard.py): these were imported inside
# the beat-grid `try`, and two callers further down the SAME function use them. A grid failure
# would leave the names unbound and turn a degraded tempo into a NameError mid-render.
from .runtime import (
    DEFAULT_RUNTIME_S, endcard_tail_s, fit_to_runtime, narration_fit, picture_budget_s,
)
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
    """The per-scene MOTION CONTRACT appended to the i2v prompt (owner v2 verdict: the video model
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
        # THE TEXT BAN LIVES IN ONE PLACE NOW. `compositor._NO_TEXT` rides every prompt — this
        # said the same thing a second time, in a second wording, in the same request. Two
        # statements of one rule do not double it; they take the space the SCENE needed. MEASURED
        # 2026-08-06: only 21% of what reached Veo was the shot the director wrote.
        base = "Free, dynamic, cinematic motion."
    return base + _look_clause(base, look)


_LOOK_MARK = "THE LOOK (identical in every shot of this film):"


def _look_clause(existing: str, look: str) -> str:
    """The look sentence, or "" when there is nothing to say or it is already said.

    Split out of `_motion_directive` so the APPROVED (verbatim) path can carry the look without
    carrying the motion contract with it — the two used to be one string, which is why an approved
    plan silently lost its look lock. Idempotent: an owner who wrote the look into her own prompt
    is not given it twice, and re-running the builder on an already-built prompt is a no-op.
    """
    text = str(look or "").strip()
    if not text or _LOOK_MARK in str(existing or "") or text in str(existing or ""):
        return ""
    return f"\n{_LOOK_MARK} {text}"


def _product_asset(profile: Optional[dict], scene, real: Optional[str]) -> Optional[str]:
    """The REAL asset to composite for a hero-product scene — SCREENS ONLY now.

    THE USE DETERMINES THE METHOD (owner's production rule, 2026-07-28):

        «لو بتعملي ريل فيديو، اعتمدي على التوليد بدون أي قلق، لأن حركة الإيد والإضاءة هتبطّل
         العجب… لكن لو بتطلعي بوستر ثابت عالي الدقة، ارجعي لطريقة القص واللزق»

    A poster is zoomed into and read; a two-second clip is not. So the poster pastes and the reel
    GENERATES — motion and lighting carry the frame, and nobody pauses a reel to read the
    ingredients panel.

    MEASURED ON A LIVE VEO RUN (2026-08-01), which is what forced this. Scene 0's QA verdict:

        "The product is not generated within the video scene; it is a STATIC IMAGE CRUDELY
         OVERLAID on top of unrelated footage."

    The gate was right. That scene burned THREE Veo renders at $1.60 each failing on the paste,
    fell back to Ken Burns anyway, and the wasted spend exhausted the per-run budget before
    scenes 3, 4 and 5 got a clip at all — so a defect in the compositing cost the reel most of
    its actual motion. The frames that came back BEST in the same run were the ones the model
    drew: a Nano Care carton on a kitchen counter, brand red, "Nanocare Liposomal E" legible.

    A SCREEN IS STILL PASTED, and that is not an exception to the rule but an application of it.
    An app UI is not packaging with a brand mark on it — it is dense fine print that IS the
    product, it does not benefit from motion hiding it, and a video model cannot render legible
    UI at any size. `real_screen` stays; the physical-pack paste goes.
    """
    if not getattr(scene, "hero_product", False):
        return None
    ov = (getattr(scene, "product_overlay", "") or "").lower()
    if ov != "real_screen":
        # Physical product (real_photo, or a director who left the field empty): the model draws
        # it. The seed generator already renders the product into the still, and the seed QA gate
        # rejects a garbled label BEFORE any Veo spend — which is where that defect belongs.
        return None
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
                              look: str = "",
                              hero_product_url: str = "") -> Storyboard:
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
    # ONE PRODUCT BEAT, AND IT IS THE LAST ONE. Since Axis 1, a scene marked `hero_product` is not
    # generated at all — it is COMPOSITED from the brand's own photograph, a motionless push-in on
    # a packshot. That is the right shot to end an ad on and the wrong thing to build one out of.
    #
    # MEASURED 2026-08-08 on the owner's Vichy link: with a featured product the director marked
    # hero_product on ALL FOUR scenes, so the whole film resolved to four static inserts and no
    # generated footage — a slideshow, one message after she asked for «كله يبقا veo». The
    # director's brief now asks for one; this GUARANTEES it, because a prompt is a request and the
    # compositor needs a fact. A mid-film mark is dropped, not honoured.
    _marked = [i for i, sc in enumerate(reel.scenes) if bool(getattr(sc, "hero_product", False))]
    _hero_beat = (n - 1) if n > 1 else (0 if _marked else -1)
    if len(_marked) > 1 or (_marked and _marked != [_hero_beat]):
        logger.info("[reel] the director marked %d product beat(s) %s — Axis 1 composites every "
                    "one of them, so only the last beat (%d) stays a product shot; the rest are "
                    "filmed as human moments", len(_marked), _marked, _hero_beat)
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
        # THE FILM ENDS ON THE PRODUCT, and that shot is the one we render ourselves.
        #
        # The ad spine already asks the LAST scene for "a clean, HELD hero shot of the product" —
        # a still subject, a locked camera, the name legible. That is the single worst thing to
        # ask a video model for (it repaints the label every frame; see `_product_asset` for the
        # measured cost) and the single easiest thing to render from the brand's own photograph.
        # So the last beat becomes an INSERT when a real packshot exists, and stays a generated
        # scene when none does — a service brand with no product image simply never gets one.
        # AXIS 1 (owner, 2026-08-02): «ممنوع منعاً باتاً تطلبي من Veo إنه يظهر المنتج».
        #
        # It was only the LAST beat. Any OTHER scene the director marked `hero_product` was left
        # to the generator, on a 2026-07-27 A/B that found a protected label beat a blanket ban.
        # Three live runs today overturned it, and the verdicts are in the audit trail: «the
        # advertised product is completely absent from the generated clip» (twice), «the product
        # label is rendered with garbled, illegible text», «SAV TAERMALE» for «EAU THERMALE»,
        # «IODERMA» for BIODERMA. A diffusion model cannot hold a registered label, and every
        # attempt cost $2.40 and failed its own gate.
        #
        # So EVERY product beat is composited from the brand's own photograph, and the generator
        # only ever shoots lifestyle. The one shot that came back right in all three runs was the
        # insert — because nothing generated it.
        _insert: list[str] = []
        _is_product_beat = (idx == _hero_beat)
        if _is_product_beat:
            from .insert_shot import product_candidates
            _insert = product_candidates(profile, hero_url=hero_product_url)
        if _insert:
            # An insert is not bound by the video model's 4-second minimum — nothing is being
            # generated — so it holds only as long as the beat needs: its own designed length, or
            # long enough to speak the CTA, whichever is greater. Three seconds of a motionless
            # pack is where a 15-second film goes to die.
            from .insert_shot import INSERT_S
            dur = max(INSERT_S, round(0.3 + vo_words / 2.2, 2) if vo_words else 0.0,
                      1.5 + 0.35 * words if cap else 0.0)
        scenes.append(ReelScene(
            kind=kind,
            duration_s=round(dur, 2),
            # HITL law: an APPROVED plan renders VERBATIM — the motion contract is only
            # appended to prompts the DIRECTOR just wrote (verbatim_prompts=False).
            #
            # THE LOOK IS NOT PART OF THAT LAW. `verbatim` protects the owner's CREATIVE choices:
            # what happens in the shot, how it moves, what the copy says. The look lock is a
            # TECHNICAL constraint — "every shot of this film is lit and graded the same way" —
            # and dropping it turned an approved plan into a film shot in four different rooms
            # under four different lights. `compositor.py`'s `_NO_TEXT` already sets this
            # precedent: it rides every prompt, approved or not, because a text ban is not a
            # creative opinion either. `_look_clause` no-ops when the prompt already carries it,
            # so an owner who edited the look by hand keeps her edit.
            visual_prompt=(str(s.veo_prompt or "") + _look_clause(str(s.veo_prompt or ""), look)
                           if verbatim_prompts
                           else str(s.veo_prompt or "").rstrip() + "\n"
                                + _motion_directive(s, look=look)
                                + _composited_product_guard(bool(_asset))),
            seed_image_url=seed,
            place_ref_url=place_ref,
            product_asset_url=_asset,   # Phase D: real screen/photo to composite
            product_placement=(s.product_overlay or "").strip(),
            insert_product_urls=_insert,
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
    # THE DELIVERED LENGTH, threaded rather than assumed. The CLI had it (`--max-seconds`) and the
    # API had it (`max_seconds`), and neither reached the only function that decides how long the
    # cuts are — so the format was a flag that governed the SCENE COUNT and nothing else.
    max_seconds: float = DEFAULT_RUNTIME_S,
    # THE ITEM THE OWNER NAMED — Axis 1's product selector. She pastes a product link and the film
    # is about THAT product: the insert cuts to its real packshot instead of whichever catalogue
    # entry happened to rank first. Empty keeps the catalogue's own order, i.e. every existing
    # caller is unchanged.
    hero_product_url: str = "",
):
    """Full creative pipeline. Returns (render_result, creative_reel) or (None, None)
    when Opus could not design a reel (caller falls back to the normal storyboard).
    `featured_product` (set when the user picked one) makes the WHOLE reel about that item.
    `photo_subjects` (aligned to `photos`) tells the director what each photo depicts."""
    # A PASTED LINK NAMES THE PRODUCT TOO, not just the packshot. `hero_product_url` reached the
    # INSERT and stopped there, while the director was briefed from `featured_product` — a separate
    # argument, a separate CLI flag. So a link with no `--product-name` produced a film about the
    # pharmacy in general with the right bottle glued to the last beat: the voice-over never named
    # it, and the `sfx` lines never asked for the one sound the picture is about (a spray).
    #
    # Both now read the SAME resolver, so the film and its insert cannot be about different items.
    # An explicit `featured_product` still wins — a caller who typed a name meant it.
    _product_ad = False
    if hero_product_url:
        try:
            from .insert_shot import hero_product_facts
            _hf = hero_product_facts(profile, hero_product_url)
            # RESOLVED means we hold the item's real packshot. A link that resolved to nothing is
            # not a product ad — it is a brand reel with a dead link, and pretending otherwise
            # would switch off the stockist rule on no evidence at all.
            _product_ad = bool(_hf.get("images"))
            if _hf.get("name") and not featured_product:
                featured_product = _hf["name"]
                logger.info("[reel] the film is about the pasted product: %s",
                            featured_product[:70])
        except Exception:  # noqa: BLE001 — an unnamed hero still gets its packshot in the insert
            pass

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
            featured_product=featured_product, product_ad=_product_ad,
            photo_subjects=photo_subjects, critic_caller=_critic)
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
            # AND THE GRADE THAT MATCHES IT, resolved from the SAME dna and profile in the SAME
            # place. `reel/grade.py` opens by saying why: "if the two ever disagreed, a film would
            # be shot one way and graded another." It has been complete, tested and unreachable
            # since it was written — no production module imported it, so every clip shipped
            # exactly as the generator returned it. MEASURED across 48 rendered reels, per shot
            # with cuts detected and the end-card excluded: median luma spread between shots of
            # the same film = 90 of 255, sigma_luma = 35.
            try:
                from reel.grade import grade_filter
                _grade = grade_filter(brand_dna=_dna, profile=profile)
            except Exception:  # noqa: BLE001 — a film with no grade beats a render that dies
                _grade = ""
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
                                           look=locals().get("_look", "") or "",
                                           hero_product_url=hero_product_url)

    # THE COPY IS MEASURED AGAINST THE FORMAT — it no longer bends the format to fit itself.
    #
    # This block used to SHRINK the film to `speech + 2s` (owner, twice: «الكلام خلص من 10 ثواني
    # والفيديو مكمل»). It was the right instinct against the wrong cause. The film outlived its
    # narration because nothing owned the delivered length, so the only lever anyone had was to
    # cut the picture down to whatever the copy happened to be — which is how a fifteen-second
    # format shipped at 9.40s and 11.00s (MEASURED, `web_reel_f3815e3b` and `alfouad_v2_rev`).
    #
    # `fit_to_runtime` below now owns the length, and it is the length the owner asked for. So the
    # shortfall is REPORTED rather than absorbed: a film whose script does not fill its format is a
    # WRITING problem, and shortening the ad is not this function's decision to make. The two
    # defects that produced the original complaint are both closed elsewhere — the picture now
    # CUTS instead of holding (`reel/cinematic.py`) and the sound bed carries the whole film
    # including the logo (`reel/runtime.py`), so a beat without narration is scored, not silent.
    if with_voiceover:
        _lines = [(s_.voiceover or "").strip() for s_ in creative.scenes]
        _speech, _note = narration_fit(
            sum(len(l.split()) for l in _lines if l),
            sum(1 for l in _lines if l),
            picture_budget_s(max_seconds, with_endcard=include_logo))
        if _note:
            print(f"[reel] narration: {_note}", file=__import__("sys").stderr)
        elif _speech:
            print(f"[reel] narration ~{_speech:.1f}s over a "
                  f"{picture_budget_s(max_seconds, with_endcard=include_logo):.1f}s picture")

    # THE BEAT GRID — and this must be the LAST thing that touches a duration.
    #
    # The dead-tail fit above multiplies every scene by a scalar, and a scaled beat multiple is not
    # a beat multiple. Quantising before it would be silently undone; quantising here is the only
    # correct order. See `reel/tempo.py` for why the tempo is DERIVED (we synthesize the bed, so we
    # are the composer) and why cumulative starts are snapped rather than individual lengths.
    #
    # The floors are the legibility contracts the owner has rejected a reel over twice — a caption
    # must stay long enough to READ and a line long enough to SAY. They win; the grid may only ever
    # round UP against them.
    _beat = None
    try:
        from reel.insert_shot import INSERT_S
        from reel.tempo import beat_grid_for_brand
        _beat = beat_grid_for_brand(brand_dna=locals().get("_dna"), profile=profile)
        # THE FLOORS ARE LEGIBILITY, NOT PURCHASE SIZE. The video model's 4-second minimum governs
        # what we BUY (`video_provider.py` ceils to 4/6/8) and says nothing about what we CUT —
        # that separation is the whole point of buying coverage. Using 4.0 here re-coupled them and
        # undid the dead-tail fit: a film correctly shortened to 8.8s to match its narration was
        # pushed back to 12.6s of silence at the end.
        #
        # What may not be broken is what the owner has rejected a reel over twice: a line must have
        # time to be SAID and a caption time to be READ.
        _floors = []
        for _i, _sc in enumerate(storyboard.scenes):
            _vo = (creative.scenes[_i].voiceover or "").strip() if _i < len(creative.scenes) else ""
            _cap = (_sc.headline or _sc.cta_text or "")
            _floors.append(max(
                0.3 + len(_vo.split()) / 2.2 if _vo else 0.0,
                1.5 + 0.35 * len(_cap.split()) if _cap else 0.0,
                INSERT_S if _sc.insert_product_urls else 1.0,   # the schema's own lower bound
            ))
        _before = [s_.duration_s for s_ in storyboard.scenes]
        # THE RUNTIME IS A NUMBER THE FILM LANDS ON, not one it starts from.
        #
        # `quantise` alone may only ever round UP against the legibility floors, so the film grew
        # past its format on every run — and the end card was never in the budget at all, so what
        # shipped was `budget + 2.1s`. MEASURED across four delivered reels: 15.30 / 16.69 / 11.00 /
        # 9.40, for a format the owner names as fifteen seconds. `fit_to_runtime` keeps the same
        # precedence (floors > total > beat) and reconciles the total instead of ignoring it; see
        # `reel/runtime.py`. A film that cannot be fitted says so rather than running long.
        _budget = picture_budget_s(max_seconds, with_endcard=include_logo)
        _after, _fit_note = fit_to_runtime(_before, _floors, _beat.beat_s, _budget)
        for _sc, _d in zip(storyboard.scenes, _after):
            _sc.duration_s = _d
        storyboard.total_duration_s = round(sum(_after), 3)
        print(f"[reel] beat grid: {_beat.reason}, beat {_beat.beat_s:.3f}s — "
              f"cuts quantised, {sum(_before):.2f}s -> {sum(_after):.2f}s "
              f"(picture budget {_budget:.2f}s"
              + (f" + {endcard_tail_s():.1f}s end card = {max_seconds:.1f}s delivered"
                 if include_logo else " , no end card")
              + ")")
        if _fit_note:
            print(f"[reel] runtime: {_fit_note}", file=__import__("sys").stderr)
    except Exception as _e:  # noqa: BLE001 — an unquantised film beats a render that dies
        import sys as _sys
        print(f"[reel] beat grid unavailable ({type(_e).__name__}: {_e}) — cuts NOT quantised",
              file=_sys.stderr)
        _beat = None

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
        # THE REFERENCE MUST BE THE PRODUCT, not the first photo in the reel. `content_images[0]`
        # is whatever the curator ranked first — an interior, a counter, a member of staff. Asking
        # a vision model "is this the product?" against a picture of the shop is a check that can
        # only answer a different question, and it passed everything.
        #
        # When the owner pastes a product link we now hold its actual packshot, so the fidelity
        # check compares the film against the bottle. Without a link the old seed still stands:
        # a weak reference beats no QA at all.
        seed_url = None
        if hero_product_url:
            try:
                from .insert_shot import hero_product_facts
                _imgs = hero_product_facts(profile, hero_product_url).get("images") or []
                seed_url = _imgs[0] if _imgs else None
            except Exception:  # noqa: BLE001
                seed_url = None
        if not seed_url:
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
    # THE SYNTH BED IS OFF (owner, 2026-08-02: «امسح صوت veo لانه بشع… واظبط صوت 11لاب»).
    #
    # MEASURED before touching anything: Veo's own audio never reaches the file — the per-scene
    # normalise maps `[v]` only, and the mux takes `0:v` from a concat that has no audio track at
    # all. So the machine sound she heard could only be OURS: `synth_sound_bed`, an ffmpeg-
    # generated whoosh/pulse/chime bed. Naming it Veo was the wrong suspect; the sound was real.
    #
    # The film now carries the ElevenLabs voice alone. A silent bed is not a missing feature —
    # a real commercial is voice over picture, and a synthesised whoosh on every cut is the
    # single loudest tell that nobody scored this film.
    # THE EFFECTS ARE REAL NOW (owner, 2026-08-03: «استعمل كل قوة 11LAB… حتى لو عايز تعمل أصوات
    # الرش، كل ده 11 لاب»). The ffmpeg synth bed stays off — it is what she called «بشع» — and the
    # director's own `sfx` lines are fetched from ElevenLabs `/v1/sound-generation` instead.
    # MEASURED before wiring: POST returns 200 audio/mpeg on this account.
    #
    # Nothing generic is invented: an effect exists only because a SCENE asked for a sound that
    # happens in it, which is the difference between scoring a film and putting music under it.
    if music_path is None:
        try:
            from .sfx_eleven import available as _sfx_ok, bed_from_beats
            if _sfx_ok():
                # THE CUE TIMES MUST BE ON THE DELIVERED TIMELINE, not on the sum of the scenes.
                # `compositor._crossfade` OVERLAPS each join by `_SWEEP_S`, so on the file that
                # ships, scene i starts `i * _SWEEP_S` EARLIER than its cumulative duration and the
                # film is `(N-1) * _SWEEP_S` shorter overall. Laying effects on the raw sums put
                # every spray progressively later than the hand that pressed the nozzle — and the
                # last cue past the final frame, where `bed_from_beats` drops it. Same arithmetic
                # as the compositor, read from the same constant, so the two cannot drift apart.
                from .compositor import _SWEEP_S
                _starts, _acc = [], 0.0
                for _i, _s in enumerate(storyboard.scenes):
                    _starts.append(max(0.0, _acc - _i * _SWEEP_S))
                    _acc += float(getattr(_s, "duration_s", 0.0) or 0.0)
                _acc = max(0.1, _acc - max(0, len(storyboard.scenes) - 1) * _SWEEP_S)
                _cues = [(_starts[i], (creative.scenes[i].sfx or "").strip())
                         for i in range(min(len(_starts), len(creative.scenes)))
                         if (creative.scenes[i].sfx or "").strip()]
                if _cues:
                    _b = bed_from_beats(_cues, Path(out_path).with_suffix(".sfx.m4a"),
                                        total_s=_acc,
                                        work_dir=Path(out_path).parent / "_sfx")
                    if _b:
                        music_path = _b
                        logger.info("[reel] %d ElevenLabs effect(s) laid on the timeline",
                                    len(_cues))
        except Exception:  # noqa: BLE001 — a quieter reel, never a failed one
            logger.exception("[reel] ElevenLabs effects unavailable; continuing without them")

    _BED_OFF = True
    if music_path is None and not _BED_OFF:
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
            # `starts` are already ON the grid (they are cumulative sums of the quantised
            # durations), so the whooshes land on the beat with no extra arithmetic — and the
            # pulse below gives the listener the grid those cuts are landing on. Cutting to a
            # silence is a claim; cutting to something audible is an edit.
            # THE LENGTH THE FILM ACTUALLY IS, not the length a field remembers.
            #
            # This read `storyboard.total_duration_s or acc` — a STORED field, where `acc` two
            # lines above is the sum of the very durations the compositor is about to cut to. When
            # the two disagree the bed is written for a film that does not exist.
            #
            # MEASURED 2026-08-02 on the Al Fouad reel: the picture ran 13.03s and the audio 15.46s
            # — the owner heard it as «الصوت خلص بدري» and the last 3.29 seconds were dead silence
            # over a frozen frame. `acc` is Σ scene durations by construction, so it cannot drift
            # from what gets rendered; the stored total can, and did.
            # THE MUSIC HAS TO SURVIVE THE LOGO. `append_endcard_to_reel` feeds the card an
            # `anullsrc` and cross-fades the reel's audio into it, so the last beat of every film
            # this pipeline has ever shipped is SILENCE under a static card. MEASURED: alfouad_run3
            # goes quiet at 12.73s of 15.30s, nanocare_film at 10.40s of 16.69s — which is what the
            # owner heard as «الصوت خلص بدري».
            #
            # A commercial resolves the other way round: the track lands ON the end frame, and the
            # logo arrives with the last beat. So the bed is written LONG ENOUGH TO COVER THE CARD,
            # and the card is given that tail instead of silence.
            _tail = endcard_tail_s() if include_logo else 0.0
            bed = synth_sound_bed(Path(out_path).with_suffix(".bed.m4a"),
                                  (acc or storyboard.total_duration_s) + _tail,
                                  cut_times=starts[1:], mood=mood, sfx_beats=sfx_beats,
                                  bpm=(_beat.bpm if _beat else None))
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
        # The music's fade belongs at the END OF THE DELIVERED FILM, not at the end of the
        # PICTURE — otherwise it dips out just before the card it is supposed to land on.
        music_tail_s=(endcard_tail_s() if include_logo else 0.0),
        # THE SAME PERSON IN EVERY SHOT. `_face` is the brand's SELECTED presenter, already
        # resolved above for the seed generator — it was reaching the stills and stopping there,
        # so Veo re-invented the protagonist from the prompt on every clip. `locals().get` for the
        # same reason as `_look` and `_grade`: REEL_SEED_MODE=real skips the block that binds it,
        # and no presenter is an honest outcome rather than a guessed one.
        identity_refs=([locals().get("_face")] if locals().get("_face") else None),
        # `locals().get` because `_grade` is bound inside the generated-seeds block, exactly like
        # `_look` above it — REEL_SEED_MODE=real skips that block entirely and there is then no
        # brand grade to apply, which is the honest outcome rather than a guessed one.
        grade=locals().get("_grade", "") or "",
    )
    return result, creative
