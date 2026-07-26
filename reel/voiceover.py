"""Voice-over track for the reel (OpenAI TTS).

Turns the creative director's per-scene narration lines into ONE audio track timed
to the scene durations: each line is synthesized, then padded with silence (or
trimmed) to fill its scene, and the segments are concatenated so the narration
lands in sync with the visuals. No key/SDK -> None (the reel just renders silent).
"""
from __future__ import annotations

import logging
import os
import re
import subprocess
from pathlib import Path
from typing import Any, Optional

from .ffmpeg_tools import ffmpeg_exe, run_ffmpeg

logger = logging.getLogger(__name__)

# OpenAI TTS. gpt-4o-mini-tts accepts an `instructions` param to STEER the
# delivery (accent/emotion/pace) — so we can ask for an Egyptian-Arabic read. The
# underlying voices are English-leaning, so this approximates the dialect, it is not
# a native ar-EG voice; for that, wire Azure ar-EG (Salma/Shakir) or ElevenLabs.
# Override via REEL_TTS_MODEL / REEL_TTS_VOICE / REEL_TTS_INSTRUCTIONS.
# Default to gpt-audio-1.5 — OpenAI's newest CONVERSATIONAL audio model. It sounds
# noticeably more HUMAN than the gpt-4o-mini-tts speech endpoint and is steered with
# a full system prompt (so we can push hard for a natural Egyptian read). It runs via
# chat.completions (modalities=audio), not /audio/speech. 'marin' is the newest voice.
# Honest ceiling: still not a NATIVE ar-EG human — for that wire ElevenLabs / Azure
# (Salma/Shakir). Override via REEL_TTS_MODEL / REEL_TTS_VOICE / REEL_TTS_INSTRUCTIONS.
_DEFAULT_MODEL = "gpt-audio-1.5"
_DEFAULT_VOICE = "marin"
_SPEECH_VOICE = "onyx"          # valid /audio/speech voice for the fallback path

# Arabic-presentation detection -> Egyptian-accent instruction.
_AR_RE = re.compile(r"[؀-ۿݐ-ݿﭐ-﷿ﹰ-﻿]")


def _instructions_for(lines: list[str], delivery: "str | list[str] | None" = "",
                      tone: str = "", dialect: str = "") -> str:
    """Performance brief for the narration. ONE consistent narrator, warm and expressive but
    controlled — never flat/robotic and never a news reader. Adapts to the brand TONE (a
    luxury brand reads refined & unhurried; a playful one reads bright & energetic) so the
    voice fits the brand instead of a hardcoded 'appetizing food ad' (that was a vertical
    leak — wrong for jewelry, clinics, telecom …). `tone` comes from the brand profile.

    `delivery` may be the director's PER-LINE list — the reel's emotional ARC. The old code
    kept only the FIRST entry, so a frustrated hook directed the whole film (owner, 2026-07-12:
    'الاحساس راح'); now every line carries its own direction, in one single-call performance."""
    env = os.environ.get("REEL_TTS_INSTRUCTIONS")
    if env:
        base = env
    else:
        t = (tone or "").lower()
        if any(k in t for k in ("luxury", "premium", "elegant", "refined", "sophisticat")):
            mood = ("refined, warm and UNHURRIED — the poised confidence of a luxury house; "
                    "let each phrase breathe, intimate and aspirational, never rushed or salesy")
        elif any(k in t for k in ("playful", "fun", "bold", "energetic", "youth", "vibrant")):
            mood = ("bright, upbeat and full of energy — genuinely excited and friendly; "
                    "let your pitch rise, smile through the words")
        else:
            mood = ("warm, confident and human — real emotion and gentle energy; "
                    "vary pace and pitch, lean into the key words, sound alive not flat")
        # OWNER-RATIFIED HYPE (2026-07-26 listen test, sample D): when the DIRECTOR's own
        # deliveries lean high-energy, the polite-narrator brief is the wrong register — the
        # approved read is street-smart ad hype. Luxury brands keep their poise regardless.
        _dl = delivery if isinstance(delivery, list) else ([delivery] if delivery else [])
        _highs = sum(1 for d in _dl if _energy_of(str(d)) == "high")
        if _dl and _highs * 2 >= len(_dl) and not any(
                k in t for k in ("luxury", "premium", "elegant", "refined", "sophisticat")):
            mood = ("street-smart AD HYPE: you just found something amazing and you are "
                    "telling your best friend — MAXIMUM energy and pace, big smile, punchy, "
                    "alive; an influencer ad read, NEVER a polite narrator, NEVER flat. The "
                    "hook TEASES, the middle stays confident and warm, the closing CTA "
                    "EXPLODES with urgency")
        is_ar = bool(_AR_RE.search(" ".join(lines or [])))
        # The dialect follows the brand's EVIDENCED region (poster.locale.arabic_dialect_for),
        # threaded in as `dialect`. NO hardcoded Egyptian: a Saudi/Gulf brand reads Khaleeji, a
        # Levantine brand reads Shami. With no region evidence, an honest neutral register (never
        # silently another region's dialect).
        if is_ar:
            dialect_clause = (f"Authentic {dialect}. " if dialect else
                              "Authentic Arabic in the audience's OWN regional register (never "
                              "defaulting to another region's dialect). ")
        else:
            dialect_clause = ""
        base = (
            "You are ONE consistent, professional human voice-over artist narrating a premium "
            f"brand film. Keep the SAME voice, character and energy from the first word to the "
            f"last — do NOT change persona between lines. {dialect_clause}Perform it {mood}. "
            "Never flat, never robotic, never a news anchor. PACE: measured and clear — a "
            "first-time listener must catch every word; never rush, even when energetic. "
            "PRONUNCIATION: any Latin brand or program name embedded in the Arabic (e.g. "
            "'HireReady', 'NTI') is pronounced as its natural ENGLISH word — one fluent word, "
            "never letter-by-letter spelling, never Arabized syllables."
        )
    if isinstance(delivery, (list, tuple)):
        arc = [f"{i}) {str(d).strip().rstrip('.')}" for i, d in enumerate(delivery, 1)
               if d and str(d).strip()]
        if arc:
            base += (" The script's sentences follow this emotional ARC, in order — perform "
                     "each sentence with its direction: " + "; ".join(arc) + ".")
    elif delivery:
        base += f" Emotional direction for this passage: {delivery}."
    return base


def _synth_one(client, text: str, out: Path, *, model: str, voice: str,
               instructions: str) -> bool:
    """One line -> mp3. gpt-audio* models go through chat.completions (audio
    modality, steered by a system prompt); tts/* models go through /audio/speech."""
    if model.startswith("gpt-audio"):
        import base64
        r = client.chat.completions.create(
            model=model, modalities=["text", "audio"],
            audio={"voice": voice, "format": "mp3"},
            messages=[{"role": "system", "content": instructions},
                      {"role": "user", "content": text}],
        )
        data = base64.b64decode(r.choices[0].message.audio.data)
        out.write_bytes(data)
    else:
        kw = {"instructions": instructions} if model == "gpt-4o-mini-tts" else {}
        client.audio.speech.create(model=model, voice=voice, input=text, **kw).stream_to_file(str(out))
    return out.is_file() and out.stat().st_size > 0


def _tts_segment(client, text: str, out: Path, *, model: str, voice: str,
                 instructions: str) -> bool:
    """Synthesize one line, trying the chosen (human) model first, then degrading to
    the steerable speech model, then plain tts-1. Returns False only if all fail."""
    text = (text or "").strip()
    if not text:
        return False
    chain = [(model, voice)]
    if not model.startswith("gpt-4o-mini-tts"):
        chain.append(("gpt-4o-mini-tts", _SPEECH_VOICE))
    chain.append(("tts-1", _SPEECH_VOICE))
    for mdl, vc in chain:
        try:
            if _synth_one(client, text, out, model=mdl, voice=vc, instructions=instructions):
                return True
        except Exception as e:  # noqa: BLE001
            logger.warning("tts segment failed (model=%s): %s", mdl, e)
    return False


# ---------------------------------------------------------------------
# Gemini TTS (runs on the SAME GCP/Vertex credits as Imagen/Veo — no OpenAI key
# needed). gemini-2.5-flash-preview-tts returns raw PCM (L16, mono); we wrap it in a
# WAV so the shared ffmpeg pad/trim path below handles it identically to OpenAI mp3s.
# ---------------------------------------------------------------------
_GEMINI_TTS_MODEL = "gemini-2.5-flash-preview-tts"
_GEMINI_VOICE = "Kore"          # a clear, warm prebuilt voice; multilingual (auto-detects Arabic)


def _gemini_client():
    """Vertex (GCP credits, ADC) or a Gemini API key. None if neither is available."""
    try:
        from google import genai
    except Exception:
        return None
    api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if api_key:
        return genai.Client(api_key=api_key)
    project = os.environ.get("GOOGLE_CLOUD_PROJECT")
    if project:
        return genai.Client(vertexai=True, project=project,
                            location=os.environ.get("GOOGLE_CLOUD_LOCATION", "us-central1"))
    return None


def _gemini_segment(client, text: str, out_wav: Path, *, voice: str, model: str,
                    instructions: str) -> bool:
    """One narration line -> WAV via Gemini TTS. The style brief is prepended to the
    text (Gemini TTS is steered by a natural-language directive in the content)."""
    text = (text or "").strip()
    if not text:
        return False
    from google.genai import types
    import re as _re
    import wave
    prompt = f"{instructions}\n\nLine to say: {text}" if instructions else text
    try:
        r = client.models.generate_content(
            model=model, contents=prompt,
            config=types.GenerateContentConfig(
                response_modalities=["AUDIO"],
                speech_config=types.SpeechConfig(voice_config=types.VoiceConfig(
                    prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=voice))),
            ),
        )
        parts = r.candidates[0].content.parts
        blob = next((p.inline_data for p in parts if getattr(p, "inline_data", None)), None)
        if not blob or not blob.data:
            return False
        rate = 24000
        m = _re.search(r"rate=(\d+)", getattr(blob, "mime_type", "") or "")
        if m:
            rate = int(m.group(1))
        with wave.open(str(out_wav), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(rate)
            w.writeframes(blob.data)
        return out_wav.is_file() and out_wav.stat().st_size > 44
    except Exception as e:  # noqa: BLE001
        logger.warning("gemini tts segment failed: %s", e)
        return False


# ---------------------------------------------------------------------
# edge-tts — FREE, keyless TTS (Microsoft Edge read-aloud). For Arabic it provides a
# NATIVE Egyptian voice (ar-EG-SalmaNeural / -ShakirNeural), which is both cheaper
# (zero cost) and more authentic for ar-EG reels than the English-leaning OpenAI
# voices. Online (Microsoft endpoint), no API key, outputs mp3. Pace/pitch can be
# steered (rate/pitch); there is no free-form 'instructions' prompt, so the emotional
# brief is not applied here — the native dialect carries the read.
# Override the voice via REEL_TTS_VOICE.
# ---------------------------------------------------------------------
_EDGE_VOICE_AR = "ar-EG-SalmaNeural"     # Egyptian Arabic, female (warm) — the pilot default
_EDGE_VOICE_EN = "en-US-AriaNeural"      # default English voice
_EDGE_RATE = os.environ.get("REEL_TTS_EDGE_RATE", "+0%")
_EDGE_PITCH = os.environ.get("REEL_TTS_EDGE_PITCH", "+0Hz")

# Native edge-tts Arabic voice PER dialect region (the FREE, keyless fallback). Keyed on the
# canonical bucket from poster.locale.arabic_region_of so a Saudi brand gets a Gulf voice, not
# ar-EG-Salma. Best-effort (Microsoft's standard per-locale neural voices); an unknown region
# degrades to the pilot Egyptian voice rather than going silent.
_EDGE_VOICE_BY_REGION = {
    "egypt": "ar-EG-SalmaNeural",
    "gulf": "ar-SA-ZariyahNeural",
    "levant": "ar-SY-AmanyNeural",
    "maghreb": "ar-MA-MounaNeural",
}


def _edge_voice_for(text: str, override: Optional[str], profile: Any = None) -> str:
    """Pick a native Arabic voice for Arabic copy — REGION-aware (the brand's evidenced country),
    not a hardcoded Egyptian voice — else an English voice. An explicit override always wins."""
    if override:
        return override
    if _AR_RE.search(text or ""):
        try:
            from poster.locale import arabic_region_of
            reg = arabic_region_of(profile)
        except Exception:  # noqa: BLE001
            reg = ""
        return _EDGE_VOICE_BY_REGION.get(reg, _EDGE_VOICE_AR)
    return _EDGE_VOICE_EN


def _edge_prosody_for_tone(tone: str) -> tuple[str, str]:
    """Tone-driven edge-tts rate/pitch so the FREE voice isn't a flat +0%/+0Hz monotone (owner:
    'unlistenable'). An explicit REEL_TTS_EDGE_RATE/PITCH override still wins."""
    if os.environ.get("REEL_TTS_EDGE_RATE") or os.environ.get("REEL_TTS_EDGE_PITCH"):
        return _EDGE_RATE, _EDGE_PITCH
    t = (tone or "").lower()
    if any(k in t for k in ("luxury", "premium", "elegant", "refined", "sophisticat")):
        return "-4%", "-1Hz"                     # unhurried, poised
    if any(k in t for k in ("playful", "energetic", "bold", "youth", "fun", "vibrant", "dynamic")):
        return "+9%", "+4Hz"                     # upbeat, energetic
    return "+4%", "+2Hz"                         # default: warm + a touch of lift (never flat)


def _edge_segment(text: str, out_mp3: Path, *, voice: str,
                  rate: Optional[str] = None, pitch: Optional[str] = None) -> bool:
    """One narration line -> mp3 via edge-tts (free). The lib is async, so we drive it
    with asyncio.run (the reel pipeline is synchronous). Never raises -> False on any
    failure (offline / endpoint error) so the caller degrades to silent filler."""
    text = (text or "").strip()
    if not text:
        return False
    try:
        import asyncio
        import edge_tts

        _rate = rate or _EDGE_RATE
        _pitch = pitch or _EDGE_PITCH

        async def _run() -> None:
            await edge_tts.Communicate(
                text, voice, rate=_rate, pitch=_pitch
            ).save(str(out_mp3))

        asyncio.run(_run())
        return out_mp3.is_file() and out_mp3.stat().st_size > 0
    except Exception as e:  # noqa: BLE001
        logger.warning("edge tts segment failed: %s", e)
        return False


def narration_lines(storyboard) -> list[str]:
    """One grounded narration line PER scene — the verbatim text already shown on that
    scene (headline -> first subline -> CTA). Empty for scenes with no text (silent)."""
    out: list[str] = []
    for s in storyboard.scenes:
        txt = (s.headline or (s.sublines[0] if s.sublines else "") or s.cta_text or "")
        out.append(str(txt).strip())
    return out


_ELEVENLABS_MODEL = "eleven_multilingual_v2"
# Native ElevenLabs library voice ids PER dialect region (owner-supplied, 2026-07-18). Keyed on the
# canonical bucket from poster.locale.arabic_region_of so a Saudi brand narrates in a real Gulf voice
# instead of an Egyptian one. Maghreb / unknown -> "" (no id): a neutral MSA read, never another
# region's voice. REEL_ELEVENLABS_VOICE overrides all (a single pinned voice for every reel).
_ELEVENLABS_VOICE_BY_REGION = {
    "egypt": "QvNF0qyyt1Tuy1YAmnzH",
    "gulf": "t9akNmCDhz230CEXOYmn",
    "levant": "Wim44P0dU9HtjyzNnFsv",
}


def _elevenlabs_voice_for(profile: Any = None, override: Optional[str] = None) -> str:
    """The native ElevenLabs library voice id for the brand's evidenced region. Explicit override >
    REEL_ELEVENLABS_VOICE env > per-region map > '' (no id -> the segment degrades, as before)."""
    if override:
        return override
    env = os.environ.get("REEL_ELEVENLABS_VOICE")
    if env:
        return env
    try:
        from poster.locale import arabic_region_of
        return _ELEVENLABS_VOICE_BY_REGION.get(arabic_region_of(profile), "")
    except Exception:  # noqa: BLE001
        return ""


def _elevenlabs_segment(text: str, out_mp3: Path, *, voice: str, model: str,
                        api_key: str, timeout: float = 120.0, _urlopen=None) -> bool:
    """One TTS segment via ElevenLabs (native Egyptian library voices — needs a Creator-tier key).
    Stdlib urllib; returns True on a written mp3, False on any failure (caller degrades)."""
    import json as _json
    import urllib.error
    import urllib.request
    if not voice:
        logger.warning("elevenlabs: no voice id (set REEL_ELEVENLABS_VOICE); skipping")
        return False
    body = _json.dumps({
        "text": text, "model_id": model or _ELEVENLABS_MODEL,
        "voice_settings": {"stability": 0.5, "similarity_boost": 0.75, "style": 0.0},
    }).encode("utf-8")
    req = urllib.request.Request(
        f"https://api.elevenlabs.io/v1/text-to-speech/{voice}", data=body, method="POST",
        headers={"xi-api-key": api_key, "Content-Type": "application/json", "Accept": "audio/mpeg"})
    opener = _urlopen or urllib.request.urlopen
    try:
        with opener(req, timeout=timeout) as r:
            data = r.read()
        if data:
            Path(out_mp3).write_bytes(data)
            return Path(out_mp3).is_file()
    except urllib.error.HTTPError as e:
        # THE STATUS AND BODY ARE THE DIAGNOSIS. This logged only `type(e).__name__`, so the
        # 2026-07-25 Abu Auf run showed "HTTPError" and nothing else — indistinguishable between
        # wrong-tier (a library voice on a free key), a bad key, and quota. ElevenLabs puts the
        # cause in the JSON body (`detail.status`: voice_not_found / quota_exceeded / …).
        try:
            body = e.read()[:300].decode("utf-8", "replace")
        except Exception:  # noqa: BLE001
            body = ""
        logger.warning("elevenlabs TTS failed: HTTP %s %s :: %s", e.code, e.reason, body)
    except Exception as e:  # noqa: BLE001
        logger.warning("elevenlabs TTS failed: %s", type(e).__name__)
    return False


def _vocalize_for_tts(lines: "list[str]", *, caller: Any = None,
                      dialect: str = "") -> "list[str]":
    """«طريقة كتابة الكلام بتخلي الصوت يقوله صح» — the owner's engineer, quoted twice.

    A TTS voice reads UNVOCALIZED dialect Arabic with MSA guesses: wrong vowels, wrong
    stress, dead rhythm — measured straight on the v2/v3 samples the owner rejected. Each
    line is rewritten EXACTLY AS SPOKEN (full tashkeel on every word, phonetic dialect
    spelling, the same words in the same order) before ANY engine hears it.

    Fail-closed to the ORIGINAL lines: wrong line count (durations align per line), no
    Arabic, no caller, REEL_TTS_VOCALIZE=0, or any call failure."""
    parts = [str(l) for l in (lines or [])]
    if not parts or os.environ.get("REEL_TTS_VOCALIZE") == "0":
        return parts
    if not _AR_RE.search(" ".join(parts)):
        return parts
    if caller is None:
        try:
            from business_profile.llm import default_caller
            caller = default_caller(strong=False)
        except Exception:  # noqa: BLE001
            caller = None
    if caller is None:
        return parts

    class _Voc:
        _model = None

        @classmethod
        def get(cls):
            if cls._model is None:
                from pydantic import BaseModel

                class VocalizedLines(BaseModel):
                    lines: list[str]

                cls._model = VocalizedLines
            return cls._model

    reg = dialect.strip() or "Egyptian Arabic (مصري)"
    system = (
        "You are a voice-over SCRIPT PHONETICIAN. Rewrite every Arabic narration line below "
        f"EXACTLY as a native {reg} narrator pronounces it, so a TTS engine reads it right:\n"
        "- FULL tashkeel (فتحة/ضمة/كسرة/سكون/شدة/تنوين) on EVERY Arabic word.\n"
        "- Phonetic dialect spelling — write what is SAID (e.g. «أوي» not «قوي», «دِلْوَقْتِي»), "
        "never 'correct' the dialect into fusha.\n"
        # OWNER'S EAR (2026-07-26): the vocalizer wrote «عَوْف» and the voice said 'awf —
        # the brand is pronounced ʿŌF. Names follow the STREET, not morphology.
        "- PROPER NOUNS & BRAND NAMES are vocalized exactly as LOCALS say them: a name heard "
        "with a long ō/ū takes ضمة + واو ساكنة («أَبُو عُوف» — NEVER «عَوْف»), a long ē/ī takes "
        "كسرة + ياء ساكنة. When unsure, prefer the long-vowel reading over a diphthong.\n"
        "- KEEP the same words, the same order, the same count of lines, and every ! and ؟ "
        "mark — change ONLY the written form.\n"
        "Return the same number of lines."
    )
    try:
        resp, _u = caller(system, "\n".join(parts), _Voc.get(), group_name="tts_vocalize")
        out = [str(l).strip() for l in (getattr(resp, "lines", None) or [])]
        if len(out) == len(parts) and all(out):
            return out
    except Exception:  # noqa: BLE001 — the read must never die on a spelling pass
        logger.warning("tts vocalization failed; using the raw spelling")
    return parts


def _join_script(lines: "list[str]") -> str:
    """ONE continuous script whose EMOTIVE terminal marks survive the join.

    Measured root of the owner's «الصوت برود»: the old joiner rstrip'ed " ،.!؟?" from every
    line, so «متتنسيش!» reached the voice as flat «متتنسيش،» — the punctuation IS the
    performance direction for every TTS engine. Neutral tails (، .) still yield to one
    consistent cadence; ! and ؟/? stay."""
    parts = [str(l).strip() for l in (lines or []) if l and str(l).strip()]
    if not parts:
        return ""
    is_ar = bool(_AR_RE.search(" ".join(parts)))
    joiner = "،  " if is_ar else ".  "
    cores = [p.rstrip(" ،.") for p in parts]
    script = ""
    for j, core in enumerate(cores):
        if j:
            script += "  " if cores[j - 1].endswith(("!", "؟", "?")) else joiner
        script += core
    return script


# Delivery -> ENERGY class -> edge rate/pitch. The director writes a per-line performance
# note (`voiceover_delivery`); until v3 it reached only the PAID engines' instruction text,
# never the free voice that actually ships — the whole reel was one flat read.
_DELIVERY_PROSODY = {
    "high": ("+9%", "+16Hz"),      # excited hook / urgent CTA
    "warm": ("+2%", "+4Hz"),       # relaxed, smiling body
    "neutral": ("+5%", "+8Hz"),    # unspecified — lightly lifted, never monotone
}


def _energy_of(delivery: str) -> str:
    d = (delivery or "").lower()
    if any(w in d for w in ("excit", "high energy", "energetic", "urgent", "punchy", "upbeat",
                            "rising", "متحمس", "حماس", "سريع", "طاقة")):
        return "high"
    if any(w in d for w in ("calm", "soft", "gentle", "relaxed", "warm", "smile", "tender",
                            "هادي", "دافي", "ابتسامة", "حنية")):
        return "warm"
    return "neutral"


def _edge_read(parts: "list[str]", deliveries: "list[str]", out_mp3: Path, *,
               voice: str, tone: str = "") -> bool:
    """The free voice, read with the director's energy: consecutive lines that share an energy
    class form ONE group; each group gets its own rate/pitch; groups concat into one track.

    Uniform energy (or an explicit REEL_TTS_EDGE_RATE/PITCH override) collapses to the single
    continuous call — the design that fixed the Azza-demo "changing voice / mid-word cuts"
    bugs stays intact when there is nothing to vary."""
    energies = [_energy_of(deliveries[k] if k < len(deliveries) else "") for k in range(len(parts))]
    override = bool(os.environ.get("REEL_TTS_EDGE_RATE") or os.environ.get("REEL_TTS_EDGE_PITCH"))
    if override or len(set(energies)) <= 1:
        rate, pitch = (_edge_prosody_for_tone(tone) if override or not energies
                       else _DELIVERY_PROSODY[energies[0]])
        return _edge_segment(_join_script(parts), out_mp3, voice=voice, rate=rate, pitch=pitch)
    groups: list[tuple[str, list[str]]] = []
    for text_line, en in zip(parts, energies):
        if groups and groups[-1][0] == en:
            groups[-1][1].append(text_line)
        else:
            groups.append((en, [text_line]))
    seg_files: list[Path] = []
    for gi, (en, glines) in enumerate(groups):
        seg = out_mp3.parent / f"{out_mp3.stem}_g{gi}.mp3"
        rate, pitch = _DELIVERY_PROSODY[en]
        if not _edge_segment(_join_script(glines), seg, voice=voice, rate=rate, pitch=pitch):
            return False
        seg_files.append(seg)
    listing = out_mp3.parent / f"{out_mp3.stem}_concat.txt"
    listing.write_text("".join(f"file '{s.as_posix()}'\n" for s in seg_files), encoding="utf-8")
    try:
        run_ffmpeg(["-f", "concat", "-safe", "0", "-i", str(listing), "-c", "copy",
                    str(out_mp3)])
    except Exception:  # noqa: BLE001 — concat trouble must not silence the reel
        rate, pitch = _edge_prosody_for_tone(tone)
        return _edge_segment(_join_script(parts), out_mp3, voice=voice, rate=rate, pitch=pitch)
    return Path(out_mp3).is_file()


def _verbatik_ssml(text: str, *, voice: str = "", delivery: str = "") -> str:
    """The SSML body for Verbatik: the language follows the SCRIPT (D-8 — not a hardcoded
    ar-EG), and a delivery note becomes a real prosody contour so the paid read is not flat
    either. The voice itself travels in the X-Voice-ID header per Verbatik's API docs."""
    lang = "ar-EG" if _AR_RE.search(text or "") else "en-US"
    inner = _xml_escape(text)
    if (delivery or "").strip():
        rate, pitch = _DELIVERY_PROSODY[_energy_of(delivery)]
        inner = f'<prosody rate="{rate}" pitch="{pitch}">{inner}</prosody>'
    return f'<speak version="1.0" xml:lang="{lang}">{inner}</speak>'


def _verbatik_segment(text: str, out_mp3: Path, *, voice: str = "",
                      api_key: str = "", timeout: float = 120.0,
                      delivery: str = "", _urlopen=None) -> bool:
    """One TTS segment via Verbatik (the owner added VERBATIK_API_KEY for the reel voice,
    2026-07-25: «حطيت كي للصوت»). Bearer-token REST; SSML body; returns audio bytes.

    Endpoint and voice are env-overridable (`VERBATIK_TTS_URL`, `REEL_VERBATIK_VOICE`) because the
    vendor's surface is a fact about the vendor: if their path or voice ids shift, the fix is a
    variable, not a release. Default voice is an Egyptian-Arabic one for the same reason the edge
    fallback uses ar-EG — this project's brands are addressed in their own dialect.

    Same honesty rule as ElevenLabs above: an HTTP failure logs its status AND body."""
    import urllib.error
    import urllib.request
    key = api_key or os.environ.get("VERBATIK_API_KEY") or ""
    if not key:
        return False
    url = os.environ.get("VERBATIK_TTS_URL", "https://api.verbatik.com/api/v1/tts")
    voice = voice or os.environ.get("REEL_VERBATIK_VOICE", "salma-ar-eg")
    # Per Verbatik's published API: POST /api/v1/tts, Bearer auth, the VOICE in the
    # X-Voice-ID header (slug ids like jenny-en-us), SSML or plain text as the body.
    ssml = _verbatik_ssml(text, voice=voice, delivery=delivery)
    req = urllib.request.Request(
        url, data=ssml.encode("utf-8"), method="POST",
        headers={"Authorization": f"Bearer {key}", "X-Voice-ID": voice,
                 "Content-Type": "application/ssml+xml", "Accept": "audio/mpeg"})
    opener = _urlopen or urllib.request.urlopen
    try:
        with opener(req, timeout=timeout) as r:
            data = r.read()
        if data:
            Path(out_mp3).write_bytes(data)
            return Path(out_mp3).is_file()
    except urllib.error.HTTPError as e:
        try:
            body = e.read()[:300].decode("utf-8", "replace")
        except Exception:  # noqa: BLE001
            body = ""
        logger.warning("verbatik TTS failed: HTTP %s %s :: %s", e.code, e.reason, body)
    except Exception as e:  # noqa: BLE001
        logger.warning("verbatik TTS failed: %s", type(e).__name__)
    return False


def _xml_escape(s: str) -> str:
    return (str(s or "").replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def _resolve_backend(backend: Optional[str]) -> str:
    """gemini | openai | edge | elevenlabs. Explicit > REEL_TTS_BACKEND > auto.

    Auto prefers a configured PAID backend (Gemini on GCP credits, else OpenAI by key),
    and falls back to FREE, keyless `edge` when no paid backend is configured — so a
    reel always gets a voice instead of going silent. Set REEL_TTS_BACKEND=edge to force
    the free native-Egyptian voice and skip the paid TTS spend entirely."""
    b = (backend or os.environ.get("REEL_TTS_BACKEND") or "").lower()
    if b in ("gemini", "openai", "edge", "elevenlabs", "verbatik"):
        return b   # elevenlabs is EXPLICIT-ONLY (never auto-selected) — a paid native-EG library voice
    # Verbatik: the owner added its key FOR the reel voice (2026-07-25), so a present key means
    # "use it" — unlike ElevenLabs, whose library voices need a tier we cannot verify up front.
    if os.environ.get("VERBATIK_API_KEY"):
        return "verbatik"
    # OWNER-RATIFIED (2026-07-11 listen test): فصحى (fusha) -> Gemini Kore DIRECTED (variant 2);
    # عامية (masri) -> OpenAI gpt-audio (variant 4). The dialect itself is the USER's choice
    # (REEL_ARABIC_DIALECT / the studio UI); this only routes the ratified voice per register.
    try:
        from reel.art_director import arabic_dialect
        if arabic_dialect() == "masri" and os.environ.get("OPENAI_API_KEY"):
            return "openai"
    except Exception:  # noqa: BLE001
        pass
    if os.environ.get("GOOGLE_CLOUD_PROJECT") or os.environ.get("GEMINI_API_KEY"):
        return "gemini"
    if os.environ.get("OPENAI_API_KEY"):
        return "openai"
    return "edge"


def _fit_filter(raw_dur: float, total: float) -> str:
    """The ffmpeg audio filter that lays the read over the footage. HARD PACING RULE (owner,
    2026-07-12: the sped-up narration was unintelligible): atempo is a last-mile polish capped
    at 1.12x — beyond that the words themselves must get more screen time (the storyboard's
    listenable-pacing stretch), never the audio squeezed to fit."""
    speak = max(0.5, total - 0.25)
    af = "adelay=250|250"
    speed = raw_dur / speak
    if speed > 1.02:
        af += f",atempo={min(1.12, speed):.3f}"
    af += ",apad"
    return af


def _audio_dur(path: Path) -> Optional[float]:
    """Seconds of an audio file, parsed from ffmpeg's stderr (no ffprobe needed)."""
    try:
        out = subprocess.run([ffmpeg_exe(), "-hide_banner", "-i", str(path)],
                             capture_output=True, text=True, timeout=60).stderr or ""
        m = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", out)
        if m:
            h, mn, s = m.groups()
            return int(h) * 3600 + int(mn) * 60 + float(s)
    except Exception:
        pass
    return None


def synth_voiceover(
    lines: list[str],
    durations: list[float],
    out_path: str | Path,
    *,
    model: Optional[str] = None,
    voice: Optional[str] = None,
    deliveries: Optional[list[str]] = None,
    api_key: Optional[str] = None,
    backend: Optional[str] = None,
    tone: str = "",
    profile: Any = None,
) -> Optional[Path]:
    """Build ONE continuous narration track for the reel and TIME-FIT it to the footage.

    Two bugs this fixes (measured on the Azza Fahmy demo — owner: "الصوت الكنه بتتغير وبيقطع"):
      * CHANGING voice — the old path synthesized each scene line as a SEPARATE call, so the
        model gave a different intonation/energy every scene. We now join the lines into ONE
        script and synthesize it in a SINGLE call: one voice, one performance, start to end.
      * CUTTING — the old path hard-trimmed every segment to its scene length, chopping any
        line that ran long mid-word. We now lay the whole read over the footage and, only if it
        runs long, gently speed it (atempo, capped) to fit; otherwise pad trailing silence — the
        speech itself is never cut.

    `tone` (from the brand profile) steers the delivery brief; `deliveries[0]` seeds the overall
    emotional direction. `backend` selects the engine (gemini | openai | edge; default auto).
    Returns the audio path, or None if TTS is unavailable."""
    if not lines:
        return None
    chosen = _resolve_backend(backend)

    gem_client = _gemini_client() if chosen == "gemini" else None
    if chosen == "gemini" and gem_client is None:        # Gemini unavailable -> OpenAI
        chosen = "openai"

    oa_client = None
    if chosen == "openai":                               # OpenAI path needs a key
        key = api_key or os.environ.get("OPENAI_API_KEY")
        if not key:
            return None
        try:
            from openai import OpenAI
        except ImportError:
            return None
        oa_client = OpenAI(api_key=key)
    el_key = None
    if chosen == "elevenlabs":                           # native-EG library voice (Creator-tier key)
        el_key = api_key or os.environ.get("ELEVENLABS_API_KEY")
        if not el_key:
            return None
    # chosen == "edge" needs no client/key (free, keyless).

    model = model or os.environ.get("REEL_TTS_MODEL") or (
        _GEMINI_TTS_MODEL if chosen == "gemini"
        else _ELEVENLABS_MODEL if chosen == "elevenlabs" else _DEFAULT_MODEL)
    if chosen == "edge":
        default_voice = _edge_voice_for(" ".join(lines), None, profile)
    elif chosen == "gemini":
        default_voice = _GEMINI_VOICE
    elif chosen == "elevenlabs":
        default_voice = _elevenlabs_voice_for(profile)
    else:
        default_voice = _DEFAULT_VOICE
    voice = voice or os.environ.get("REEL_TTS_VOICE") or default_voice

    # ONE continuous script, scene order preserved, joined with a natural pause — but the
    # EMOTIVE terminal marks survive (owner: «الصوت برود»; the old joiner stripped every ! and ؟).
    parts = [str(l).strip() for l in lines if l and str(l).strip()]
    if not parts:
        return None
    # THE SPELLING IS THE PERFORMANCE (owner's engineer): rewrite as-spoken before any engine.
    _dial = ""
    if profile is not None:
        try:
            from poster.locale import arabic_dialect_for
            _dial = arabic_dialect_for(profile)
        except Exception:  # noqa: BLE001
            _dial = ""
    parts = _vocalize_for_tts(parts, dialect=_dial)
    script = _join_script(parts)
    total = sum(max(0.5, float(d)) for d in durations) or 10.0
    # Dialect follows the brand's evidenced region (poster.locale) — not a hardcoded Egyptian read.
    dialect_name = ""
    if profile is not None:
        try:
            from poster.locale import arabic_dialect_for
            dialect_name = arabic_dialect_for(profile)
        except Exception:  # noqa: BLE001
            dialect_name = ""
    instructions = _instructions_for(lines, list(deliveries or []), tone, dialect=dialect_name)

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    import tempfile
    with tempfile.TemporaryDirectory(prefix="vo_") as tmp:
        tmpd = Path(tmp)
        ext = "wav" if chosen == "gemini" else "mp3"
        raw = tmpd / f"vo_full.{ext}"
        if chosen == "gemini":
            ok = _gemini_segment(gem_client, script, raw, voice=voice, model=model,
                                 instructions=instructions)
        elif chosen == "edge":
            ok = _edge_read(parts, list(deliveries or []), raw, voice=voice, tone=tone)
        elif chosen == "elevenlabs":
            ok = _elevenlabs_segment(script, raw, voice=voice, model=model, api_key=el_key)
        elif chosen == "verbatik":
            ok = _verbatik_segment(script, raw,
                                   delivery=(deliveries[0] if deliveries else ""))
        else:
            ok = _tts_segment(oa_client, script, raw, model=model, voice=voice,
                              instructions=instructions)
        if not ok and chosen != "edge":
            # NEVER-SILENT (owner ruling, after the 2026-07-25 Abu Auf reel shipped with
            # audio=False): a paid backend failing must not cost the reel its voice. `edge` is
            # free, keyless, and carries a native Egyptian voice — a working voice from the free
            # tier beats silence from the paid one, every time. The primary's failure was already
            # logged above with its HTTP status and body, so nothing is hidden by recovering.
            logger.warning("[voiceover] %s failed -> edge fallback", chosen)
            raw = tmpd / "vo_full.mp3"       # edge writes mp3 whatever the primary wanted
            ok = _edge_read(parts, list(deliveries or []), raw,
                            voice=_edge_voice_for(script, None, profile), tone=tone)
        if not ok:
            return None

        # Time-fit to the footage. 0.25s lead-in keeps the read off the hard cut; if the read
        # runs long, speed it JUST enough to fit; then pad trailing silence to the full length.
        # `-t total` trims only that trailing silence.
        raw_dur = _audio_dur(raw) or total
        af = _fit_filter(raw_dur, total)
        run_ffmpeg([
            "-i", str(raw), "-af", af,
            "-t", f"{total:.2f}", "-ar", "44100", "-ac", "2",
            "-c:a", "aac", "-b:a", "192k", str(out_path.resolve()),
        ])
    return out_path if out_path.is_file() else None
