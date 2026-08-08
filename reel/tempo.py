# -*- coding: utf-8 -*-
"""The film's tempo — derived, not detected, because we author the sound ourselves.

Every agency cuts to the track. This pipeline had **no notion of tempo at all**: the only BPM code
in the repo (`reel/motion.py::_grid_durations`) has no production caller, and `reel/sound.py`'s own
docstring claims "the cuts beat-sync to its bpm" about a compositor that has never known what a
beat is. So the cuts landed wherever the copy happened to end.

The usual way to fix that is to license a track and detect its tempo. We don't need to: when no
`--music` is supplied, `reel/sound.py::synth_sound_bed` SYNTHESIZES the bed we ship. We are the
composer. So the tempo is a decision, not a measurement — no vendor, no licence question for a
client-sold reel, and no octave error.

TWO PROPERTIES MAKE THIS EXACT RATHER THAN APPROXIMATE.

1. **The BPMs are chosen so a beat is a whole number of frames.** At 30fps a beat is `60*fps/bpm`
   frames, so only `bpm = 1800/k` lands on the frame grid. Every value below does. That means
   "on the beat" and "on the frame" are the SAME lattice — a cut never has to fall between two
   frames and get rounded to whichever is nearer.

   The honest limit is downstream, not here: the compositor renders each scene with `-t {d:.2f}`,
   so a cut lands within 10ms of its beat — under a third of a frame. The grid itself is exact.

2. **Cumulative starts are quantised, never individual durations.** Rounding N independent lengths
   accumulates the error — the identical defect `reel/pacing.py` was just fixed for, where
   `[round(d/parts,3)] * parts` made a 5.000s scene sum to 5.001. Snapping the START of each cut
   and taking differences makes every cut land on a beat by construction.

D-8: the tempo keys off the SAME resolver as the look and the grade (`brand.look.look_lock` ->
family), never off a country, a language or a category. A luxury house is unhurried and a youth
brand is fast because of what the brand IS, and that resolution already exists — recomputing it
here with different words is how a film ends up shot one way and cut to another.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Optional, Sequence

logger = logging.getLogger(__name__)

# Per look family: beats per minute. Every value divides 1800 exactly, so at 30fps the beat is a
# whole number of frames (shown). Change one and the frame-exactness test will say so.
#   luxury  75 -> 24 frames   held; lets a shot land
#   warm    90 -> 20 frames   relaxed lifestyle pace
#   clinical 100 -> 18 frames measured, precise
#   vibrant 120 -> 15 frames  punchy
_BPM = {"luxury": 75.0, "warm": 90.0, "clinical": 100.0, "vibrant": 120.0}
_DEFAULT_BPM = 90.0

# A cut that lands within this of a beat is already on it; nudging it further is noise.
_EPS = 1e-6


@dataclass(frozen=True)
class BeatGrid:
    bpm: float
    beat_s: float
    family: str
    frames_per_beat: float
    reason: str = ""

    @property
    def frame_exact(self) -> bool:
        return abs(self.frames_per_beat - round(self.frames_per_beat)) < 1e-9


def look_family(*, brand_dna: Any = None, profile: Optional[dict] = None) -> str:
    """Which look family this brand resolved to — asked of the ONE resolver that decides it.

    `reel/grade.py` had this as a private helper; the tempo is its third consumer, so it lives
    where the other two can see it. Delegating rather than re-deriving is the whole point: if the
    three disagreed, a film would be shot one way, graded another and cut to a third.
    """
    try:
        from brand.look import DEFAULT_LOOK, _LOOKS, look_lock
        look = look_lock(brand_dna=brand_dna, profile=profile)
        for name, text in _LOOKS.items():
            if look == text:
                return name
        return "warm" if look == DEFAULT_LOOK else "warm"
    except Exception:  # noqa: BLE001 — no DNA, no crash; the default family is a real answer
        return "warm"


def beat_grid_for_brand(*, brand_dna: Any = None, profile: Optional[dict] = None,
                        fps: int = 30) -> BeatGrid:
    """This brand's tempo. Deterministic: same brand, same grid, every render."""
    fam = look_family(brand_dna=brand_dna, profile=profile)
    bpm = _BPM.get(fam, _DEFAULT_BPM)
    beat_s = 60.0 / bpm
    fpb = float(fps) * beat_s
    reason = f"{fam} brand -> {bpm:.0f} BPM"
    if abs(fpb - round(fpb)) > 1e-9:
        # Honest rather than silent: at an unusual fps the beat is not frame-exact and the phase
        # error is real, so it gets named instead of being quietly rounded away.
        reason += f" (not frame-exact at {fps}fps: {fpb:.3f} frames/beat)"
    return BeatGrid(bpm=bpm, beat_s=beat_s, family=fam, frames_per_beat=fpb, reason=reason)


def quantise(durations: Sequence[float], floors: Sequence[float], beat_s: float) -> list[float]:
    """Cut lengths snapped to the beat grid, with every floor respected.

    THE FLOORS WIN, AND THE GRID MAY ONLY ROUND UP AGAINST THEM. The floors are legibility
    contracts the owner has already rejected a reel over twice — «الكلام سريع» and "the text goes
    by too fast" — and are computed upstream from the words each scene has to speak and show. The
    beat is an aesthetic contract. If the beat won, a caption would be clipped mid-read to buy a
    rhythm nobody asked for, so a snap that would land under a floor snaps to the next beat up.

    Quantising CUMULATIVE STARTS, not individual lengths: the returned durations are differences
    between snapped boundaries, so every cut is exactly on a beat and the error cannot accumulate.

    Returns the input unchanged when there is no usable grid — never a guessed one.
    """
    ds = [float(d) for d in (durations or [])]
    if not ds or float(beat_s) <= 0:
        return ds
    fl = [float(f) for f in (floors or [])]
    fl += [0.0] * (len(ds) - len(fl))

    out: list[float] = []
    start = 0.0
    for i, d in enumerate(ds):
        want_end = start + max(d, fl[i])
        k = round(want_end / beat_s)
        end = k * beat_s
        # round UP whenever the nearest beat would break this scene's floor
        while end < start + fl[i] - _EPS or end <= start + _EPS:
            k += 1
            end = k * beat_s
        # NOT ROUNDED. Rounding each difference re-accumulates exactly the error this function
        # exists to remove — measured at 0.07ms over four cuts at 4 decimal places, which is
        # nothing, but it is the same shape as the `pacing.py` drift and there is no reason to
        # reintroduce it here. The render's own `-t {d:.2f}` is the real resolution limit.
        out.append(end - start)
        start = end
    return out
