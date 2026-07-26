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


def _fetch(url: str) -> Optional[bytes]:
    """Bytes for a remote asset. A seam so tests never touch the network."""
    try:
        from reel.video_provider import _load_reference_image
        loaded = _load_reference_image(str(url))
        return loaded[0] if loaded else None
    except Exception:  # noqa: BLE001
        return None


def _load(asset: "bytes | str | Path") -> Optional["object"]:
    from PIL import Image
    try:
        if isinstance(asset, (bytes, bytearray)):
            if not asset:
                return None
            return Image.open(io.BytesIO(bytes(asset))).convert("RGBA")
        p = str(asset)
        if p.startswith(("http://", "https://")):
            data = _fetch(p)
            return Image.open(io.BytesIO(data)).convert("RGBA") if data else None
        return Image.open(p).convert("RGBA")
    except Exception:  # noqa: BLE001
        return None


def _feather(im, radius_frac: float = 0.06):
    """Soft-edge an unkeyable panel so it blends instead of reading as a pasted card.

    THE THIRD STATE (measured on v8): refusing to paste shipped a poster with NO product and an
    empty hand reaching for nothing — worse for the owner than anything the refusal protected
    her from. A feathered blend keeps the label pixel-exact and removes the 177-level edge
    cliff that made the panel read as tape."""
    from PIL import Image, ImageDraw, ImageFilter
    w, h = im.size
    r = max(4, int(min(w, h) * radius_frac))
    mask = Image.new("L", (w, h), 0)
    ImageDraw.Draw(mask).rounded_rectangle([r // 2, r // 2, w - r // 2, h - r // 2],
                                           radius=r, fill=255)
    mask = mask.filter(ImageFilter.GaussianBlur(radius=r))
    out = im.copy()
    out.putalpha(mask)
    return out


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


def composite_product(background: "str | Path", asset, *,
                      zone: tuple = DEFAULT_ZONE, contact_shadow: bool = True,
                      product_url: str = "",
                      out_path: "str | Path | None" = None) -> Optional[str]:
    """Paste the product onto `background` inside `zone`. `asset` is bytes, a URL/path, or a
    LIST of candidates — the first that keys into a clean cut-out wins; if none does, the first
    usable one is FEATHERED in. Returns the new path, or None only when nothing loads at all."""
    from PIL import Image, ImageFilter

    try:
        bg = Image.open(str(background)).convert("RGBA")
    except Exception:  # noqa: BLE001
        return None

    candidates = list(asset) if isinstance(asset, (list, tuple)) else [asset]
    if len(candidates) > 1 and all(isinstance(c, str) for c in candidates):
        candidates = rank_candidates(candidates, product_url=product_url)
    prod, feathered = None, False
    first_usable = None
    for cand in candidates:
        loaded = _load(cand)
        if loaded is None:
            continue
        first_usable = first_usable or loaded
        keyed = _key_studio_background(loaded)
        if keyed is not None:
            prod = _trim(keyed)
            break
    if prod is None:
        if first_usable is None:
            return None
        # No candidate keyed — blend the best one softly rather than ship no product at all.
        logger.info("[product] no candidate keyed cleanly — feathering the panel")
        prod, feathered = _feather(first_usable), True
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
    if contact_shadow and not feathered:
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


# Filename fragments that are page CHROME, never the product. Measured: the site logo
# (`Logo_colors.png`) is a clean transparent PNG, so it won the "first candidate that keys
# cleanly" race and was composited at 44% of the canvas — the giant blurry artifact the art
# critic condemned on three consecutive attempts.
_CHROME_HINTS = ("logo", "icon", "sprite", "favicon", "flag", "payment", "visa", "mastercard",
                 "badge", "banner", "placeholder", "avatar", "loader", "arrow", "cart")


def _slug_words(url: str) -> set:
    import re as _re
    tail = str(url or "").split("?", 1)[0].rstrip("/").rsplit("/", 1)[-1]
    return {w for w in _re.split(r"[-_.+]+", tail.lower()) if len(w) > 2}


def rank_candidates(urls, *, product_url: str = "") -> list:
    """Candidate images, best-first: the ones whose filename echoes the PRODUCT's own slug lead,
    obvious page chrome is dropped, and everything else keeps its original order.

    Ranking must happen BEFORE keying: a transparent logo keys beautifully and is still not the
    product."""
    out = [str(u) for u in (urls or []) if str(u or "").strip()]
    clean = [u for u in out if not any(h in u.split("?", 1)[0].lower() for h in _CHROME_HINTS)]
    pool = clean or out                      # never return nothing because everything looked like chrome
    want = _slug_words(product_url)
    if not want:
        return pool
    def _score(u):
        return -len(_slug_words(u) & want)
    return sorted(pool, key=_score)


def compose_or_background(background: "str | Path", asset, *, zone: tuple = DEFAULT_ZONE,
                          product_url: str = "",
                          out_path: "str | Path | None" = None) -> tuple:
    """(path, mode) — the NAMED fallback the reviewer asked for.

    MEASURED on the Al Fouad banner: ground p5/median/p95 = 124/216/250, the white bottle body
    = 91/202/247. Fourteen levels between the medians, ranges fully overlapped, and the keying
    tolerance has a CLIFF rather than a curve (tol 20 eats 4% of the bottle, tol 28 eats 38%).
    No threshold exists that strips the ground and keeps the product, so refusing to key is
    correct — and refusing alone shipped a poster with no product at all.

    So the refusal now has somewhere to go: mode "full_bleed" makes the studio shot the SCENE.
    Its ground, its reflection and its own props stop being an intruder pasted onto another
    image and become the composition — no seam, because there is no join. mode "composited" is
    the clean-cut-out path; mode "none" means nothing usable at all."""
    from PIL import Image

    candidates = list(asset) if isinstance(asset, (list, tuple)) else [asset]
    if len(candidates) > 1 and all(isinstance(c, str) for c in candidates):
        candidates = rank_candidates(candidates, product_url=product_url)

    pasted = composite_product(background, candidates, zone=zone, product_url=product_url)
    if pasted:
        # composite_product feathers as a last resort; a feathered PANEL is exactly the card the
        # reviewer measured, so treat "keyed cleanly" as the only composited state.
        for cand in candidates:
            loaded = _load(cand)
            if loaded is not None and _key_studio_background(loaded) is not None:
                return pasted, "composited"

    # FULL-BLEED IS OFF BY DEFAULT (owner, 2026-07-26, seeing it live: «أوحش من الأول بمراحل…
    # منتجات كتير… رجّع المنتج زي ما كنت بتعمله»). The store's asset is a multi-product MARKETING
    # BANNER, so making it the whole scene put a wall of other products on the poster and threw
    # away the generated scene entirely. The measured argument for it stands only for a
    # single-product studio shot, so it is opt-in, never the automatic fallback.
    import os as _os
    if _os.environ.get("POSTER_ALLOW_FULL_BLEED") != "1":
        return str(background), "none"

    for cand in candidates:
        loaded = _load(cand)
        if loaded is None:
            continue
        try:
            bg = Image.open(str(background)).convert("RGB")
            W, H = bg.size
            shot = loaded.convert("RGB")
            # cover-fit: fill the canvas, crop the overflow, keep the product centred
            scale = max(W / shot.width, H / shot.height)
            new = shot.resize((max(1, int(shot.width * scale)), max(1, int(shot.height * scale))),
                              Image.LANCZOS)
            left = max(0, (new.width - W) // 2)
            top = max(0, (new.height - H) // 2)
            out = new.crop((left, top, left + W, top + H))
            dest = Path(out_path) if out_path else Path(str(background)).with_name(
                Path(str(background)).stem + "_fullbleed.png")
            out.save(dest)
            logger.info("[product] asset used FULL-BLEED as the scene (no keyable cut-out)")
            return str(dest), "full_bleed"
        except Exception:  # noqa: BLE001
            continue
    return str(background), "none"
