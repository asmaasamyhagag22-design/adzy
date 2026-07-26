# -*- coding: utf-8 -*-
"""A contrast RATIO on the rendered poster — not a judgement call about gradients.

REVIEWER (2026-07-26): «تشيك برمجي على WCAG contrast ratio بين النص والبكسلز اللي تحته. أقل من
4.5:1 = reject».

Every legibility fix in this repo so far has been an argument about gradient stops: strengthen
the scrim, move the stops, add a shadow. Each one was reasonable and each one shipped copy the
owner could not read, because nobody ever computed the number the standard is written in. The
measured case that finally forced this: white copy on a 185/255 band — a ratio of about 1.7:1
where 4.5:1 is the floor for body text.

Deterministic Pillow arithmetic. No model, no network, nothing to hallucinate. TRI-STATE: an
unreadable file is `checked=False, passed=None` — absence is never success.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

logger = logging.getLogger(__name__)

WCAG_BODY_MIN = 4.5          # the standard's floor for normal-size text
WCAG_LARGE_MIN = 3.0         # >= 24px bold / 18.66px — a display headline


def _srgb_channel(c: float) -> float:
    c = c / 255.0
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def relative_luminance(rgb) -> float:
    r, g, b = (_srgb_channel(float(v)) for v in rgb[:3])
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def wcag_ratio(fg, bg) -> float:
    """The WCAG 2.x contrast ratio between two colours: (L1 + 0.05) / (L2 + 0.05)."""
    l1, l2 = relative_luminance(fg), relative_luminance(bg)
    hi, lo = max(l1, l2), min(l1, l2)
    return (hi + 0.05) / (lo + 0.05)


def _hex_to_rgb(value: str):
    s = str(value or "").strip().lstrip("#")
    if len(s) == 3:
        s = "".join(c * 2 for c in s)
    if len(s) != 6:
        return (255, 255, 255)
    try:
        return tuple(int(s[i:i + 2], 16) for i in (0, 2, 4))
    except ValueError:
        return (255, 255, 255)


@dataclass
class ContrastVerdict:
    passed: Optional[bool] = None
    checked: bool = False
    worst_ratio: float = 0.0
    zones: List[dict] = field(default_factory=list)
    reason: str = ""


def check_copy_contrast(poster_png: "str | Path", *, zones, ink: str = "#ffffff",
                        minimum: float = WCAG_BODY_MIN) -> ContrastVerdict:
    """Measure the ink against what is actually behind it in each text zone.

    `zones` are (x, y, w, h) fractions of the canvas. The background under the copy is taken as
    the zone's MEDIAN pixel: a mean is dragged by the ink itself and by any bright highlight,
    while the median is what most of the copy actually sits on."""
    v = ContrastVerdict()
    try:
        from PIL import Image
        im = Image.open(str(poster_png)).convert("RGB")
    except Exception:  # noqa: BLE001
        v.reason = "could not read the poster"
        return v                                   # checked=False -> could not verify

    fg = _hex_to_rgb(ink)
    W, H = im.size
    worst = None
    for (zx, zy, zw, zh) in (zones or []):
        box = (max(0, int(W * zx)), max(0, int(H * zy)),
               min(W, int(W * (zx + zw))), min(H, int(H * (zy + zh))))
        if box[2] <= box[0] or box[3] <= box[1]:
            continue
        crop = im.crop(box)
        px = list(crop.getdata())
        if not px:
            continue
        px.sort(key=relative_luminance)
        bg = px[len(px) // 2]                       # median pixel of the zone
        ratio = wcag_ratio(fg, bg)
        v.zones.append({"box": [round(z, 4) for z in (zx, zy, zw, zh)],
                        "bg": list(bg), "ratio": round(ratio, 2)})
        worst = ratio if worst is None else min(worst, ratio)

    if worst is None:
        v.reason = "no measurable text zone"
        return v                                   # checked=False — nothing to judge
    v.checked = True
    v.worst_ratio = round(worst, 2)
    v.passed = worst >= float(minimum)
    if not v.passed:
        v.reason = (f"copy contrast {v.worst_ratio}:1 is below the {minimum}:1 floor — "
                    "the text is not readable on what is behind it")
        logger.info("[contrast] %s", v.reason)
    return v
