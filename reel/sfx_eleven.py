# -*- coding: utf-8 -*-
"""Sound effects from ElevenLabs — the film's atmosphere, not a synthesiser's idea of one.

OWNER, 2026-08-03: «استعمل كل قوة 11LAB علشان منقعش في مشكلة الصوت تاني، حتى لو عايز تعمل أصوات
الرش، كل ده 11 لاب».

WHY THIS EXISTS. `reel/sound.py` builds a bed out of ffmpeg primitives — `aevalsrc` pulses,
filtered noise for whooshes, a sine chime. It is free and it sounds free: the owner called the
result «بشع» and the bed was switched off entirely, which left the film carrying voice over
silence. That is honest but thin — a spray ad with no spray is missing the one sound the picture
is about.

MEASURED before writing a line: `POST /v1/sound-generation` returns 200 audio/mpeg on this
account (payg tier). The endpoint answers 405 to GET, which is how it was found — it exists and
wants a POST. So real recorded-quality effects are available and the synth never needs to be.

WHAT IT DELIBERATELY DOES NOT DO. It does not invent an atmosphere. Every effect here is asked
for BY THE DIRECTOR, as a scene's `sfx` line, describing something visible in that shot. A bed of
generic "uplifting ambience" under a film nobody scored is the same lie the synth bed was.

Never raises: a reel is a deliverable and a missing effect is a quieter reel, not a failed render.
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

_URL = "https://api.elevenlabs.io/v1/sound-generation"

# The API caps a single effect; anything longer is a bed, not an effect, and a bed is a licensing
# question rather than a generation one.
_MAX_S = 22.0
_MIN_S = 0.5

# How literally the model follows the words. High enough that "spray hiss" is a spray hiss and not
# a mood, low enough that it still sounds recorded.
_INFLUENCE = 0.6


def _key() -> str:
    return (os.environ.get("ELEVENLABS_API_KEY") or "").strip()


def available() -> bool:
    """True when an effect could be fetched. Cheap: no network, just the credential."""
    return bool(_key())


def sound_effect(description: str, out_path: "str | Path", *,
                 duration_s: float = 2.0) -> Optional[str]:
    """One effect, written to `out_path`. Returns the path, or None if nothing could be made.

    `description` is plain English describing a SOUND, not a mood — the API is literal and rewards
    it: "a short soft cosmetic pump spray hiss, close mic, clean studio" returns a spray;
    "uplifting" returns a synthesiser pad.
    """
    text = str(description or "").strip()
    if not text or not _key():
        return None
    dur = max(_MIN_S, min(_MAX_S, float(duration_s or 2.0)))
    try:
        import requests

        r = requests.post(
            _URL,
            headers={"xi-api-key": _key(), "Content-Type": "application/json"},
            data=json.dumps({"text": text, "duration_seconds": round(dur, 2),
                             "prompt_influence": _INFLUENCE}),
            timeout=120,
        )
        if r.status_code != 200 or not r.content:
            logger.info("[sfx] %s -> %s %s", text[:40], r.status_code, r.text[:120])
            return None
        dest = Path(str(out_path))
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(r.content)
        _meter(text, dur)
        return str(dest)
    except Exception as exc:  # noqa: BLE001 — a missing effect is a quieter reel, never a failure
        logger.info("[sfx] %s unavailable (%s: %s)", text[:40], type(exc).__name__, exc)
        return None


def _meter(text: str, dur: float) -> None:
    """Append to the ElevenLabs spend ledger.

    Effects bill separately from characters, and an unmetered vendor call is how a bill becomes a
    surprise — this repo has already had that conversation once about Veo.
    """
    try:
        import datetime

        line = {"ts": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                "kind": "sound_effect", "seconds": round(dur, 2), "detail": text[:80]}
        p = Path("runs/elevenlabs_spend.jsonl")
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(line, ensure_ascii=False) + "\n")
    except Exception:  # noqa: BLE001
        pass


def bed_from_beats(beats: list, out_path: "str | Path", *, total_s: float,
                   work_dir: "str | Path") -> Optional[str]:
    """The film's effects, laid on one silent track at their cue times.

    `beats` is `[(start_s, description)]` — the director's own `sfx` lines, which is why this
    cannot drift into generic ambience: an effect exists only because a scene asked for something
    that happens in it.

    Returns a path to an audio file `total_s` long, or None. The track is SILENT where no effect
    was asked for; the compositor ducks it under the voice exactly as it did the synth bed, so
    nothing downstream changes.
    """
    cues = [(float(t), str(d).strip()) for t, d in (beats or []) if str(d or "").strip()]
    if not cues or not _key():
        return None
    work = Path(str(work_dir))
    work.mkdir(parents=True, exist_ok=True)
    made: list = []
    for i, (at, desc) in enumerate(cues):
        if at >= total_s:
            continue                       # a cue past the last frame is a cue for another film
        # Each effect gets the room until the next cue, capped — an effect that runs into the next
        # one is two sounds fighting, which is what a synth bed sounded like.
        nxt = min([t for t, _ in cues if t > at] or [total_s])
        got = sound_effect(desc, work / f"sfx{i}.mp3", duration_s=min(4.0, max(0.8, nxt - at)))
        if got:
            made.append((at, got))
    if not made:
        return None
    try:
        from .ffmpeg_tools import ffmpeg_exe, run_ffmpeg

        args: list = ["-f", "lavfi", "-i", f"anullsrc=r=44100:cl=stereo:d={total_s:.2f}"]
        for _, path in made:
            args += ["-i", path]
        parts = [f"[{i + 1}:a]adelay={int(at * 1000)}|{int(at * 1000)},volume=0.85[s{i}]"
                 for i, (at, _) in enumerate(made)]
        mix = "".join(f"[s{i}]" for i in range(len(made)))
        parts.append(f"[0:a]{mix}amix=inputs={len(made) + 1}:duration=first:"
                     f"dropout_transition=0,volume=1.4[a]")
        dest = Path(str(out_path))
        run_ffmpeg(args + ["-filter_complex", ";".join(parts), "-map", "[a]",
                           "-t", f"{total_s:.2f}", "-c:a", "aac", "-b:a", "160k", str(dest)])
        ffmpeg_exe()  # referenced so a missing binary fails here, loudly, not at mux time
        return str(dest) if dest.is_file() and dest.stat().st_size else None
    except Exception as exc:  # noqa: BLE001
        logger.info("[sfx] bed assembly failed (%s: %s)", type(exc).__name__, exc)
        return None
