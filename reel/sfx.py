# -*- coding: utf-8 -*-
"""Sound effects, generated from a description — the layer this project never had.

The reel directive, §5: «الإعلان الاحترافي بيعتمد على التفاصيل؛ لازم يتم إضافة مؤثرات صوتية لكل
حركة: صوت خروش الغلاف · قطمة مقرمشة · whoosh بين المشاهد · أصوات خلفية واقعية». Every reel this
project has shipped had exactly two audio layers — a voice-over and a music bed — while the
director has been writing `sfx: whoosh | impact | chime` cues per scene that nothing consumed.

MEASURED 2026-07-26, the hour the account was funded: `POST /v1/sound-generation` answers with
mp3 bytes for a plain English description. So the cue the director already writes can become a
real sound.

Every call is METERED (`runs/elevenlabs_spend.jsonl`). A paid layer with no spend record is how
the $228 Veo scare happened, and it is not repeating on a layer added at the end of a long day.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

SFX_URL = "https://api.elevenlabs.io/v1/sound-generation"
# ElevenLabs bills sound generation in credits; ~100 credits/second is the published figure and
# the account is pay-as-you-go. Recorded as SECONDS so the ledger stays true even if the price
# per second changes — a row that says "1.5 s" is still correct next month.
_LEDGER = "runs/elevenlabs_spend.jsonl"

# The director's own cue vocabulary (reel.creative_director.CreativeScene.sfx) -> a description
# a sound model can actually render. A one-word prompt produces a one-word-quality sound.
_CUES = {
    "whoosh": "fast clean cinematic whoosh transition, short tail, no music",
    "impact": "deep short impact hit with a tight low thump, no reverb tail",
    "riser": "rising tension riser building to a clean cut, three seconds",
    "chime": "bright short chime accent, single note, clean and modern",
    "crunch": "crisp close-mic bite and crunch, dry and punchy, no room",
    "crinkle": "snack wrapper crinkling and tearing open, close mic, dry",
    "pour": "granular pour of nuts and grains onto a hard surface, close mic",
    "sparkle": "delicate sparkle shimmer accent, short, airy",
    "room": "quiet natural room tone, no voices, no music",
}


def describe_cue(cue: str) -> str:
    """A director cue -> a sound description.

    THREE cases, and the middle one is the whole point of the owner's request:

      * a known TOKEN (`whoosh`) expands to a usable description — a one-word prompt buys
        one-word-quality sound;
      * an ALREADY-WRITTEN description ("Crisp chocolate bar bite with crunchy nuts") passes
        through UNTOUCHED. It used to be reframed as "<the whole sentence> sound effect, short,
        clean, close mic, no music", which fights the sentence instead of using it. This is the
        case that makes a sound product-specific rather than stock;
      * an unknown single token still gets the neutral frame, so nothing renders silent.
    """
    text = str(cue or "").strip()
    if not text:
        return ""
    key = text.lower()
    if key in _CUES:
        return _CUES[key]
    if len(text.split()) >= 3:
        return text                      # already a description — do not fight it
    return f"{key} sound effect, short, clean, close mic, no music"


def sfx_clip(cue: str, *, seconds: float = 2.0, cache_dir: "str | Path | None" = None,
             api_key: str = "") -> Optional[Path]:
    """One CACHED sound effect for a cue. Returns the mp3 path, or None when it cannot be made.

    The cache is not an optimisation, it is a spend control: the owner re-renders a reel many
    times while reviewing it, and paying for the same whoosh on every render is exactly the shape
    of the $228 Veo bill. Key = the DESCRIPTION plus the duration, so an edited cue is a new sound
    and an unchanged one is free.

    A FAILURE IS NEVER CACHED. Writing a zero-byte placeholder would make the next render inherit
    the silence with no way to tell it from a real result.
    """
    description = describe_cue(cue)
    if not description:
        return None
    root = Path(cache_dir) if cache_dir else Path(
        os.environ.get("REEL_SFX_CACHE", "outputs/_sfx_cache"))
    digest = hashlib.sha1(
        f"{description}|{float(seconds):.2f}".encode("utf-8")).hexdigest()[:16]
    dst = root / f"{digest}.mp3"
    if dst.is_file() and dst.stat().st_size > 0:
        return dst
    out = generate_sfx(description, dst, seconds=seconds, api_key=api_key)
    if out is None:
        try:
            if dst.exists() and dst.stat().st_size == 0:
                dst.unlink()
        except OSError:
            pass
        return None
    return out


def _record_spend(*, seconds: float, detail: str = "") -> None:
    try:
        path = Path(os.environ.get("ELEVENLABS_SPEND_LEDGER", _LEDGER))
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"ts": time.time(), "provider": "elevenlabs",
                                 "kind": "sound_generation",
                                 "seconds": round(float(seconds), 2),
                                 "detail": detail}, ensure_ascii=False) + "\n")
    except Exception:  # noqa: BLE001 — a ledger failure must not kill a render
        logger.exception("could not record the sfx spend")


def generate_sfx(description: str, out_path: "str | Path", *, seconds: float = 2.0,
                 api_key: str = "", prompt_influence: float = 0.6,
                 timeout: float = 180.0, _urlopen=None) -> Optional[Path]:
    """One sound effect as an mp3. Returns the path, or None on ANY failure — a reel must never
    die because a whoosh could not be generated."""
    import urllib.request

    text = str(description or "").strip()
    key = api_key or os.environ.get("ELEVENLABS_API_KEY") or ""
    if not text or not key:
        return None
    body = json.dumps({"text": text, "duration_seconds": round(float(seconds), 2),
                       "prompt_influence": float(prompt_influence)}).encode("utf-8")
    req = urllib.request.Request(SFX_URL, data=body, headers={
        "xi-api-key": key, "Content-Type": "application/json", "Accept": "audio/mpeg"})
    opener = _urlopen or urllib.request.urlopen
    try:
        with opener(req, timeout=timeout) as r:
            data = r.read()
    except Exception as exc:  # noqa: BLE001
        code = getattr(exc, "code", "")
        detail = ""
        try:
            detail = exc.read()[:200].decode("utf-8", "replace")
        except Exception:  # noqa: BLE001
            pass
        logger.warning("sfx generation failed (%s) %s", code, detail)
        return None
    if not data:
        return None
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(data)
    _record_spend(seconds=seconds, detail=text[:60])
    return out
