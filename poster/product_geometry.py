# -*- coding: utf-8 -*-
"""Understand the product BEFORE anything is generated or pasted.

THE DEFECT THIS CLOSES (Nano Care, 2026-07-28, measured on a live run). The QA critic rejected
the poster three times out of three, twice in the same words:

    "The product boxes are floating illogically in the scene, which undermines the poster's
     credibility."

The cause was not the shadow and not the zone. The two halves of the pipeline had never been
introduced to each other:

    the packshot (`_nano_probe/11.jpg`)     the generated scene
    -----------------------------------     -------------------------------------
    shot from ABOVE, ~45 degrees down       eye level, horizon at the model's chest
    flat high-key studio light, no shadow   directional window light from the left
    TWO cartons leaning on each other       an ad for ONE named product
    no flat footprint at all                a marble floor across the lower third

A packshot photographed from above cannot stand on a floor photographed from eye level. That is
a contradiction in geometry, and no zone, scale or contact shadow can resolve it — the module
next door already knew the principle («a shadow under a base drawn in mid-air is a shadow in
mid-air», product_composite.py) but treated it as a reason to move the paste rather than a
reason to measure the camera.

So the asset is measured ONCE, here, and everything downstream is derived from the measurement:
the scene is prompted to be photographed the way the product was, and the paste is placed and lit
to match. The asset itself is still NEVER shown to the image generator — that ruling was measured
on 2026-07-26 and stands (a reference the model can see is a reference it redraws, and a redrawn
registered label is a trademark distortion). The asset informs the prompt in WORDS and informs
the composite in PIXELS.

Cached by content hash, so re-running a brand costs nothing.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
from pathlib import Path
from typing import Any, Optional

from pydantic import BaseModel

logger = logging.getLogger(__name__)

# Every value a downstream decision is allowed to branch on. Anything else is "unknown", which
# must behave like "not measured" and never like a default that happens to be convenient.
ELEVATIONS = ("overhead", "high_angle", "eye_level", "low_angle", "unknown")
FORM_FACTORS = ("carton", "bottle", "tube", "sachet", "jar", "blister", "pouch", "can",
                "device", "apparel", "other")
SCALE_HINTS = ("palm", "two_hands", "large", "unknown")


class ProductGeometry(BaseModel):
    """What the product IS, as measured. `checked=False` means nothing was measured — the third
    state, so a caller can tell "we looked and found nothing" from "we never looked"."""

    camera_elevation: str = "unknown"
    # Clock position of the key light as seen in the packshot: 12 = from behind the camera's top,
    # 3 = from the right, 9 = from the left. 0 means undetermined.
    light_clock: int = 0
    light_hard: Optional[bool] = None
    unit_count: int = 0
    has_flat_base: Optional[bool] = None
    form_factor: str = "other"
    real_scale_hint: str = "unknown"
    # Deterministic Pillow measurements — available even with no vision caller at all.
    aspect: float = 1.0             # width / height of the trimmed subject
    mean_luma: float = 0.0          # 0..1, the subject's own brightness
    checked: bool = False
    reason: str = ""

    @property
    def is_overhead(self) -> bool:
        """True when the packshot looks DOWN at the product — the case that cannot stand on an
        eye-level floor no matter what the compositor does."""
        return self.camera_elevation in ("overhead", "high_angle")

    @property
    def can_stand(self) -> bool:
        """True only when the product was photographed level AND has something to stand on.
        `has_flat_base is None` (not measured) is deliberately NOT a yes."""
        return self.camera_elevation in ("eye_level", "low_angle") and self.has_flat_base is True


class _GeometryResponse(BaseModel):
    camera_elevation: str
    light_clock: int = 0
    light_hard: bool = False
    unit_count: int = 1
    has_flat_base: bool = False
    form_factor: str = "other"
    real_scale_hint: str = "unknown"


_SYSTEM = (
    "You are a PRODUCT PHOTOGRAPHER examining a packshot so it can be composited into a scene "
    "that will be photographed to match it. Report what the CAMERA and the LIGHT did — not what "
    "would look nice. Be literal.\n"
    "- camera_elevation: 'overhead' if the lens looks straight down and you can see the top face "
    "and no front face; 'high_angle' if it looks down at roughly 20-60 degrees and you can see "
    "BOTH the top face and a front/side face; 'eye_level' if the lens is level with the product "
    "and the top face is invisible or a thin sliver; 'low_angle' if it looks up at it.\n"
    "- light_clock: the clock position the KEY light comes from as seen in this photograph — 12 "
    "is from the top of the frame, 3 from the right, 6 from the bottom, 9 from the left. Use the "
    "direction the shadows and highlights say, and 0 if the lighting is so flat that no direction "
    "is readable.\n"
    "- light_hard: true if the shadows have crisp edges, false if they are soft or absent.\n"
    "- unit_count: how many SEPARATE physical units of the product are pictured. Two boxes of the "
    "same item is 2, not 1. A box next to its own sachet is 2.\n"
    "- has_flat_base: true ONLY if the product is resting squarely on a flat surface with a "
    "visible footprint it could stand on. A unit that is tilted, leaning on another unit, "
    "floating, or lying on its side is FALSE.\n"
    f"- form_factor: one of {', '.join(FORM_FACTORS)}.\n"
    "- real_scale_hint: 'palm' if a person could hold it in one hand, 'two_hands' if it needs "
    "both, 'large' if it is furniture-sized or bigger, 'unknown' if you cannot tell."
)


def _pillow_measurements(data: bytes) -> tuple[float, float]:
    """(aspect, mean_luma) of the subject — deterministic, no model, never raises."""
    try:
        import io

        from PIL import Image, ImageStat
        with Image.open(io.BytesIO(data)) as im:
            rgba = im.convert("RGBA")
            alpha = rgba.getchannel("A")
            box = alpha.getbbox() if alpha.getextrema()[0] < 250 else None
            subject = rgba.crop(box) if box else rgba
            w, h = subject.size
            aspect = float(w) / float(h) if h else 1.0
            luma = float(ImageStat.Stat(subject.convert("L")).mean[0]) / 255.0
            return round(aspect, 4), round(luma, 4)
    except Exception:  # noqa: BLE001
        return 1.0, 0.0


def _cache_path(digest: str) -> Path:
    root = Path(os.environ.get("POSTER_GEOMETRY_CACHE", "outputs/_product_geometry"))
    return root / f"{digest}.json"


def measure_product(asset: "bytes | str | Path", *, caller: Any = None,
                    use_cache: bool = True) -> ProductGeometry:
    """Measure one packshot. Returns `checked=False` rather than raising or guessing.

    A FAILED MEASUREMENT IS NEVER CACHED — the same mistake as caching a failed SFX render, and
    it would pin a brand to "unknown" forever after one transient 429."""
    from poster.product_composite import _load  # the loader that already handles url/path/bytes

    data: Optional[bytes]
    if isinstance(asset, (bytes, bytearray)):
        data = bytes(asset)
    else:
        img = _load(asset)
        if img is None:
            return ProductGeometry(reason="asset could not be loaded")
        import io
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        data = buf.getvalue()
    if not data:
        return ProductGeometry(reason="empty asset")

    aspect, luma = _pillow_measurements(data)
    digest = hashlib.sha1(data).hexdigest()[:16]
    cache = _cache_path(digest)
    if use_cache:
        try:
            if cache.exists():
                return ProductGeometry(**json.loads(cache.read_text(encoding="utf-8")))
        except Exception:  # noqa: BLE001
            pass

    if caller is None:
        return ProductGeometry(aspect=aspect, mean_luma=luma,
                               reason="no vision caller: geometry not measured")

    try:
        resp, _u = caller(_SYSTEM, "Describe this packshot's camera, light and units.",
                          _GeometryResponse, group_name="product_geometry",
                          images=[(data, "image/png")])
    except Exception as exc:  # noqa: BLE001
        return ProductGeometry(aspect=aspect, mean_luma=luma,
                               reason=f"geometry call failed ({type(exc).__name__})")

    elevation = str(getattr(resp, "camera_elevation", "") or "").strip().lower()
    if elevation not in ELEVATIONS:
        elevation = "unknown"
    form = str(getattr(resp, "form_factor", "") or "").strip().lower()
    if form not in FORM_FACTORS:
        form = "other"
    hint = str(getattr(resp, "real_scale_hint", "") or "").strip().lower()
    if hint not in SCALE_HINTS:
        hint = "unknown"
    clock = int(getattr(resp, "light_clock", 0) or 0)
    if not 0 <= clock <= 12:
        clock = 0

    geo = ProductGeometry(
        camera_elevation=elevation,
        light_clock=clock,
        light_hard=bool(getattr(resp, "light_hard", False)),
        unit_count=max(0, int(getattr(resp, "unit_count", 0) or 0)),
        has_flat_base=bool(getattr(resp, "has_flat_base", False)),
        form_factor=form,
        real_scale_hint=hint,
        aspect=aspect,
        mean_luma=luma,
        checked=True,
    )
    logger.info("[geometry] %s · light %sh · %s unit(s) · base=%s · %s",
                geo.camera_elevation, geo.light_clock, geo.unit_count,
                geo.has_flat_base, geo.form_factor)
    if use_cache:
        try:
            cache.parent.mkdir(parents=True, exist_ok=True)
            cache.write_text(geo.model_dump_json(), encoding="utf-8")
        except Exception:  # noqa: BLE001
            pass
    return geo
