# -*- coding: utf-8 -*-
"""The editor's last five minutes, as filters: handheld shake, mid-action opens, intra-scene cuts.

OWNER'S CHEAT SHEET (2026-07-27). Three of the four secrets live here; the fourth (the leading
whoosh) already lived in `reel/sound.py` and only needed its level fixed.

  * HANDHELD SHAKE — generation artefacts (a wobbling finger, a swimming hairline) read as flaws
    on a locked-off frame and as CAMERA MOTION on a moving one. The shake buries the tell.
  * HEAD TRIM — an AI clip warms up: the first half-second is the subject settling into the
    motion the prompt asked for. Cutting it opens every shot mid-action.
  * INTRA-SCENE CUTS — the pacing decision from the brief, applied the zero-cost way: ONE video-model
    clip re-cut into ≤1.8s shots with alternating punch-ins, instead of billing a clip per shot.

Everything here returns ffmpeg filter STRINGS — deterministic, testable without rendering, and
composable into the compositor's existing per-scene graph.
"""
from __future__ import annotations

from .pacing import MAX_SHOT_S, split_for_pace

# In-media-res: how much of a clip's head to discard when there is slack to spare.
HEAD_TRIM_S = 0.5

# Shake throw in pixels at 1080-wide, and the sine frequencies (rad/s). The frequencies are
# deliberately incommensurate — near-integer ratios make the two axes beat in sync and the wobble
# reads as a LOOP, which is worse than no shake at all.
_SHAKE_X = ((7.3, 6.0), (2.9, 4.0))
_SHAKE_Y = ((5.1, 5.0), (3.3, 3.0))
_SHAKE_SCALE = 1.05


def handheld_shake_filter(width: int, height: int, intensity: float = 1.0) -> str:
    """A subtle animated crop that never exposes an edge.

    The frame is oversampled by 5% and the crop window drifts inside it on summed sines. Max
    throw at intensity=1.0 is 10px horizontal / 8px vertical on a 1080-wide frame — visible as
    life, invisible as technique.
    """
    k = max(0.0, float(intensity))

    def _sum(terms):
        return "+".join(f"sin(t*{f})*{a * k:.1f}" for f, a in terms)

    def _cos_sum(terms):
        first = True
        parts = []
        for f, a in terms:
            fn = "cos" if first else "sin"
            parts.append(f"{fn}(t*{f})*{a * k:.1f}")
            first = False
        return "+".join(parts)

    return (
        f"scale=iw*{_SHAKE_SCALE}:ih*{_SHAKE_SCALE},"
        f"crop={width}:{height}:"
        f"x='(iw-{width})/2+{_sum(_SHAKE_X)}':"
        f"y='(ih-{height})/2+{_cos_sum(_SHAKE_Y)}'"
    )


def head_trim_for(*, clip_s: float, scene_s: float) -> float:
    """How much head to cut off this clip — HEAD_TRIM_S when the slack exists, else nothing.

    Guarded on the measured clip length: trimming a clip that only just covers its scene would
    leave the scene short and the concat frozen on a last frame. Unknown length (0) trims nothing,
    because "we did not measure" must never become "we cut anyway".
    """
    try:
        clip, scene = float(clip_s), float(scene_s)
    except (TypeError, ValueError):
        return 0.0
    if clip <= 0 or scene <= 0:
        return 0.0
    return HEAD_TRIM_S if clip - HEAD_TRIM_S >= scene else 0.0


def intra_scene_chain(label_in: str, label_out: str, *, duration_s: float,
                      width: int, height: int) -> str:
    """Re-cut one scene's footage into commercial-length shots with alternating punch-ins.

    Returns an ffmpeg filter fragment reading [label_in] and writing [label_out], or "" when the
    scene is already at pace (the caller keeps its original label). Even-numbered shots play
    clean; odd-numbered ones are punched in 6% — enough that the cut registers as a new angle,
    not enough to soften the image. Same clip throughout: the cut is the effect, and it is free.
    """
    d = float(duration_s or 0)
    shots = split_for_pace([d]) if d > 0 else []
    if len(shots) <= 1:
        return ""
    parts = []
    labels = []
    t = 0.0
    for i, s in enumerate(shots):
        seg = f"[{label_in}{i}]trim={t:.3f}:{t + s:.3f},setpts=PTS-STARTPTS"
        if i % 2 == 1:
            seg += f",scale=iw*1.06:ih*1.06,crop={width}:{height}"
        # SAR PINNED ON EVERY SHOT, punched or not. `concat` refuses inputs whose sample aspect
        # ratios differ, and a fractional `scale` leaves a SAR a hair off the untouched shots' —
        # identical pixel sizes, mismatched SAR, render dead. MEASURED: it killed a live Nano Care
        # render with "Failed to configure output pad on Parsed_concat_14".
        seg += f",setsar=1[shot{i}]"
        parts.append(seg)
        labels.append(f"[shot{i}]")
        t += s
    split = f"[{label_in}]split={len(shots)}" + "".join(
        f"[{label_in}{i}]" for i in range(len(shots)))
    concat = "".join(labels) + f"concat=n={len(shots)}:v=1:a=0[{label_out}]"
    return ";".join([split] + parts + [concat])
