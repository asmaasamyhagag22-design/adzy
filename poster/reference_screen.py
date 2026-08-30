# -*- coding: utf-8 -*-
"""Is this image a STYLE reference, or is it a finished ad?

THE ROOT under every poster symptom of 2026-07-26. `poster.pipeline._brand_style_refs` collects
the brand's own images and attaches two of them to the image model as visual conditioning. For
Al Fouad those images are the site's HOMEPAGE BANNERS — two were downloaded and inspected:

    special_offer.jpg   a 3-D lettering graphic reading "Special Offer" — pure text
    Desktop_14.webp     7210x1685 — "Pharmacy You Can Trust · Licensed Pharmacist · Verified
                        Products · 15+ Years of Proven Experience · 17 Branches Across Egypt",
                        an "Explore More" button, the wordmark, and a pharmacist

Told "make it look like these", the model complied: garbled English ("FIC. TRUSTWORSHY"), a
fabricated wordmark ("FoMad"), and other brands' products lifted out of the banners. Every
symptom chased separately today traces here. The prompt said "no text, no logos" the whole
time — prose loses to pixels.

Deterministic, no model, no network: two measurements that separate a photograph from a laid-out
advertisement.

  * TEXT DENSITY — letterforms are dense, high-contrast, horizontally-repeating strokes. Measured
    as the fraction of rows whose horizontal-gradient energy exceeds a floor. A photograph of a
    shop, a face or a bottle scores low; a page of copy scores high.
  * ASPECT — a website hero is a letterbox (Desktop_14 is 4.3:1). Nothing shot as an image for a
    poster is that wide.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)

MAX_BANNER_RATIO = 2.2          # wider than this is a website hero, not a photograph
MAX_TEXT_DENSITY = 0.16         # fraction of rows that read as lines of type


@dataclass
class RefVerdict:
    usable: bool = False
    reason: str = ""
    text_density: Optional[float] = None
    aspect: Optional[float] = None


def _text_density(gray) -> float:
    """Fraction of image rows that look like a LINE OF TYPE.

    A row of text alternates ink and paper many times across its width, so its horizontal
    gradient has many strong sign changes. A photographic row — skin, sky, a bottle — changes
    slowly. Counting rows rather than pixels keeps a single dark object from scoring as text."""
    from PIL import ImageFilter, ImageStat

    w, h = gray.size
    if w < 8 or h < 8:
        return 0.0
    small = gray.resize((min(w, 480), min(h, 480)))
    edges = small.filter(ImageFilter.FIND_EDGES)
    sw, sh = edges.size
    px = edges.load()
    base = ImageStat.Stat(edges).mean[0] or 1.0
    floor = max(24.0, base * 2.2)
    texty = 0
    for y in range(sh):
        crossings = 0
        for x in range(1, sw):
            if px[x, y] >= floor and px[x - 1, y] < floor:
                crossings += 1
        # a line of type crosses ink/paper many times across the row
        if crossings >= max(6, sw // 40):
            texty += 1
    return texty / float(sh)


def screen_reference(data: bytes) -> RefVerdict:
    """Judge ONE candidate reference. Never raises; an unreadable asset is simply unusable."""
    v = RefVerdict()
    try:
        import io

        from PIL import Image
        im = Image.open(io.BytesIO(bytes(data or b""))).convert("RGB")
    except Exception:  # noqa: BLE001
        v.reason = "unreadable image"
        return v

    w, h = im.size
    if w < 32 or h < 32:
        v.reason = f"too small ({w}x{h})"
        return v
    v.aspect = round(max(w / h, h / w), 2)
    if (w / max(1, h)) > MAX_BANNER_RATIO:
        v.reason = f"website banner {v.aspect}:1 (over {MAX_BANNER_RATIO}:1)"
        return v

    v.text_density = round(_text_density(im.convert("L")), 3)
    if v.text_density > MAX_TEXT_DENSITY:
        v.reason = f"text density {v.text_density} (over {MAX_TEXT_DENSITY})"
        return v

    v.usable = True
    v.reason = f"clean (text {v.text_density}, aspect {v.aspect}:1)"
    return v
