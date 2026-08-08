# -*- coding: utf-8 -*-
"""The editor's last five minutes, as filters: handheld shake, mid-action opens, intra-scene cuts.

OWNER'S CHEAT SHEET (2026-07-27). Three of the four secrets live here; the fourth (the leading
whoosh) already lived in `reel/sound.py` and only needed its level fixed.

  * HANDHELD SHAKE — generation artefacts (a wobbling finger, a swimming hairline) read as flaws
    on a locked-off frame and as CAMERA MOTION on a moving one. The shake buries the tell.
  * HEAD TRIM — an AI clip warms up: the first half-second is the subject settling into the
    motion the prompt asked for. Cutting it opens every shot mid-action.
  * INTRA-SCENE CUTS — the pacing decision from the brief, applied the zero-cost way: ONE video-model
    clip re-cut into ≤1.8s shots, instead of billing a clip per shot.

MEASURED 2026-08-06, and it corrected this file's own claim. `intra_scene_chain` said its 6%
punch-in was "enough that the cut registers as a new angle". It is not. Scene-change scores on
`outputs/reels/alfouad_run3.mp4` (ffmpeg `select='gt(scene,0.02)'`), a shipped 15-second reel:

    real scene cuts   2.97s → 0.522 · 7.13s → 0.606 · 10.10s → 0.667
    intra-scene cuts  1.50s → 0.101 · 4.37s → 0.079 · 5.77s → 0.080 · 8.63s → 0.104

Five to six times weaker than a cut, and inside the range ordinary movement scores. So the film
had NINE cuts in the plan and THREE a viewer can see — mean visible shot 3.83s on a 15-second ad,
where the format wants 1.2-1.8s. The nominal pace was right all along; the cut was not being made.

TWO THINGS MAKE A CUT READ, and the old chain did neither:

  * THE SIZE CHANGES. An editor's rule of thumb is that two shots of the same subject cut together
    only when the image size moves by about a third; below that the eye reads a zoom, not an edit.
    6% is under that by a factor of five. The tiers below move 1.37-1.48x.
  * TIME IS REMOVED. The old windows were CONTIGUOUS — `trim=0:1.8` then `trim=1.8:3.6` — so the
    action ran straight through the join. Nothing was cut out, therefore nothing was cut. The
    windows now advance across the whole purchased clip, so the action jumps forward at every cut.

The second one is free in the strictest sense: the video model only sells 4/6/8-second clips, so a
2.97-second scene has already been PAID FOR out to 4 seconds and the surplus was being discarded by
`-t`. Buying coverage and cutting less than you bought is what the purchase was for.

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


# THE TWO FRAMING TIERS THE CUT ALTERNATES BETWEEN.
#
# Each entry is (zoom, centre_x, centre_y) where the centres are fractions of the available crop
# travel — 0.5 is dead centre. A punch that only zooms is a zoom; one that also MOVES is a
# different camera position, which is what an angle change actually is. The close tier sits
# slightly high (0.41 / 0.46) because in a 9:16 ad the face and the hands live in the upper half.
#
# The zoom ceiling is a PICTURE-QUALITY decision, not a taste one: the video model delivers 720p
# on this project, so 1.48x is already showing a 486-pixel-wide region blown up to the frame.
# Past ~1.5 the softening is visible on a phone, and a soft shot is a worse defect than a tame cut.
_WIDE = ((1.00, 0.50, 0.50), (1.04, 0.44, 0.54))
_CLOSE = ((1.42, 0.52, 0.41), (1.48, 0.46, 0.57))

# Below this ratio between two consecutive shots the eye reads a zoom, not a cut (the editor's
# "30% rule"). Stated here so the guard test can check the ladder instead of a magic number.
MIN_CUT_RATIO = 1.30

# Shot lengths are not metronomic in any cut worth watching. These weights (normalised, so the
# scene's total is untouched) break the tick without ever pushing a shot past the pace ceiling —
# and when they would, the split falls back to equal pieces rather than breaking the cap.
_RHYTHM = {2: (1.18, 0.82), 3: (1.14, 0.86, 1.00), 4: (1.12, 0.88, 1.04, 0.96)}


def framings_for(n: int) -> list[tuple[float, float, float]]:
    """The (zoom, cx, cy) ladder for `n` consecutive shots — wide, close, wide, close.

    Alternating TIERS rather than walking a single scale is what keeps every adjacent pair above
    `MIN_CUT_RATIO`: a monotonic ladder has to take small steps to stay inside the zoom ceiling,
    and small steps are exactly the defect this replaced. The two variants inside each tier stop a
    repeated wide (or a repeated close, across a scene boundary) from being the identical frame.
    """
    out: list[tuple[float, float, float]] = []
    for i in range(max(0, int(n))):
        tier = _WIDE if i % 2 == 0 else _CLOSE
        out.append(tier[(i // 2) % len(tier)])
    return out


def framing_filter(framing: tuple[float, float, float], width: int, height: int) -> str:
    """One shot's reframe as an ffmpeg fragment, or "" for the untouched plate."""
    z, cx, cy = framing
    if z <= 1.0 + 1e-9:
        return ""
    return (f"scale=iw*{z:.3f}:ih*{z:.3f},"
            f"crop={width}:{height}:x=(iw-{width})*{cx:.2f}:y=(ih-{height})*{cy:.2f}")


def _shot_lengths(d: float) -> list[float]:
    """The scene's shots, paced and de-metronomed, summing to exactly `d`."""
    shots = split_for_pace([d]) if d > 0 else []
    n = len(shots)
    w = _RHYTHM.get(n)
    if not w or n < 2:
        return shots
    total = sum(w)
    if max(w) / total * d > MAX_SHOT_S + 1e-9:
        return shots                      # the rhythm would break the pace ceiling — stay equal
    # Boundaries, then differences: rounding each weighted piece re-introduces exactly the drift
    # `split_for_pace` was fixed to remove.
    acc, bounds = 0.0, [0.0]
    for x in w:
        acc += x
        bounds.append(round(d * acc / total, 3))
    bounds[-1] = round(d, 3)
    return [round(bounds[k + 1] - bounds[k], 3) for k in range(n)]


def intra_scene_chain(label_in: str, label_out: str, *, duration_s: float,
                      width: int, height: int, clip_s: float = 0.0) -> str:
    """Re-cut one scene's footage into commercial-length shots that actually read as cuts.

    Returns an ffmpeg filter fragment reading [label_in] and writing [label_out], or "" when the
    scene is already at pace (the caller keeps its original label).

    `clip_s` is how much SOURCE the scene actually has — the purchased clip's length after any
    head trim, which is normally LONGER than the scene, because the video model sells 4/6/8-second
    clips and a 2.97-second scene is billed at 4. That surplus is the edit's raw material: the
    windows are spread across all of it, so time is removed at every join and the action jumps
    forward instead of running through. `0.0` means nobody measured it — the windows then stay
    inside the scene exactly as before, because "we did not measure" must not become "we cut
    anyway" (the same rule `head_trim_for` follows).
    """
    d = float(duration_s or 0)
    shots = _shot_lengths(d)
    if len(shots) <= 1:
        return ""
    n = len(shots)
    # THE SOURCE IS THE WHOLE CLIP; THE OUTPUT IS THE SCENE. Every window keeps its shot's output
    # length, but the windows are pushed apart by an equal gap so the last one ENDS on the last
    # frame we paid for. Total output is untouched — pacing is an edit decision, never a licence
    # to shorten the film (`reel/pacing.py`).
    source = max(d, float(clip_s or 0.0))
    gap = (source - d) / (n - 1) if n > 1 else 0.0
    # BOUNDARIES, NOT ACCUMULATION — the same discipline `split_for_pace` was fixed to (and the
    # reason it is checked by a guard test). `edges` are the OUTPUT boundaries; shot k's SOURCE
    # window is that pair pushed forward by k gaps, which is a closed form rather than a running
    # total, so the last window lands exactly on the end of the clip by construction.
    edges = [0.0]
    for s in shots:
        edges.append(min(d, edges[-1] + float(s)))
    edges[-1] = d
    parts, labels = [], []
    framings = framings_for(n)
    for i, _s in enumerate(shots):
        # CLAMPED, ALWAYS. Whatever arithmetic produced the shot list, no window may ask ffmpeg
        # for footage past the end of the clip — it used to emit `trim=3.334:5.001` for a 5-second
        # scene. The clamp cannot fail; the arithmetic above can.
        t = min(edges[i] + i * gap, max(0.0, source - (edges[i + 1] - edges[i])))
        end = min(source, t + (edges[i + 1] - edges[i]))
        seg = f"[{label_in}{i}]trim={t:.3f}:{end:.3f},setpts=PTS-STARTPTS"
        reframe = framing_filter(framings[i], width, height)
        if reframe:
            seg += "," + reframe
        # SAR PINNED ON EVERY SHOT, reframed or not. `concat` refuses inputs whose sample aspect
        # ratios differ, and a fractional `scale` leaves a SAR a hair off the untouched shots' —
        # identical pixel sizes, mismatched SAR, render dead. MEASURED: it killed a live Nano Care
        # render with "Failed to configure output pad on Parsed_concat_14".
        seg += f",setsar=1[shot{i}]"
        parts.append(seg)
        labels.append(f"[shot{i}]")
    split = f"[{label_in}]split={n}" + "".join(f"[{label_in}{i}]" for i in range(n))
    concat = "".join(labels) + f"concat=n={n}:v=1:a=0[{label_out}]"
    return ";".join([split] + parts + [concat])
