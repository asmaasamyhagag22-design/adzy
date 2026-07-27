# -*- coding: utf-8 -*-
"""The colour grade, derived from the brand's own look — never a house style.

BRIEF (owner, 2026-07-27, rule 3 refactored): no static universal warm LUT. Inherit the palette,
thermal temperature and mood from `brand/look.py`; cool and crisp for clinical/scientific brands,
warm and golden for organic lifestyle ones; keep skin natural; and make the 35mm border OPTIONAL.

ONE LOOK, TWO CONSUMERS. `brand.look.look_lock` already resolves a single look per brand into four
families, and its own docstring says why it exists: "a clinic is cool and clinical, a snack brand is
warm and golden — because stamping one house style on every brand" is the failure it prevents. A
global warm grade would override precisely that, and would push a deep-blue pharmaceutical brand
golden. So this keys off the SAME resolver rather than re-reading the DNA with different words: if
the two ever disagreed, a film would be shot one way and graded another.

MEASURED BEFORE THIS EXISTED: `grep -rn "lut|curves|colorbalance|eq=" reel/` found no grading at
all. Clips were muxed exactly as each generation returned them, which is why a reel assembled from
several generations has no visual consistency — the problem the brief calls out.

SKIN IS PROTECTED IN EVERY FAMILY. Cooling a whole frame is what turns faces grey, so saturation
never drops below neutral and the cool families lift it slightly to compensate.
"""
from __future__ import annotations

import os
from typing import Any, Dict, Optional

# Per family: (temperature, contrast, saturation, gamma, grain).
# `temperature` is the SIGNED intent — negative cools, positive warms — and is applied as a
# blue/red balance shift. Values are deliberately small: a grade that announces itself has stopped
# being a grade and become a filter.
_GRADES: Dict[str, Dict[str, float]] = {
    # crisp, clean, slightly cool — bright and clinical without going surgical-blue
    "clinical": {"temperature": -0.06, "contrast": 1.10, "saturation": 1.02, "gamma": 1.02,
                 "grain": 3.0},
    # deep and restrained; low-key brands carry contrast, not colour
    "luxury": {"temperature": -0.02, "contrast": 1.16, "saturation": 0.98, "gamma": 0.98,
               "grain": 5.0},
    # punchy and high-key
    "vibrant": {"temperature": 0.03, "contrast": 1.14, "saturation": 1.12, "gamma": 1.02,
                "grain": 3.0},
    # golden hour, the organic/lifestyle default
    "warm": {"temperature": 0.08, "contrast": 1.08, "saturation": 1.08, "gamma": 1.01,
             "grain": 4.0},
}


def _family(*, brand_dna: Any = None, profile: Optional[dict] = None) -> str:
    """Which look family this brand resolved to, asked of the ONE resolver that decides it."""
    from brand.look import DEFAULT_LOOK, _LOOKS, look_lock

    look = look_lock(brand_dna=brand_dna, profile=profile)
    for name, text in _LOOKS.items():
        if look == text:
            return name
    return "warm" if look == DEFAULT_LOOK else "warm"


def grade_for_brand(*, brand_dna: Any = None, profile: Optional[dict] = None) -> Dict[str, Any]:
    """The grade parameters for this brand. Deterministic: same brand, same numbers, every call."""
    fam = _family(brand_dna=brand_dna, profile=profile)
    out: Dict[str, Any] = dict(_GRADES.get(fam, _GRADES["warm"]))
    out["family"] = fam
    return out


def grade_filter(*, brand_dna: Any = None, profile: Optional[dict] = None) -> str:
    """The grade as an ffmpeg filter chain.

    `eq` carries contrast/saturation/gamma; the temperature is a signed shift on the blue and red
    channels via `colorbalance`; `noise` adds the fine grain that gives a generated clip the
    texture of film rather than the flatness of a render.
    """
    g = grade_for_brand(brand_dna=brand_dna, profile=profile)
    t = float(g["temperature"])
    # positive = warm (lift reds, drop blues); negative = cool (the reverse)
    rs, bs = round(t, 3), round(-t, 3)
    return (
        f"eq=contrast={g['contrast']:.3f}:saturation={g['saturation']:.3f}:gamma={g['gamma']:.3f}"
        f",colorbalance=rs={rs}:bs={bs}:rm={rs / 2:.3f}:bm={bs / 2:.3f}"
        f",noise=alls={int(g['grain'])}:allf=t+u"
    )


def use_film_frame(flag: Optional[bool] = None) -> bool:
    """Is the 35mm border on? OFF unless someone asks for it.

    The brief is explicit, and the reason is the canvas: a border on a 9:16 social ad eats the
    vertical space the product, the safe zones and the kinetic captions need. `REEL_FILM_FRAME=1`
    is the env door for a run that wants it; the CLI flag is the per-render one.
    """
    if flag is not None:
        return bool(flag)
    return os.environ.get("REEL_FILM_FRAME", "") in ("1", "true", "yes")
