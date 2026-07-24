"""Generate a brand reel: UNDERSTAND the brand -> INVENT fresh footage -> REAL motion.

Owner's direction: don't dump the website's product photos; UNDERSTAND the brand and INVENT
on-brand footage that tells its STORY. Pipeline:
  BrandCreativeDNA (the brand's real ads via search — used as LEARNED TEXT: themes/mood,
      never the ad pixels, which baked garbled text/colour)
   -> an LLM director writes a brand-grounded STORY (narrative arc + ONE recurring protagonist
      + a short on-screen CAPTION per beat, gated by the Evidence Ledger: drop-to-grounded)
   -> text-to-image stills (Imagen; region + natural-skin + hard no-text guards)
   -> Veo 3.1 image-to-video animates EACH still (Ken Burns is the per-scene fallback)
   -> xfade assembly on the clips' ACTUAL durations.
Returns the reel + the captions timed on its timeline (for the kinetic overlay layer). Never
fabricates the FACTS (footage is text-free; captions are gated); raises if nothing generates.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, NamedTuple, Optional


class GeneratedReel(NamedTuple):
    """The assembled footage + the story captions timed on ITS timeline (for the overlay layer)."""
    path: Path
    caption_beats: list  # (text, t_in, t_out) tuples, in footage seconds
    # The story's FRESH Ledger-gated opener ("" -> the overlay falls back to the brief
    # headline, the old always-the-tagline behavior).
    hook: str = ""


_AR_RE = re.compile(r"[؀-ۿ]")


def _voiceover_clause(text: str, profile: dict, language: str = "auto",
                      narrator: str = "") -> str:
    """The spoken-audio instruction for one Veo 3.1 scene (the TrendPulse insight — Veo renders
    NATIVE speech from the prompt, so the reel talks at no extra cost). The dialect is derived
    UNIVERSALLY from the copy language + the brand's ccTLD (no vertical/market hardcoding):
    Arabic + .eg -> Egyptian Arabic; Arabic elsewhere -> natural conversational Arabic. An
    explicit `language` override ("ar"/"en" — the owner's choice) forces the dialect even when
    the caption text alone is ambiguous. Empty text -> ambience only."""
    text = (text or "").strip()
    if not text:
        return "Natural ambient sound only, no speech."
    # ONE consistent narrator across every scene: without this Veo cast a random
    # voice PER CLIP — a male voice reading feminine-form lines, switching mid-reel
    # (the owner: "واحد بيتكلم بصيغة أنثى، كلام مش مترابط").
    voice = (narrator or "a warm, confident voice").strip().rstrip(".")
    continuity = " The SAME single narrator voice continues across all scenes of this video."
    if language == "en":
        return f'Voiceover: {voice}, speaking in English: "{text}".{continuity}'
    if language == "ar" or _AR_RE.search(text):
        # Region-aware, universally: the dialect follows the brand's EVIDENCED country
        # (poster.locale.arabic_dialect_for) — a Saudi brand reads Khaleeji, not a crude ".eg ->
        # Masri / else generic" split that gave every non-Egyptian Arab brand a flavourless read.
        try:
            from poster.locale import arabic_dialect_for
            dialect = arabic_dialect_for(profile if isinstance(profile, dict) else {})
        except Exception:
            dialect = "natural, warm conversational Arabic"
        return f'Voiceover: {voice}, speaking in {dialect}: "{text}".{continuity}'
    return f'Voiceover: {voice}, speaking in English: "{text}".{continuity}'


def _caption_windows(durs: list[float], transition_s: float = 0.5) -> list[tuple[float, float]]:
    """Pure: each clip's fully-visible window on the assembled xfade timeline (mirrors
    `build_animated_reel`'s math — clip i starts at sum(durs[:i]) - i*t). The window opens just
    after the incoming transition settles and closes before the outgoing one starts."""
    if not durs:
        return []
    t = min(transition_s, min(durs) * 0.5)
    wins: list[tuple[float, float]] = []
    for i, d in enumerate(durs):
        start = sum(durs[:i]) - i * t
        t_in = start + (0.3 if i else 0.2)
        t_out = start + d - t - 0.15
        wins.append((round(t_in, 2), round(max(t_in, t_out), 2)))
    return wins


def _scene_prompts(brief: Any, n: int) -> list[str]:
    """N brand-grounded, TEXT-FREE scene briefs for the generated stills — a hero moment, the real
    offerings, and an aspirational lifestyle beat. The STYLE reference carries the brand look; the
    image stays text-free (ImagenEditProvider appends the no-text contract)."""
    base = f"A premium, photorealistic commercial scene for {brief.business_name}"
    # VARIED human + city moments (not every shot a person staring at a blank phone). The brand
    # vibe comes from the people + city energy + the persistent logo overlay, NOT a literal screen.
    prompts = [
        f"{base}: a confident hero portrait of a real person, genuine expression, natural light.",
        f"{base}: friends or family together, authentically connected and happy, a warm real moment.",
        f"{base}: a person out in a vibrant modern city street of the brand's own country, dynamic urban energy.",
        f"{base}: a relaxed aspirational lifestyle moment at home or a cafe, real and unposed.",
        f"{base}: a cinematic wide establishing shot of a modern city skyline at golden hour.",
    ]
    out: list[str] = []
    i = 0
    while len(out) < max(2, n):
        out.append(prompts[i % len(prompts)])
        i += 1
    return out


def _dna_refs(profile: dict, caller: Any, brand_dna: Any) -> list[str]:
    """The brand's REAL ad references (from BrandCreativeDNA). Uses a supplied/cached DNA, else
    builds with caller=None (harvest + the tiered attribution filter — no vision cost needed just
    to get the reference URLs)."""
    refs = list(getattr(brand_dna, "references_seen", []) or []) if brand_dna is not None else []
    if refs:
        return refs
    try:
        from brand.creative_dna import build_creative_dna
        dna = build_creative_dna(profile, caller=None)   # refs only -> no Gemini-vision spend
        return list(getattr(dna, "references_seen", []) or [])
    except Exception:
        return []


def _onbrand_context(profile: dict, brief: Any) -> str:
    """On-brand, TEXT-FREE context for text-to-image: brand region (from the ccTLD) + the brand
    palette + a HARD no-text clause. With no ad references there is no text to copy, so the scene
    stays clean while the prompt keeps it on-brand."""
    url = ""
    # FIX-D5: evidence-derived country (was: a ".eg"-only branch hardcoding Egypt —
    # anti-universal and blind to every other market).
    region = ""
    try:
        from poster.locale import country_of
        _country = country_of(profile)
        if _country:
            region = (f"Set in {_country} with authentic local people and real {_country} "
                      "surroundings — NOT Western, NOT Gulf/Khaleeji. ")
    except Exception:  # noqa: BLE001
        region = ""
    # DELIBERATELY no brand-palette instruction: telling the model to use the (purple) brand
    # colour produced a monochromatic purple DYE over people + scene ("purple-people"). Brand
    # identity comes from the persistent LOGO overlay, NOT from tinting the footage.
    return (
        "Natural, vibrant, TRUE-TO-LIFE colour like a real professional photograph: WARM realistic "
        "human SKIN TONES, full natural colour contrast and depth, clean soft daylight. CRITICAL — "
        "do NOT apply ANY single-colour tint, duotone, monochrome wash, colour grade or coloured-gel "
        "lighting to the people or the scene; NO purple, green or neon cast on skin or environment; "
        "every person and object keeps its OWN natural colour. " + region +
        "If a phone or screen appears it is incidental, held naturally, with only a soft natural "
        "glow — never a blank white screen. ABSOLUTELY NO text, words, letters, numbers, logos, "
        "signage, watermarks or typography anywhere — a clean, text-free photograph."
    )


def build_brand_generated_reel(
    profile: dict, *, caller: Any = None, out_path: str | Path, brand_dna: Any = None,
    n_scenes: int = 5, music_path: Optional[str] = None, width: int = 1080, height: int = 1920,
    language: str = "auto",
    log=print,
) -> GeneratedReel:
    """Generate fresh, on-brand, TEXT-FREE scenes (text-to-image) then motion-animate them.
    NOTE: STYLE-conditioning on the brand's real ADS was DROPPED — Imagen reproduced the ads'
    text-heavy look as GARBLED baked text (the owner's "الكلام معكوس / عبث"). Brand fidelity now
    comes from the PROMPT (region + the brand's real offerings), and the image stays text-free.
    Returns GeneratedReel(path, caption_beats) — the caption beats are the story's GROUNDED
    on-screen lines timed on the footage timeline, for the kinetic overlay layer; raises only
    if no scene could be generated."""
    from poster.from_profile import build_poster_brief
    from poster.imagen_provider import VertexImagenProvider
    from reel.art_director import build_brand_story
    from reel.motion import _clip_duration, _grid_durations, _make_clip, build_animated_reel
    from reel.video_provider import VeoProvider

    brief = build_poster_brief(profile)
    # The reel must EXPRESS the brand (the owner: "هو حكاية بتعبّر عن البراند، مش صور ورا بعض").
    # An LLM director crafts a brand-grounded STORY (narrative arc + recurring character) from the
    # real persona — like the poster understands the brand. Resolve a default caller so the web
    # reel gets it too; fall back to deterministic varied scenes when no LLM is available.
    if caller is None:
        try:
            from business_profile.llm.caller import default_caller
            caller = default_caller(strong=True)
        except Exception:
            caller = None
    # GROUND THE STORY IN THE BRAND'S REAL CREATIVE (owner: "اعتمد ع الريلز القديمة بالسيرش"): the
    # BrandCreativeDNA searches the brand's actual ads and vision-UNDERSTANDS their visual language
    # (themes/mood/imagery — as TEXT), so the story reflects the real brand world. We feed only the
    # learned DESCRIPTION into the prompt, never the ad pixels (which baked garbled text/colour).
    if brand_dna is None and caller is not None:
        try:
            from poster.pipeline import load_or_build_dna
            brand_dna = load_or_build_dna(profile, caller)
            if brand_dna is not None:
                log(f"[gen] brand DNA loaded ({len(getattr(brand_dna, 'references_seen', []) or [])} real ads)")
        except Exception:
            brand_dna = None
    character, story, captions, voiceovers, hook, narrator = build_brand_story(
        brief, profile, caller, n=n_scenes, brand_dna=brand_dna, language=language)
    # Captions, voiceovers AND the hook are LLM copy on CUSTOMER-FACING surfaces (displayed /
    # SPOKEN) -> gate all (drop-to-grounded): a line carrying an unsourced falsifiable claim is
    # BLANKED (no caption / ambience instead of speech / hook falls back to the brief headline).
    from reel.grounding import grounded_captions
    for label, lines in (("caption", captions), ("voiceover", voiceovers)):
        if any(lines):
            kept = grounded_captions(profile, lines)
            blanked = sum(1 for a, b in zip(lines, kept) if a and not b)
            if blanked:
                log(f"[gen] {blanked} {label}(s) blanked (unsourced hard claim)")
            lines[:] = kept
    if hook:
        hook = (grounded_captions(profile, [hook])[0] or "").strip()
    cont = f" The SAME recurring person appears in this scene: {character}." if character else ""
    if story:
        scene_prompts = [f"{s}{cont}" for s in story][:n_scenes]
        captions = (captions + [""] * len(scene_prompts))[: len(scene_prompts)]
        voiceovers = (voiceovers + [""] * len(scene_prompts))[: len(scene_prompts)]
        log(f"[gen] brand STORY: {len(scene_prompts)} scenes"
            + (" + recurring character" if character else "")
            + (f" + {sum(1 for c in captions if c)} captions" if any(captions) else "")
            + (f" + {sum(1 for v in voiceovers if v)} voiceover lines" if any(voiceovers) else ""))
    else:
        scene_prompts = _scene_prompts(brief, n_scenes)
        captions = [""] * len(scene_prompts)
        voiceovers = [""] * len(scene_prompts)
        log(f"[gen] no LLM story -> {len(scene_prompts)} deterministic varied scenes")

    out_path = Path(out_path)
    work = out_path.parent / "_genscenes"
    work.mkdir(parents=True, exist_ok=True)
    provider = VertexImagenProvider()
    ctx = _onbrand_context(profile, brief)
    # The brand's learned VISUAL LANGUAGE must reach the IMAGE prompts too — it only
    # steered the story text before, so the stills came out generically cinematic
    # (owner: "هوية البراند مش ظاهرة"). THEMES/imagery only, never colour fields —
    # a colour instruction is what produced the purple-dye people (documented).
    dna_style = ""
    if brand_dna is not None:
        bits = [str(getattr(brand_dna, f, "") or "").strip()
                for f in ("imagery_style", "motifs")]
        bits = [b if isinstance(b, str) else ", ".join(map(str, b)) for b in bits if b]
        if bits:
            dna_style = ("\nThe brand's visual world (match its themes and imagery, "
                         "NOT its colours or any text): " + "; ".join(bits)[:400])
    stills: list[tuple[int, str]] = []       # (scene index, still path) — a failed scene must not
    for i, prompt in enumerate(scene_prompts):  # shift the later scenes off their story/caption
        try:
            p = provider.generate(f"{prompt}{dna_style}\n{ctx}", out_dir=str(work),
                                  aspect_ratio="9:16")
        except Exception as exc:  # noqa: BLE001 — skip a failed scene, keep the rest
            log(f"[gen] scene {i + 1} failed: {type(exc).__name__}: {exc}")
            continue
        # STAGE A — SEED GATE (owner directive 2026-07-12): judge the still BEFORE any Veo
        # spend. Fail -> regenerate the IMAGE once (cents); still bad -> DROP the scene —
        # never animate a bad seed into dollars.
        from reel.scene_qa import check_seed_frame, seed_may_animate
        v = check_seed_frame(p, caller=caller, brand_hint=brief.business_name or "")
        # FAIL-CLOSED (P-21, matching seed_gen.py:172): only an explicit PASS animates. A
        # NOT_CHECKED verdict (no caller / unreadable / call failure -> overall_pass=None) must
        # NOT spend Veo money — it regenerates once, then DROPS, exactly like a judged failure.
        if not seed_may_animate(v):
            log(f"[gen] scene {i + 1} seed not a clean PASS ({v.reason}); regenerating the image once")
            try:
                p = provider.generate(
                    f"{prompt}{dna_style}\n{ctx}\nNatural anatomy, realistic photographic "
                    "light, one clean focal point.", out_dir=str(work), aspect_ratio="9:16")
            except Exception:  # noqa: BLE001
                p = None
            v2 = check_seed_frame(p, caller=caller,
                                  brand_hint=brief.business_name or "") if p else None
            if p is None or not seed_may_animate(v2):
                log(f"[gen] scene {i + 1} seed still not a clean PASS -> scene DROPPED "
                    "(never animate an unvetted seed)")
                continue
        stills.append((i, str(p)))
        log(f"[gen] scene {i + 1}/{n_scenes} ok")
    if not stills:
        raise RuntimeError("build_brand_generated_reel: no scene could be generated")

    # PRIMARY PATH: animate each Imagen still into REAL video with Veo 3.1 (image-to-video), so the
    # reel MOVES (a real short film, not a slideshow). Ken Burns (static zoom) is ONLY the per-scene
    # fallback when Veo fails. The still is passed as the i2v reference so the motion is anchored to
    # the exact on-brand scene; the story action steers the motion.
    veo = VeoProvider()
    durs = _grid_durations(len(stills))
    clips: list[str] = []
    veo_ok = 0
    audio_verdicts: list = []   # per-clip transcription-gate records (exported on the audit)
    for j, (si, still) in enumerate(stills):
        clip = work / f"_scene_clip{j}.mp4"
        d = durs[j] if j < len(durs) else 5.0
        veo_prompt = ((story[si] + " " if (story and si < len(story)) else "")
                      + "Gentle, natural cinematic motion — the person and scene move naturally, "
                      "subtle camera move; photoreal, absolutely NO text, letters or logos. "
                      + _voiceover_clause(voiceovers[si] if si < len(voiceovers) else "", profile,
                                          language=language, narrator=narrator))
        try:
            veo.generate(veo_prompt, out_path=clip, duration_s=d, width=width, height=height,
                         reference_image=still)
            veo_ok += 1
            log(f"[veo] scene {si + 1}/{n_scenes} -> REAL video")
            # AUDIO GATE (roadmap Phase-1 #1): Veo SPEAKS natively on this path and
            # build_animated_reel keeps its audio — verify what was actually SAID against
            # the Ledger; an unsourced spoken hard claim MUTES the clip (fail-closed:
            # visuals survive, rogue speech never ships).
            from reel.audit import gate_clip_audio
            audio_verdicts.append({"scene": si,
                                   **gate_clip_audio(clip, profile, caller, log=log)})
        except Exception as exc:  # noqa: BLE001 — Veo failed this scene: Ken Burns fallback only
            log(f"[veo] scene {si + 1} FAILED ({type(exc).__name__}: {str(exc)[:90]}) -> Ken Burns")
            _make_clip(Path(still), clip, move="in" if j % 2 == 0 else "out",
                       duration_s=d, width=width, height=height)
        clips.append(str(clip))
    log(f"[gen] Veo real video: {veo_ok}/{len(stills)} scenes; assembling reel")
    # Time each scene's caption on the ASSEMBLED footage timeline (actual clip durations, same
    # xfade math as build_animated_reel) BEFORE the per-scene clips are deleted.
    actual = [_clip_duration(Path(c)) for c in clips]
    wins = _caption_windows(actual)
    caption_beats: list[tuple[str, float, float]] = []
    for j, (si, _still) in enumerate(stills):
        text = captions[si] if si < len(captions) else ""
        if text and j < len(wins):
            caption_beats.append((text, wins[j][0], wins[j][1]))
    # keep_audio: Veo 3.1's NATIVE audio (the gated spoken voiceover + ambience) survives the
    # assembly — the reel TALKS; silent fallback clips are padded so the audio chain holds.
    reel = build_animated_reel(clips, out_path, width=width, height=height, keep_audio=True)
    # per-asset trail (poster parity): coverage + the audio-gate verdicts, next to the reel
    from reel.audit import build_reel_audit, write_reel_audit
    write_reel_audit(build_reel_audit(
        profile, audio_verdicts=audio_verdicts,
        audio_surface="generated-path: Veo native speech — transcription-gated "
                      "(an unsourced spoken claim mutes its clip)"), reel)
    for c in clips:                                    # tidy the per-scene clips
        try:
            Path(c).unlink()
        except OSError:
            pass
    return GeneratedReel(reel, caption_beats, hook)
