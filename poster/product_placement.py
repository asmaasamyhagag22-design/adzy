# -*- coding: utf-8 -*-
"""WHERE the product goes, decided ONCE, before anything is generated.

OWNER'S RULING (2026-07-28): «مينفعش يكون مكان ثابت — لازم نفهم المنتج وعلى أساسه نقرر مساحته
وحجمه، وكل الاحتمالات بناءً ع المنتج والبوستر مكتوب عليه إيه».

Until now the answer was a module-level constant — `DEFAULT_ZONE = (0.54, 0.50, 0.34)` — chosen
once for one brand and applied to every product of every shape in every layout. That constant is
why the same owner also said «اللزق بيبوّظ الباك جراوند اللي اتعمل ليها جينيريشن»: the paste
landed wherever the constant pointed, on top of a composition that had been generated for
something else entirely.

THE ORDER OF OPERATIONS IS THE FIX. The decision comes FIRST and both halves obey it:

    measure the asset  ->  read the copy  ->  DECIDE  ->  generate the scene FROM the decision
                                              |          ->  paste at the decision
                                              +-- the single point of truth

A background generated for this exact camera, this exact surface and this exact empty area
cannot be "ruined" by the paste, because the paste is what it was rendered to receive.

Deterministic: no model is called here. Everything is arithmetic over the measured geometry
(`poster.product_geometry`) and the layout the copy already occupies.
"""
from __future__ import annotations

import logging
import os
from typing import Any, Optional

from pydantic import BaseModel

logger = logging.getLogger(__name__)

# The four ways a real product appears in a real photograph. Anything not on this list is a
# composite defect wearing a name.
MODE_FLAT_LAY = "flat_lay_overhead"
MODE_STANDING = "standing_on_surface"
MODE_HELD = "held_in_hand"
MODE_HERO = "hero_foreground"

# Height as a fraction of the canvas, by what the product physically IS. The ceiling exists
# because of a measured defect: at 0.44 the Nano Care carton was "the size of the park bench
# beside it" and the critic rejected the poster three times.
_BASE_HEIGHT = {
    "sachet": 0.15, "blister": 0.15, "tube": 0.18, "pouch": 0.20,
    "carton": 0.24, "jar": 0.22, "can": 0.20, "bottle": 0.26,
    "device": 0.28, "apparel": 0.34, "other": 0.22,
}
_SCALE_FACTOR = {"palm": 0.85, "two_hands": 1.0, "large": 1.30, "unknown": 1.0}

MAX_HEIGHT_FRAC = 0.34     # never a third of the canvas or more
MIN_HEIGHT_FRAC = 0.10     # below this the product is decoration, not the subject

# THE HYBRID NEEDS THE PRODUCT BIG, for two independent reasons that happen to agree.
#
# MEASURED (2026-07-28). A tight product shot — the box filling much of the frame — matched the
# real packshot at 33/97 correspondences. The same technique on production scenes, where the
# placement sized the carton at 20% of the canvas height, found 10 to 23 and the geometry gates
# refused nearly all of them. SIFT has nothing to key on in two hundred pixels of carton.
#
# And the owner's own reason for the hybrid says the same thing from the other side: «العميل ممكن
# يكبر الصورة عشان يقرا المكونات أو يشوف الترخيص». A label nobody can read did not need its
# lettering corrected in the first place.
HYBRID_MIN_HEIGHT_FRAC = 0.26
_COPY_BAND_H = 0.22        # what `_copy_zones` assumes a text_box occupies vertically
_MARGIN = 0.04             # breathing room between the product and anything else


class PlacementDecision(BaseModel):
    """One decision, honoured by the prompt and by the compositor."""

    mode: str = MODE_HERO
    # (x, y, height) as canvas fractions; x/y are the TOP-LEFT corner — the same shape the
    # compositor has always taken, so nothing downstream needs to learn a new convention.
    zone: tuple[float, float, float] = (0.54, 0.50, 0.22)
    # The clock position the SCENE's key light must come from, carried over from the packshot so
    # the two halves are lit from the same side. 0 = the asset's lighting was unreadable.
    light_clock: int = 0
    light_hard: bool = False
    # False when the asset was never measured — the scene guard then keeps its generic wording
    # instead of confidently asking for a camera angle nobody verified.
    confident: bool = False
    reason: str = ""

    @property
    def height_frac(self) -> float:
        return float(self.zone[2])


def zone_rect(zone, *, aspect: float, canvas_aspect: float) -> tuple:
    """(x, y, w, h) of the product's footprint in canvas fractions.

    The compositor scales by HEIGHT, so the width a zone actually consumes depends on both the
    product's own aspect and the canvas's. Callers that need to keep the copy clear must reason
    about this rectangle, not about the (x, y, h) triple."""
    x, y, h = float(zone[0]), float(zone[1]), float(zone[2])
    w = h * float(aspect) / float(canvas_aspect or 1.0)
    return (x, y, min(w, 1.0), h)


def _rects_overlap(a, b) -> bool:
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    return not (ax + aw <= bx or bx + bw <= ax or ay + ah <= by or by + bh <= ay)


def _copy_rect(text_box) -> Optional[tuple]:
    """The copy's footprint as (x, y, w, h). `text_box` is the (x, y, w) triple the poster spec
    carries; its height is the band `_copy_zones` measures."""
    try:
        box = list(text_box or [])
        if len(box) < 3:
            return None
        x, y, w = float(box[0]), float(box[1]), float(box[2])
    except Exception:  # noqa: BLE001
        return None
    return (max(0.0, x), max(0.0, y), min(w, 1.0), _COPY_BAND_H)


def _height_for(geo, *, hybrid: bool = False) -> float:
    base = _BASE_HEIGHT.get(getattr(geo, "form_factor", "other"), _BASE_HEIGHT["other"])
    base *= _SCALE_FACTOR.get(getattr(geo, "real_scale_hint", "unknown"), 1.0)
    floor = HYBRID_MIN_HEIGHT_FRAC if hybrid else MIN_HEIGHT_FRAC
    return max(floor, min(MAX_HEIGHT_FRAC, base))


def _mode_for(geo, *, has_person: bool, allow_held: bool) -> tuple[str, str]:
    """The mode, and why — every branch is a measured property of the asset, never a preference."""
    if getattr(geo, "is_overhead", False):
        # The case that broke Nano Care: a top-down packshot can only be believed on a top-down
        # scene. Matching the camera is the ONLY resolution; every other lever is cosmetic.
        return MODE_FLAT_LAY, "the packshot looks down at the product, so the scene must too"
    if allow_held and has_person and getattr(geo, "real_scale_hint", "") == "palm":
        return MODE_HELD, "a palm-sized product and a person in frame"
    if getattr(geo, "can_stand", False):
        return MODE_STANDING, "photographed level with a flat base to stand on"
    return MODE_HERO, "no verified base and no matching surface — carry it in the foreground"


def decide_placement(geometry, *, text_box=None, canvas_aspect: float = 0.8,
                     has_person: bool = False, allow_held: Optional[bool] = None,
                     hybrid: bool = False) -> PlacementDecision:
    """The placement, derived from what the product IS and what the poster SAYS.

    `canvas_aspect` is width/height of the poster (0.8 for the 4:5 the pipeline renders).
    `allow_held` defaults to the POSTER_ALLOW_HELD env flag — held-in-hand is the most convincing
    mode and the hardest to land, so it stays opt-in until it has been measured on its own."""
    if allow_held is None:
        allow_held = os.environ.get("POSTER_ALLOW_HELD") == "1"

    checked = bool(getattr(geometry, "checked", False))
    mode, why = _mode_for(geometry, has_person=has_person, allow_held=allow_held)
    h = _height_for(geometry, hybrid=hybrid)
    aspect = float(getattr(geometry, "aspect", 1.0) or 1.0)

    # WHERE THE COPY IS NOT. The product may not be sized or placed without knowing what the
    # poster says — that relationship is the owner's «والبوستر مكتوب عليه إيه».
    copy = _copy_rect(text_box)
    top_free = (0.0, max(0.0, copy[1] - _MARGIN)) if copy else (0.0, 1.0)
    bot_free = (min(1.0, copy[1] + copy[3] + _MARGIN), 1.0) if copy else (0.0, 1.0)
    band = max((top_free, bot_free), key=lambda b: b[1] - b[0])
    band_h = band[1] - band[0]

    if band_h < MIN_HEIGHT_FRAC + _MARGIN and copy:
        # The copy leaves no clear band at all, so share a band and separate HORIZONTALLY instead.
        band = (0.0, 1.0)
        band_h = 1.0
        side_left = copy[0]
        side_right = 1.0 - (copy[0] + copy[2])
        x = _MARGIN if side_left >= side_right else 1.0 - _MARGIN
        h = min(h, MAX_HEIGHT_FRAC)
        w = h * aspect / (canvas_aspect or 1.0)
        x = max(_MARGIN, min(x if side_left >= side_right else 1.0 - _MARGIN - w, 1.0 - w - _MARGIN))
        y = max(0.0, min(copy[1] + (copy[3] - h) / 2.0, 1.0 - h))
        decision = PlacementDecision(
            mode=mode, zone=(round(x, 4), round(y, 4), round(h, 4)),
            light_clock=int(getattr(geometry, "light_clock", 0) or 0),
            light_hard=bool(getattr(geometry, "light_hard", False)),
            confident=checked,
            reason=f"{why}; beside the copy (no clear band)")
        logger.info("[placement] %s zone=%s (%s)", decision.mode, decision.zone, decision.reason)
        return decision

    # Fit the product inside the clear band with margins on both sides of it.
    _floor = HYBRID_MIN_HEIGHT_FRAC if hybrid else MIN_HEIGHT_FRAC
    h = min(h, max(_floor, band_h - 2 * _MARGIN))
    w = h * aspect / (canvas_aspect or 1.0)
    if w > 0.62:                                  # never wider than the room a poster can give it
        w = 0.62
        h = w * (canvas_aspect or 1.0) / max(aspect, 1e-6)

    if mode == MODE_STANDING:
        # The FEET are the point: the base sits near the bottom of its band, where a generated
        # scene puts its floor and where a person expects a product to stand.
        y = band[1] - _MARGIN - h
    elif mode == MODE_FLAT_LAY:
        y = band[0] + (band_h - h) / 2.0          # lying on a surface seen from above: centred
    elif mode == MODE_HELD:
        y = band[0] + (band_h - h) * 0.45         # roughly chest height within the band
    else:
        y = band[1] - _MARGIN - h                 # hero: forward and low, like a foreground object

    # Off-centre horizontally — a product dead-centre under centred copy reads as a diagram.
    x = 0.5 - w / 2.0 + (0.10 if (copy and copy[0] < 0.2) else -0.06)
    x = max(_MARGIN, min(x, 1.0 - w - _MARGIN))
    y = max(0.0, min(y, 1.0 - h))

    decision = PlacementDecision(
        mode=mode, zone=(round(x, 4), round(y, 4), round(h, 4)),
        light_clock=int(getattr(geometry, "light_clock", 0) or 0),
        light_hard=bool(getattr(geometry, "light_hard", False)),
        confident=checked, reason=why)

    if copy:
        rect = zone_rect(decision.zone, aspect=aspect, canvas_aspect=canvas_aspect)
        if _rects_overlap(rect, copy):
            logger.warning("[placement] product %s still meets the copy %s", rect, copy)
    logger.info("[placement] %s zone=%s h=%s (%s)",
                decision.mode, decision.zone, decision.height_frac, decision.reason)
    return decision


def scene_instruction(decision: PlacementDecision, *, hybrid: bool = False) -> str:
    """What the SCENE must be photographed like, given where the product goes.

    Two different jobs behind one decision:

    * `hybrid=False` (the CUT-AND-PASTE lane) — the generator is never shown the packshot, so
      this describes only the CAMERA, the SURFACE and the LIGHT the product's own photograph
      implies, and reserves its area empty for the paste.
    * `hybrid=True` (the owner's production rule, 2026-07-28) — the model DRAWS the product from
      the attached packshot, because its lighting, contact shadow and 3-D angle are the things
      no deterministic paste can manufacture. Here the same decision becomes art direction:
      put it THERE, at THAT size. Its label is corrected afterwards by
      `poster.label_reprojection`, so the model is free to be a photographer rather than a
      typesetter."""
    if not decision.confident:
        return ""
    x, y, h = decision.zone
    where = (f"the area from {int(x * 100)}% across and {int(y * 100)}% down the frame, "
             f"about {int(h * 100)}% of the frame tall")
    if hybrid:
        anchor = {
            MODE_FLAT_LAY: ("lying on a surface seen from above, the way it was photographed"),
            MODE_STANDING: ("standing squarely on a real surface — a counter, a table, a ledge — "
                            "with a true contact shadow where it meets it"),
            MODE_HELD: "held naturally in a person's hand, fingers closing around it",
            MODE_HERO: "placed in the near foreground, resting on something real",
        }[decision.mode]
        lines = [
            f"PLACE THE PRODUCT: put it at {where}, {anchor}. It must sit in the scene's own "
            "light and cast the shadow that light implies — this is a photograph of the product "
            "in this place, not a cut-out laid over a backdrop.",
            "Show exactly ONE unit of it, and nothing else of the kind.",
        ]
        if decision.light_clock:
            lines.append("Light it with the scene's own key light; every shadow in the frame "
                         "must agree with one direction.")
        return "\n" + "\n".join(lines)
    lines = []

    if decision.mode == MODE_FLAT_LAY:
        lines.append(
            "CAMERA: shoot this scene LOOKING DOWN from above at roughly the same steep angle a "
            "product would be photographed from — a table, counter or tray seen from overhead. "
            "Do NOT shoot it at eye level with a horizon.")
        lines.append(f"Keep {where} a clean, empty, unobstructed patch of that surface.")
    elif decision.mode == MODE_STANDING:
        lines.append(
            "CAMERA: shoot this scene at EYE LEVEL with the subject, level with the surface the "
            "product will stand on — no steep downward angle.")
        lines.append(
            f"Put a flat, horizontal surface — a table top, a counter, a stone ledge — so that "
            f"{where} is clear, empty and standing room, lit so an object placed there would cast "
            "its shadow onto that surface.")
    elif decision.mode == MODE_HELD:
        lines.append(
            "CAMERA: eye level with the person. One hand is presenting something to the camera "
            "with an OPEN, RELAXED GRIP — fingers curved as if around a box, but the hand is "
            f"EMPTY. The grip sits at {where}. Do not draw anything in the hand.")
    else:
        lines.append(
            f"Keep {where} clean and uncluttered with a simple, believable foreground — an object "
            "will occupy it.")

    if decision.light_clock:
        clock = decision.light_clock
        side = {12: "from directly above", 1: "from the upper right", 2: "from the upper right",
                3: "from the right", 4: "from the lower right", 5: "from the lower right",
                6: "from the front, below the subject", 7: "from the lower left",
                8: "from the lower left", 9: "from the left", 10: "from the upper left",
                11: "from the upper left"}.get(clock, "from the upper left")
        quality = "hard, with crisp shadow edges" if decision.light_hard else "soft and diffused"
        lines.append(f"LIGHT: the key light comes {side} and is {quality}. Every shadow in the "
                     "frame must agree with that one direction.")
    return "\n" + "\n".join(lines)
