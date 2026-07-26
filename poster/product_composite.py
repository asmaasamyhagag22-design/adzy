# -*- coding: utf-8 -*-
"""Paste the REAL product onto the generated scene. The model never draws it.

THE BLOCKER THIS CLOSES (2026-07-26, Al Fouad × La Roche-Posay). Asked to render a real,
registered product — with its own photo attached as a reference, in both engines, on every
attempt of every run this week — the image model rewrote the label:

    on the poster                          the truth
    LABORATONE DERHATOLOGIQUE              LABORATOIRE DERMATOLOGIQUE
    ANTFLICHES / AXTI-DARC SPOIS FLUID     ANTI-TACHES / ANTI-DARK SPOTS FLUID

For a pharmacy advertising the genuine article that is a trademark distortion, not a taste
defect, and no prompt wording has ever fixed it: reproducing text is simply not something these
models do. So the division of labour changes — the scene model renders the BACKGROUND and the
PEOPLE, and the product arrives as its own real asset and is composited in. Then the label is
pixel-identical by construction: «عايزة المنتج يجي زي ما هو بالظبط ع الويب سايت».

Everything here is deterministic Pillow work: no model, no network beyond fetching the asset
the caller already has a URL for, nothing to hallucinate.
"""
from __future__ import annotations

import io
import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# (x, y, height) of the product box as fractions of the canvas — x/y are the TOP-LEFT.
DEFAULT_ZONE = (0.52, 0.34, 0.44)


def _load(asset: "bytes | str | Path") -> Optional["object"]:
    from PIL import Image
    try:
        if isinstance(asset, (bytes, bytearray)):
            if not asset:
                return None
            return Image.open(io.BytesIO(bytes(asset))).convert("RGBA")
        p = str(asset)
        if p.startswith(("http://", "https://")):
            from reel.video_provider import _load_reference_image
            loaded = _load_reference_image(p)
            if not loaded:
                return None
            return Image.open(io.BytesIO(loaded[0])).convert("RGBA")
        return Image.open(p).convert("RGBA")
    except Exception:  # noqa: BLE001
        return None


def _key_studio_background(im):
    """Key the asset's own studio background to alpha — or say it cannot be done.

    Store product photos are rarely cut-outs: they are STUDIO SHOTS with their own gradient
    ground, their own props, their own reflections. MEASURED on the v7 render («البانل الملزوق
    باين كمستطيل… الحد السفلي فرق 177 مستوى سطوع»): white-keying strips only a FLAT border, so
    the whole rectangle shipped as a card — with the shot's own heart prop inside it, doubling
    the background's motif («قلبين مختلفين في نفس الكادر»).

    The key is border-connected on the border's own MEDIAN colour with tolerance, so any
    near-uniform studio ground (white, grey, gradient-ish) strips. Returns None when the border
    is genuinely photographic (busy edges) — the caller must then REFUSE to paste, because a
    card glued onto the poster is worse than no product."""
    from PIL import Image
    try:
        alpha = im.getchannel("A")
        if alpha.getextrema()[0] < 250:
            return im                       # already a real cut-out

        w, h = im.size
        px = im.load()
        border = ([(x, 0) for x in range(w)] + [(x, h - 1) for x in range(w)]
                  + [(0, y) for y in range(h)] + [(w - 1, y) for y in range(h)])
        cols = sorted(px[x, y][:3] for (x, y) in border[:: max(1, len(border) // 400)])
        ref = cols[len(cols) // 2]

        def _near_ref(c, tol=42):
            return (abs(c[0] - ref[0]) <= tol and abs(c[1] - ref[1]) <= tol
                    and abs(c[2] - ref[2]) <= tol)

        # busy border = photographic scene -> keying would eat the subject; refuse instead
        matching = sum(1 for (x, y) in border[:: max(1, len(border) // 400)]
                       if _near_ref(px[x, y][:3]))
        total = len(border[:: max(1, len(border) // 400)])
        if matching / max(1, total) < 0.85:
            return None

        # border-connected flood fill on the reference tone
        out = im.copy()
        opx = out.load()
        seen = bytearray(w * h)
        stack = [(x, y) for (x, y) in border if _near_ref(px[x, y][:3])]
        for (x, y) in stack:
            seen[y * w + x] = 1
        while stack:
            x, y = stack.pop()
            r, g, b, a = opx[x, y]
            opx[x, y] = (r, g, b, 0)
            for nx, ny in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
                if 0 <= nx < w and 0 <= ny < h and not seen[ny * w + nx] \
                        and _near_ref(px[nx, ny][:3]):
                    seen[ny * w + nx] = 1
                    stack.append((nx, ny))
        kept = out.getchannel("A").getextrema()[1]
        if kept == 0:
            return None                     # keying erased everything — not a usable asset
        return out
    except Exception:  # noqa: BLE001
        return None


def _trim(im):
    box = im.getchannel("A").getbbox()
    return im.crop(box) if box else im


def composite_product(background: "str | Path", asset: "bytes | str | Path", *,
                      zone: tuple = DEFAULT_ZONE, contact_shadow: bool = True,
                      out_path: "str | Path | None" = None) -> Optional[str]:
    """Paste `asset` onto `background` inside `zone`. Returns the new path, or None when the
    asset is unusable — a caller that gets None must NOT claim the real product is shown."""
    from PIL import Image, ImageFilter

    try:
        bg = Image.open(str(background)).convert("RGBA")
    except Exception:  # noqa: BLE001
        return None
    prod = _load(asset)
    if prod is None:
        return None
    keyed = _key_studio_background(prod)
    if keyed is None:
        # A photographic panel pasted onto a poster is a CARD, not a product («البانل الملزوق»).
        # No product beats a glued rectangle.
        logger.info("[product] the asset's background is photographic and cannot be keyed — "
                    "not compositing")
        return None
    prod = _trim(keyed)
    if prod.width < 8 or prod.height < 8:
        return None

    W, H = bg.size
    zx, zy, zh = (list(zone) + list(DEFAULT_ZONE))[:3]
    target_h = max(8, int(H * float(zh)))
    scale = target_h / prod.height
    target_w = max(8, int(prod.width * scale))
    if target_w > W * 0.6:                        # never wider than the room it has
        scale = (W * 0.6) / prod.width
        target_w, target_h = int(prod.width * scale), int(prod.height * scale)
    prod = prod.resize((target_w, target_h), Image.LANCZOS)

    x = int(W * float(zx))
    y = int(H * float(zy))
    x = max(0, min(x, W - target_w))
    y = max(0, min(y, H - target_h))

    layer = Image.new("RGBA", bg.size, (0, 0, 0, 0))
    if contact_shadow:
        # A soft ellipse where the product meets the surface — the reviewer's «العبوة طايرة
        # مش واقفة على حاجة». Deterministic, and it is what makes a paste read as a photograph.
        shadow = Image.new("RGBA", bg.size, (0, 0, 0, 0))
        from PIL import ImageDraw
        d = ImageDraw.Draw(shadow)
        sw = int(target_w * 1.15)
        sh = max(6, int(target_h * 0.09))
        sx = x + target_w // 2 - sw // 2
        sy = y + target_h - sh // 2
        d.ellipse([sx, sy, sx + sw, sy + sh], fill=(0, 0, 0, 120))
        shadow = shadow.filter(ImageFilter.GaussianBlur(radius=max(6, sh // 2)))
        layer = Image.alpha_composite(layer, shadow)

    layer.paste(prod, (x, y), prod)
    out = Image.alpha_composite(bg, layer).convert("RGB")
    dest = Path(out_path) if out_path else Path(str(background)).with_name(
        Path(str(background)).stem + "_prod.png")
    out.save(dest)
    logger.info("[product] composited the real asset at %sx%s", target_w, target_h)
    return str(dest)
