# -*- coding: utf-8 -*-
"""Pull every shot toward ONE anchor, so the cuts read as one film.

THE NUMBER THIS EXISTS FOR. Measured 2026-08-01 across 48 rendered reels on disk, per SHOT (cuts
found by scene score, the end-card excluded because it is a deliberate flat plate): the median luma
spread between shots of the same film is **90 of 255**, median sigma_luma **35**. A third of the
dynamic range, between one shot and the next, inside one advertisement.

`reel/grade.py` does NOT fix this and was never meant to. It applies the SAME grade to every shot,
which makes a film look intentional without making its shots agree — a warm grade over a bright
kitchen and a dark gym gives you a warm bright kitchen and a warm dark gym. Convergence needs a
per-shot correction, and that is this module.

ANCHOR, NOT NEIGHBOUR. Matching each shot to the one before it compounds: shot N ships corrected,
but its tail — N+1's reference — reflects a state N no longer occupies, so the film walks one cut
at a time and ends somewhere nobody chose. Every shot is measured against the SAME opening frame
instead, so `correction_i = f(anchor, shot_i)` and there is no chain to drift along. It also
answers the real complaint better: neighbour matching would happily permit a slow warm-to-cold walk
across a reel, which is exactly what the owner saw.

CLAMP, DON'T FAIL TO IDENTITY — and this is a deliberate divergence from `poster/product_composite
._harmonize`, whose caps this borrows. There, exceeding a cap returns the image untouched, because
over-correcting would destroy a REGISTERED BRAND COLOUR on a product label and identity is the safe
state. Here the input is generated footage with no registered colour to protect, and not correcting
leaves a visible exposure jump at the cut — the defect this exists to remove. Worse, the needed
correction is largest exactly when it matters most (bright kitchen cutting to dark gym), so failing
to identity would do nothing in the only case that motivated the feature. A partial correction
strictly reduces the visible jump.

FAILING TO IDENTITY IS STILL RIGHT WHEN THE MEASUREMENT IS UNRELIABLE, which is a different
question from "the correction is large". A missing frame, a near-black or blown reference, or two
frames that differ by more than 3x are not evidence — and inventing a correction from them is the
"absence read as success" shape this repo keeps paying for.
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# How much of the measured difference to take. A full correction makes every shot identical, which
# is not a film — it is a flat. Half closes the jump and leaves the director's intent visible.
PULL = 0.5

# Ceilings on one shot's correction. Looser than `_harmonize`'s 0.12/0.06 because there is no brand
# colour to protect here and the eye is far more forgiving of a moving image than of a packshot.
MAX_LUMA_SHIFT = 0.18
MAX_CHANNEL_SHIFT = 0.08

# Below/above these the frame is crushed or blown and its median says nothing about the shot.
_MIN_LUMA, _MAX_LUMA = 8.0, 247.0
# Past this the two shots are deliberately different exposures, not a mismatch to be corrected.
_MAX_RATIO = 3.0
# Under this the correction is invisible; emitting a filter for it costs a pass and says "graded".
_DEADBAND = 0.005


@dataclass(frozen=True)
class ShotMatch:
    """What one shot needs to sit where the anchor sits. `checked=False` means UNMEASURABLE —
    never "no correction needed", which is a different and much stronger claim."""
    checked: bool
    reason: str = ""
    luma_gain: float = 1.0
    gains: tuple = (1.0, 1.0, 1.0)      # per-channel, exposure INCLUDED
    capped: bool = False
    src_luma: float = 0.0
    dst_luma: float = 0.0

    @property
    def is_identity(self) -> bool:
        return all(abs(g - 1.0) < _DEADBAND for g in self.gains)


def _median_rgb(png: "str | Path") -> Optional[list]:
    """Median, not mean. A film frame has a blown highlight or a black corner that drags a mean;
    `_harmonize` uses the mean because it measures a uniform packshot, which this is not."""
    try:
        from PIL import Image, ImageStat
        im = Image.open(str(png)).convert("RGB")
        if max(im.size) > 160:                     # detail is noise for this measurement
            im = im.resize((160, max(1, int(160 * im.height / im.width))))
        return list(ImageStat.Stat(im).median)
    except Exception:  # noqa: BLE001
        return None


def measure(anchor: "str | Path | None", frame: "str | Path | None") -> ShotMatch:
    """What this shot needs to sit where the anchor sits. Never raises."""
    if not anchor or not frame:
        return ShotMatch(checked=False, reason="no anchor frame")
    dst, src = _median_rgb(anchor), _median_rgb(frame)
    if dst is None or src is None:
        return ShotMatch(checked=False, reason="a frame could not be read")

    dst_l, src_l = sum(dst) / 3.0, sum(src) / 3.0
    if not (_MIN_LUMA < dst_l < _MAX_LUMA) or not (_MIN_LUMA < src_l < _MAX_LUMA):
        return ShotMatch(checked=False, reason="a frame is crushed or blown",
                         src_luma=src_l, dst_luma=dst_l)
    ratio = max(dst_l, src_l) / max(1e-6, min(dst_l, src_l))
    if ratio > _MAX_RATIO:
        # A director who wrote a bright exterior cutting to a dark interior meant it. Flattening
        # that is not matching the film, it is deleting a decision.
        return ShotMatch(checked=False, reason=f"deliberate contrast ({ratio:.1f}x)",
                         src_luma=src_l, dst_luma=dst_l)

    capped = False
    luma_gain = 1.0 + PULL * (dst_l / src_l - 1.0)
    if abs(luma_gain - 1.0) > MAX_LUMA_SHIFT:
        luma_gain = 1.0 + math.copysign(MAX_LUMA_SHIFT, luma_gain - 1.0)
        capped = True

    gains = []
    for i in range(3):
        # Exposure divided out first, exactly as `_harmonize` does it, so the channel term is a
        # COLOUR TEMPERATURE reading and not a second copy of the brightness difference.
        cast = (dst[i] / dst_l) / max(1e-6, (src[i] / src_l))
        cast = 1.0 + PULL * (cast - 1.0)
        if abs(cast - 1.0) > MAX_CHANNEL_SHIFT:
            cast = 1.0 + math.copysign(MAX_CHANNEL_SHIFT, cast - 1.0)
            capped = True
        gains.append(cast * luma_gain)

    return ShotMatch(checked=True, reason="measured", luma_gain=luma_gain,
                     gains=tuple(round(g, 4) for g in gains), capped=capped,
                     src_luma=src_l, dst_luma=dst_l)


def _gamma_for(gain: float, level: float) -> float:
    """The gamma that multiplies mid-level `level` by `gain`.

    A gain is multiplicative; ffmpeg's `eq=brightness` is additive, and mapping a measured ratio
    onto `colorbalance`'s units would be a guess needing its own calibration. Gamma is exact here:
    `eq` computes out = in**(1/gamma), so solving `level**(1/g) = gain*level` gives
    `gamma = ln(level) / ln(gain*level)`. It also cannot clip — black stays black, white stays
    white — which a gain applied directly cannot promise.
    """
    lvl = min(0.95, max(0.02, float(level)))
    tgt = min(0.98, max(0.02, float(gain) * lvl))
    if abs(math.log(tgt)) < 1e-9:
        return 1.0
    return max(0.4, min(2.5, math.log(lvl) / math.log(tgt)))


def match_filter(m: ShotMatch) -> str:
    """The correction as an ffmpeg fragment, or "" when there is nothing to do.

    Emitting nothing for a no-op matters: the ffmpeg command string is then itself the evidence
    that the feature fired, which is the direct test for this repo's "produced, never read" class.
    """
    if not m.checked or m.is_identity:
        return ""
    lvl = max(0.02, min(0.95, m.src_luma / 255.0))
    g = _gamma_for(m.luma_gain, lvl)
    gr, gg, gb = (_gamma_for(x, lvl) for x in m.gains)
    # `gamma` carries the exposure and the per-channel ones carry the cast on top of it, so each
    # channel gamma is divided by the master to avoid applying the exposure three times over.
    return (f"eq=gamma={g:.4f}:gamma_r={gr / g:.4f}:gamma_g={gg / g:.4f}:gamma_b={gb / g:.4f}")
