# -*- coding: utf-8 -*-
"""Commercial pacing: no shot outstays its welcome, and cuts carry movement.

BRIEF (owner, 2026-07-27, rule 2): no single clip or camera angle may exceed ~1.8 s, and shots are
joined with speed ramps or whip pans rather than laid end to end.

MEASURED BEFORE THIS EXISTED: nothing anywhere capped a shot. `reel/compositor.py:15` states its own
method — "Hard cuts + per-scene text fades ... rather than xfade crossfades" — so a director writing
a six-second beat got a six-second locked-off shot, hard-cut to the next one. Long shot + hard cut +
no camera move is the definition of the slideshow the brief is about.

TWO RULES, AND THE SECOND IS THE ONE THAT IS EASY TO GET WRONG:
  * a beat longer than the cap is SPLIT into equal shots, so total run time is unchanged — pacing
    is an edit decision, not a licence to shorten the film;
  * no fragment may fall below MIN_SHOT_S. A naive `while remaining > cap` leaves a remainder: 3.7 s
    becomes 1.8 + 1.8 + 0.1, and a tenth-of-a-second shot reads as a glitch, not a cut. Splitting
    into EQUAL parts avoids the runt entirely.
"""
from __future__ import annotations

import math
from typing import List

MAX_SHOT_S = 1.8      # the brief's ceiling
MIN_SHOT_S = 0.6      # below this a shot reads as a flash rather than an image

# Transitions carry the motion across the cut. Kept short on purpose: past ~0.25 s the move stops
# being a join and becomes the content.
_TRANSITIONS = (
    {"kind": "whip", "direction": "left", "duration_s": 0.18},
    {"kind": "push", "direction": "in", "duration_s": 0.16},
    {"kind": "whip", "direction": "right", "duration_s": 0.18},
    {"kind": "push", "direction": "out", "duration_s": 0.16},
)


def split_for_pace(durations, max_shot_s: float = MAX_SHOT_S) -> List[float]:
    """Beat durations -> shot durations, none longer than the cap, same total run time.

    Equal division rather than greedy subtraction: greedy leaves a remainder that is almost always
    below the flash threshold.
    """
    out: List[float] = []
    for raw in (durations or []):
        try:
            d = float(raw)
        except (TypeError, ValueError):
            continue
        if d <= 0:
            continue
        if d <= max_shot_s + 1e-9:
            out.append(round(d, 3))
            continue
        parts = max(2, int(math.ceil(d / max_shot_s)))
        piece = d / parts
        # If the cap forces pieces below the flash floor, take fewer, longer shots instead: a
        # readable shot slightly over the cap beats a stutter of unreadable ones.
        while piece < MIN_SHOT_S and parts > 2:
            parts -= 1
            piece = d / parts
        out.extend([round(piece, 3)] * parts)
    return out


def transition_for(index: int) -> dict:
    """The move joining shot `index` to the next one.

    Cycled rather than random: an edit must be reproducible, and the same reel rendered twice
    should cut the same way. Varied rather than fixed, because one repeated move reads as a
    template — the tick of an automated edit.
    """
    return dict(_TRANSITIONS[int(index) % len(_TRANSITIONS)])
