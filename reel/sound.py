"""Procedural SOUND DESIGN for the reel (the owner's #6 — "sound is half the feel").

The web reel was SILENT and there is NO royalty-free music bundled (and we can't license
one). So this synthesizes a tasteful, free sound bed ENTIRELY with the bundled ffmpeg's
audio-synthesis filters (sine / anoisesrc / aevalsrc — VERIFIED present in imageio-ffmpeg):
  * a quiet low ambient PAD (a detuned two-note drone) so the reel never feels dead,
  * a WHOOSH on every cut (timed to the transition offsets) — the classic transition sweep,
  * an opening IMPACT (a soft low boom) under the hero word — the "hit".
This is sound DESIGN, not music: when a real `music_path` is supplied the compositor/motion
engine muxes THAT instead (and the cuts beat-sync to its bpm). Never raises — on any failure
the caller just gets a silent reel (today's behavior), so it can't break a render.
"""
from __future__ import annotations

import math
import os
import struct
import wave
from pathlib import Path
from typing import Optional

from reel.ffmpeg_tools import run_ffmpeg
from reel.sfx import sfx_clip


def _wave_impact_fallback(out_path: str | Path, total_s: float) -> Optional[Path]:
    """stdlib-ONLY fallback (the owner's #ج): if ffmpeg's audio-synth filters are ever missing
    from the build, write a short low IMPACT then silence as a real WAV via the `wave` module —
    so the CTA/opening still gets a hit and the reel is never dead-silent. No external library."""
    try:
        sr = 44100
        n = max(sr, int(float(total_s) * sr))
        path = Path(out_path).with_suffix(".wav")
        impact_n = int(0.5 * sr)
        frames = bytearray()
        for i in range(impact_n):                       # a 62Hz boom decaying over ~0.4s
            t = i / sr
            s = math.exp(-t * 8.0) * math.sin(2 * math.pi * 62 * t) * 0.7
            frames += struct.pack("<h", int(max(-1.0, min(1.0, s)) * 32767))
        with wave.open(str(path), "w") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(sr)
            w.writeframes(bytes(frames))
            w.writeframes(b"\x00\x00" * (n - impact_n))  # silence to length (bulk, fast)
        return path if path.exists() else None
    except Exception:
        return None


# The pad's tone by MOOD (universal keywords, substring-matched against the plan's sound_design /
# music_mood). (root Hz, fifth Hz, lowpass Hz, volume) — darker+sparser for tension, brighter+fuller
# for confidence. A static pad can't follow a whole progression, so it takes the FIRST mood token
# found (the opening tone); the per-beat sfx carry the arc's movement.
_MOOD_TABLE = {
    "tense": (98.00, 130.81, 360, 0.50), "cluttered": (98.00, 130.81, 360, 0.50),
    "anxious": (98.00, 130.81, 360, 0.50), "minimal": (110.00, 164.81, 440, 0.48),
    "calm": (110.00, 164.81, 520, 0.52), "warm": (110.00, 164.81, 620, 0.58),
    "build": (110.00, 174.61, 720, 0.60), "hopeful": (110.00, 174.61, 700, 0.58),
    "upbeat": (130.81, 196.00, 900, 0.62), "collaborat": (123.47, 185.00, 820, 0.60),
    "confident": (130.81, 196.00, 780, 0.62), "resolve": (130.81, 196.00, 700, 0.60),
    "proud": (130.81, 196.00, 760, 0.60), "triumph": (130.81, 196.00, 900, 0.62),
}
_MOOD_DEFAULT = (110.00, 164.81, 520, 0.55)


def _mood_params(mood: str) -> tuple[float, float, int, float]:
    m = (mood or "").lower()
    for key, params in _MOOD_TABLE.items():
        if key in m:
            return params
    return _MOOD_DEFAULT


def synth_sound_bed(
    out_path: str | Path,
    total_s: float,
    cut_times: Optional[list[float]] = None,
    *,
    impact: bool = True,
    mood: str = "",
    sfx_beats: Optional[list[tuple[float, str]]] = None,
    real_sfx: Optional[bool] = None,
) -> Optional[Path]:
    """Synthesize a sound bed (mood-tuned pad + whooshes at `cut_times` + opening impact + authored
    `sfx_beats`) into an .m4a. `mood` tunes the pad's tone; `sfx_beats` are (time_s, name) cues where
    name is whoosh | impact | chime — the reel's authored per-beat sound. Returns the path, or None on
    failure (caller falls back to silence). Pure ffmpeg synth — no external assets, no network."""
    out_path = Path(out_path)
    total = max(1.0, float(total_s))
    cuts = [c for c in (cut_times or []) if 0.2 < c < total - 0.1]
    root, fifth, lp, vol = _mood_params(mood)

    inputs: list[str] = []
    filters: list[str] = []
    mix_labels: list[str] = []
    idx = 0

    # 0) ambient PAD — two quiet detuned low sines (root + a soft fifth), lowpassed = a warm drone,
    #    tuned by the reel's opening mood.
    inputs += ["-f", "lavfi", "-i",
               f"aevalsrc=0.05*sin(2*PI*{root:.2f}*t)+0.035*sin(2*PI*{fifth:.2f}*t):d={total:.2f}:s=44100"]
    filters.append(f"[{idx}:a]lowpass=f={lp},volume={vol:.2f},"
                   f"afade=t=in:d=0.6,afade=t=out:st={max(0.0, total - 1.2):.2f}:d=1.2[pad]")
    mix_labels.append("[pad]")
    idx += 1

    # 1) opening IMPACT — a soft low boom under the hero word.
    if impact:
        inputs += ["-f", "lavfi", "-i", "sine=frequency=62:duration=0.5:sample_rate=44100"]
        filters.append(f"[{idx}:a]afade=t=out:st=0.06:d=0.42,lowpass=f=180,volume=0.85[imp]")
        mix_labels.append("[imp]")
        idx += 1

    # 2..) a WHOOSH per cut — pink-noise sweep, band-limited, delayed to just before the cut.
    for c in cuts:
        inputs += ["-f", "lavfi", "-i", "anoisesrc=d=0.6:c=pink:a=0.7:r=44100"]
        ms = int(max(0.0, c - 0.3) * 1000)
        # -20dB (owner cheat-sheet #1): the whoosh PREPARES the ear for the cut — it must
        # be felt, not heard. At volume=0.5 it competed with the voice.
        filters.append(
            f"[{idx}:a]highpass=f=320,lowpass=f=5200,"
            f"afade=t=in:d=0.3,afade=t=out:st=0.3:d=0.3,volume=0.1,"
            f"adelay={ms}|{ms}[w{idx}]"
        )
        mix_labels.append(f"[w{idx}]")
        idx += 1

    # 3..) AUTHORED SFX beats — the plan's per-scene sfx cues, placed at their scene start times.
    #
    # REAL SOUND FIRST (2026-07-26). Everything below this line used to be the ONLY option: a
    # 62 Hz sine for an impact, pink noise for a whoosh, two sines for a chime. Signal generators,
    # not sound design — and the reason a technically-correct reel still sounded cheap. `sfx_clip`
    # renders the cue with a real sound model and CACHES it; when it cannot (no key, no network,
    # an API error) the synthesis below still covers the beat. Fail DOWN, never to silence.
    use_real = (os.environ.get("REEL_REAL_SFX", "1") not in ("0", "false", "no")
                if real_sfx is None else bool(real_sfx))
    for t_beat, name in (sfx_beats or []):
        try:
            t = float(t_beat)
        except (TypeError, ValueError):
            continue
        if not (0.0 <= t < total):
            continue
        nm = (name or "").strip().lower()

        clip = None
        if use_real and nm:
            try:
                clip = sfx_clip(name, seconds=min(3.0, max(1.0, total - t)))
            except Exception:  # noqa: BLE001 — a paid layer must never break a free render
                clip = None
        if clip is not None:
            ms = int(t * 1000)   # a recorded sound's attack IS its first sample — no pre-roll
            inputs += ["-i", str(clip)]
            filters.append(f"[{idx}:a]volume=0.7,afade=t=out:st=0.9:d=0.4,"
                           f"aresample=44100,adelay={ms}|{ms}[s{idx}]")
            mix_labels.append(f"[s{idx}]")
            idx += 1
            continue

        if "chime" in nm or "bell" in nm or "ding" in nm:
            ms = int(t * 1000)
            inputs += ["-f", "lavfi", "-i",
                       "aevalsrc=0.5*sin(2*PI*880*t)+0.4*sin(2*PI*1318.51*t):d=0.4:s=44100"]
            filters.append(f"[{idx}:a]afade=t=out:st=0.08:d=0.32,volume=0.5,adelay={ms}|{ms}[s{idx}]")
        elif "impact" in nm or "boom" in nm or "hit" in nm or "riser" in nm:
            ms = int(t * 1000)
            inputs += ["-f", "lavfi", "-i", "sine=frequency=62:duration=0.5:sample_rate=44100"]
            filters.append(f"[{idx}:a]afade=t=out:st=0.06:d=0.42,lowpass=f=180,volume=0.8,"
                           f"adelay={ms}|{ms}[s{idx}]")
        else:                                    # whoosh / sweep / anything else -> the sweep
            ms = int(max(0.0, t - 0.3) * 1000)
            inputs += ["-f", "lavfi", "-i", "anoisesrc=d=0.6:c=pink:a=0.7:r=44100"]
            filters.append(f"[{idx}:a]highpass=f=320,lowpass=f=5200,afade=t=in:d=0.3,"
                           f"afade=t=out:st=0.3:d=0.3,volume=0.5,adelay={ms}|{ms}[s{idx}]")
        mix_labels.append(f"[s{idx}]")
        idx += 1

    n = len(mix_labels)
    filters.append(
        "".join(mix_labels)
        + f"amix=inputs={n}:normalize=0,atrim=0:{total:.2f},"
        + "alimiter=limit=0.95,aresample=44100[out]"
    )
    graph = ";".join(filters)

    try:
        run_ffmpeg(inputs + [
            "-filter_complex", graph, "-map", "[out]",
            "-c:a", "aac", "-b:a", "160k", str(out_path),
        ])
    except Exception:
        return _wave_impact_fallback(out_path, total)   # owner's #ج — never dead-silent
    return out_path if out_path.exists() else _wave_impact_fallback(out_path, total)
