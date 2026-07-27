# -*- coding: utf-8 -*-
"""Zero-latency word sync: the caption word pops at the instant the voice says it.

OWNER'S FINAL PIECE (2026-07-27). The kinetic layer staggers hero words at a FIXED 0.07s — a
rhythm, not a sync. ElevenLabs' `/with-timestamps` endpoint returns per-character start/end times
alongside the audio, so the real schedule is available for the price of the same TTS call.

The narration is ONE continuous ElevenLabs call (synth_voiceover joins the scene lines precisely
so the voice cannot change between scenes), which makes the mapping simple: the alignment's word
times are GLOBAL narration times; a scene's caption words map onto its line's words; the CSS delay
for a word is (its global start − the scene's start), clamped at zero because audio can drift a
breath ahead of a hard video cut.

Grouping is by Unicode whitespace, not the space character: her reference snippet split on " "
only, which welds two words together across a newline and shifts every timestamp after it.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import List, Optional

logger = logging.getLogger(__name__)


def words_from_alignment(characters, starts, ends) -> List[dict]:
    """ElevenLabs character alignment -> [{word, start, end}], whitespace-safe and paired."""
    out: List[dict] = []
    word, w_start, w_end = "", None, None
    for ch, s, e in zip(characters or [], starts or [], ends or []):
        if str(ch).isspace():
            if word:
                out.append({"word": word, "start": float(w_start), "end": float(w_end)})
                word, w_start, w_end = "", None, None
            continue
        if w_start is None:
            w_start = s
        word += str(ch)
        w_end = e
    if word:
        out.append({"word": word, "start": float(w_start), "end": float(w_end)})
    return out


def tts_with_timestamps(text: str, out_mp3: "str | Path", *, voice: str, model: str,
                        api_key: str, voice_settings: dict, timeout: float = 180.0,
                        _urlopen=None) -> Optional[List[dict]]:
    """One narration with its word schedule. Writes the mp3 AND `<out>.words.json`; returns the
    words, or None on ANY failure so the caller falls back to the plain endpoint — a reel must
    never lose its voice because a timestamp was unavailable."""
    import base64
    import urllib.request

    if not (text or "").strip() or not voice or not api_key:
        return None
    body = json.dumps({"text": text, "model_id": model,
                       "voice_settings": dict(voice_settings or {})}).encode("utf-8")
    req = urllib.request.Request(
        f"https://api.elevenlabs.io/v1/text-to-speech/{voice}/with-timestamps",
        data=body, method="POST",
        headers={"xi-api-key": api_key, "Content-Type": "application/json"})
    opener = _urlopen or urllib.request.urlopen
    try:
        with opener(req, timeout=timeout) as r:
            data = json.loads(r.read().decode("utf-8"))
        audio = base64.b64decode(data["audio_base64"])
        align = data.get("alignment") or {}
        words = words_from_alignment(align.get("characters"),
                                     align.get("character_start_times_seconds"),
                                     align.get("character_end_times_seconds"))
        if not audio or not words:
            return None
        out = Path(out_mp3)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(audio)
        out.with_suffix(out.suffix + ".words.json").write_text(
            json.dumps(words, ensure_ascii=False), encoding="utf-8")
        return words
    except Exception as exc:  # noqa: BLE001
        logger.warning("with-timestamps failed (%s); caller falls back", type(exc).__name__)
        return None


def _norm(w: str) -> str:
    import re
    return re.sub(r"[^\w؀-ۿ]+", "", str(w or "").lower())


def caption_delays(caption: str, words: List[dict], *, scene_start_s: float,
                   search_from: int = 0) -> Optional[tuple]:
    """CSS delays for a caption's words, read off the narration schedule.

    Finds the caption's word sequence inside the global word list (normalised match, resuming at
    `search_from` so repeated words land on the right occurrence), and returns
    (delays, next_search_from) — or None when the caption is not what the voice says, in which
    case the caller keeps the designed stagger. Delays clamp at 0: audio may lead a hard cut.
    """
    cap = [_norm(w) for w in str(caption or "").split() if _norm(w)]
    if not cap or not words:
        return None
    pool = [_norm(w["word"]) for w in words]
    for i in range(max(0, search_from), len(pool) - len(cap) + 1):
        if all(pool[i + j] == cap[j] or pool[i + j].startswith(cap[j]) or
               cap[j].startswith(pool[i + j]) for j in range(len(cap))):
            delays = tuple(max(0.0, round(float(words[i + j]["start"]) - scene_start_s, 3))
                           for j in range(len(cap)))
            return delays, i + len(cap)
    return None
