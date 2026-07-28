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

# (x, y, height) as canvas fractions, x/y = TOP-LEFT. THE THIRD STATE, NOT A DEFAULT.
#
# This used to be `DEFAULT_ZONE` and it was the answer for every product of every shape in every
# layout. The owner's ruling (2026-07-28) ended that: «مينفعش يكون مكان ثابت — لازم نفهم المنتج
# وعلى أساسه نقرر مساحته وحجمه». The real zone is now DECIDED per product and per layout by
# `poster.product_placement.decide_placement`, before the scene is even generated.
#
# What survives here is the honest fallback for when nothing was measured — and the compositor
# LOGS when it lands here, because "nobody decided" must never look like "this was chosen".
#
# MEASURED (Nano Care, 2026-07-27): at (0.52, 0.34, 0.44) the product was 44% of the canvas — the
# size of the park bench beside it. The height ceiling now lives in `product_placement`.
UNMEASURED_ZONE = (0.54, 0.50, 0.34)


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
        # An ALREADY-OPEN image passes straight through. Without this a caller that had loaded
        # the asset once and passed the object on got `str(<PIL.Image>)` treated as a file path,
        # which fails and returns None — measured live as "[label] not reprojected: the artwork
        # could not be loaded" on an artwork that was sitting in memory.
        if isinstance(asset, Image.Image):
            return asset.convert("RGBA")
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


# --------------------------------------------------------------------------------------------
# LIGHT AND MATTER. Everything below exists because of one owner note (2026-07-28) naming the
# four reasons a paste reads as a paste: no shadow, mismatched light and colour, mismatched
# perspective, and no interaction with the scene. Perspective is settled upstream — the scene is
# now photographed from the angle the packshot was. These three close the rest.
# --------------------------------------------------------------------------------------------

def _clock_vector(clock: int) -> tuple:
    """Unit vector pointing FROM the frame's centre TOWARD a clock position, in image
    coordinates (y grows downward). 12 o'clock is straight up the frame."""
    import math
    theta = math.radians((int(clock) % 12) * 30.0)
    return (math.sin(theta), -math.cos(theta))


def _estimate_light(bg, x: int, y: int, w: int, h: int) -> int:
    """The clock position the SCENE's key light comes from, read off the luminance gradient
    around the destination patch. 0 when the neighbourhood is too flat to claim a direction —
    the third state again, so the caller can fall back to a plain contact shadow instead of
    inventing a confident cast in the wrong direction."""
    try:
        from PIL import ImageStat
        pad = max(8, int(max(w, h) * 0.6))
        W, H = bg.size
        x0, y0 = max(0, x - pad), max(0, y - pad)
        x1, y1 = min(W, x + w + pad), min(H, y + h + pad)
        if x1 - x0 < 8 or y1 - y0 < 8:
            return 0
        gray = bg.convert("L").crop((x0, y0, x1, y1))
        gw, gh = gray.size
        left = ImageStat.Stat(gray.crop((0, 0, gw // 2, gh))).mean[0]
        right = ImageStat.Stat(gray.crop((gw // 2, 0, gw, gh))).mean[0]
        top = ImageStat.Stat(gray.crop((0, 0, gw, gh // 2))).mean[0]
        bottom = ImageStat.Stat(gray.crop((0, gh // 2, gw, gh))).mean[0]
        dx, dy = right - left, top - bottom          # +dx brighter right, +dy brighter top
        if (dx * dx + dy * dy) ** 0.5 < 4.0:         # < ~1.5% of the range: no readable direction
            return 0
        import math
        # atan2 in image space, then to a clock face where 12 is up and it runs clockwise.
        clock = int(round((math.degrees(math.atan2(dx, dy)) % 360.0) / 30.0)) % 12
        return clock or 12
    except Exception:  # noqa: BLE001
        return 0


def _cast_shadow(layer, prod, x: int, y: int, clock: int, *, overhead: bool):
    """Add the shadow the light direction implies, on top of whatever `layer` already holds.

    A contact shadow says the object touches something. A CAST shadow says where the sun is —
    and it is the one a viewer reads without noticing. Standing objects throw a squashed, sheared
    copy of themselves across the ground away from the light; an object lying on a surface under
    an overhead camera throws a short offset drop instead."""
    from PIL import Image, ImageFilter
    if not clock:
        return layer
    lx, ly = _clock_vector(clock)
    w, h = prod.size
    alpha = prod.getchannel("A")
    silhouette = Image.new("RGBA", prod.size, (0, 0, 0, 0))
    silhouette.putalpha(alpha)

    if overhead:
        # Lying flat, seen from above: the shadow is the same shape, nudged away from the light.
        off = (int(x - lx * w * 0.06), int(y - ly * h * 0.06))
        shadow = Image.new("RGBA", layer.size, (0, 0, 0, 0))
        tint = Image.new("RGBA", prod.size, (0, 0, 0, 105))
        tint.putalpha(alpha.point(lambda a: int(a * 0.42)))
        shadow.paste(tint, off, tint)
        blur = max(4, int(min(w, h) * 0.05))
    else:
        # Standing: flatten the silhouette onto the ground plane and shear it away from the light.
        squashed_h = max(4, int(h * 0.30))
        squashed = silhouette.resize((w, squashed_h), Image.LANCZOS)
        shear = -lx * w * 0.55
        sheared = squashed.transform(
            (w + int(abs(shear)) + 2, squashed_h), Image.AFFINE,
            (1, shear / max(squashed_h, 1), max(0.0, -shear), 0, 1, 0),
            resample=Image.BICUBIC)
        tint = Image.new("RGBA", sheared.size, (0, 0, 0, 0))
        tint.paste((0, 0, 0, 130), (0, 0), sheared.getchannel("A"))
        shadow = Image.new("RGBA", layer.size, (0, 0, 0, 0))
        shadow.paste(tint, (int(x - max(0.0, shear)), y + h - squashed_h // 2), tint)
        blur = max(6, int(squashed_h * 0.6))

    shadow = shadow.filter(ImageFilter.GaussianBlur(radius=blur))
    return Image.alpha_composite(layer, shadow)


# HOW FAR THE PRODUCT MAY BE MOVED TOWARD THE SCENE. Deliberately small: a full relight is not
# something deterministic arithmetic can do, and a brand's registered colour is exactly the thing
# a naive colour transfer destroys — Nano Care's red would go pink. The goal is "belongs in this
# light", not "was photographed in this room".
_MAX_LUMA_SHIFT = 0.12
_MAX_CHANNEL_SHIFT = 0.06
_PULL = 0.45              # how much of the measured difference is taken, before the caps


def _harmonize(prod, bg, x: int, y: int, w: int, h: int) -> tuple:
    """(product, note) — the product nudged toward the destination's brightness and colour cast.

    Returns the ORIGINAL unchanged, with a note, whenever the required correction exceeds the
    caps: a label that has drifted out of brand colour is a worse defect than one that is
    slightly too bright."""
    try:
        from PIL import Image, ImageStat
        W, H = bg.size
        x0, y0 = max(0, x), max(0, y)
        x1, y1 = min(W, x + w), min(H, y + h)
        if x1 - x0 < 4 or y1 - y0 < 4:
            return prod, "destination patch too small to measure"
        dst = ImageStat.Stat(bg.convert("RGB").crop((x0, y0, x1, y1))).mean

        alpha = prod.getchannel("A")
        opaque = alpha.point(lambda a: 255 if a > 200 else 0)
        if not opaque.getbbox():
            return prod, "no opaque pixels to measure"
        src = ImageStat.Stat(prod.convert("RGB"), mask=opaque).mean
        if min(src) < 1.0:
            return prod, "product too dark to measure"

        src_l = (src[0] + src[1] + src[2]) / 3.0
        dst_l = (dst[0] + dst[1] + dst[2]) / 3.0
        luma_gain = 1.0 + _PULL * (dst_l / max(src_l, 1.0) - 1.0)
        if abs(luma_gain - 1.0) > _MAX_LUMA_SHIFT:
            return prod, (f"brightness correction {abs(luma_gain - 1.0):.0%} exceeds the "
                          f"{_MAX_LUMA_SHIFT:.0%} cap — left true to the packshot")

        cast = []
        for i in range(3):
            # the channel's cast AFTER the brightness match — this is colour temperature, not
            # exposure, so it is capped far tighter.
            want = (dst[i] / max(dst_l, 1.0)) / (src[i] / max(src_l, 1.0))
            c = 1.0 + _PULL * (want - 1.0)
            if abs(c - 1.0) > _MAX_CHANNEL_SHIFT:
                return prod, (f"colour correction {abs(c - 1.0):.0%} exceeds the "
                              f"{_MAX_CHANNEL_SHIFT:.0%} cap — brand colour kept exact")
            cast.append(c)

        gains = [c * luma_gain for c in cast]
        r, g, b, a = prod.split()
        lut = [[min(255, int(v * gains[i] + 0.5)) for v in range(256)] for i in range(3)]
        out = Image.merge("RGBA", (r.point(lut[0]), g.point(lut[1]), b.point(lut[2]), a))
        return out, (f"matched to the scene (brightness {luma_gain - 1.0:+.0%}, "
                     f"colour cast {max(abs(c - 1.0) for c in cast):.0%})")
    except Exception as exc:  # noqa: BLE001
        return prod, f"harmonisation skipped ({type(exc).__name__})"


def composite_product(background: "str | Path", asset, *,
                      zone: "tuple | None" = None, contact_shadow: bool = True,
                      product_url: str = "", mode: str = "", harmonize: bool = True,
                      out_path: "str | Path | None" = None) -> Optional[str]:
    """Paste the product onto `background` inside `zone`. `asset` is bytes, a URL/path, or a
    LIST of candidates — the first that keys into a clean cut-out wins; if none does, the first
    usable one is FEATHERED in. Returns the new path, or None only when nothing loads at all.

    `zone` comes from `product_placement.decide_placement`; None means nobody decided, which is
    logged rather than silently treated as a choice. `mode` is that decision's mode — it changes
    the shadow's physics, because an object lying under an overhead camera and an object standing
    on a floor do not throw the same shape."""
    from PIL import Image, ImageFilter

    if zone is None:
        zone = UNMEASURED_ZONE
        logger.info("[product] no placement decision — falling back to the unmeasured zone %s",
                    UNMEASURED_ZONE)

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
    zx, zy, zh = (list(zone) + list(UNMEASURED_ZONE))[:3]
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

    # MATCH THE LIGHT BEFORE CASTING IT. Both readings come from the destination patch, so they
    # have to happen after the zone is resolved and before anything is drawn.
    overhead = str(mode) == "flat_lay_overhead"
    clock = _estimate_light(bg, x, y, target_w, target_h)
    if harmonize and not feathered:
        prod, note = _harmonize(prod, bg, x, y, target_w, target_h)
        logger.info("[product] %s", note)

    layer = Image.new("RGBA", bg.size, (0, 0, 0, 0))
    if contact_shadow and not feathered:
        # A CAST shadow says where the light is; a CONTACT shadow says the object touches
        # something. A photograph has both, and the paste has read as a paste without either.
        layer = _cast_shadow(layer, prod, x, y, clock, overhead=overhead)
        # A contact shadow under a standing object only. Lying flat under an overhead camera
        # there is no "underneath" to darken — the cast drop above already does that job, and an
        # ellipse there is the mid-air shadow this module has warned about since 2026-07-27.
        if not overhead:
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
    logger.info("[product] composited the real asset at %sx%s (%s, light %s o'clock)",
                target_w, target_h, mode or "unspecified mode", clock or "unreadable")
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


def rank_candidates(urls, *, product_url: str = "", caller=None, probe: int = 4) -> list:
    """Candidate images, best-first: the ones whose filename echoes the PRODUCT's own slug lead,
    obvious page chrome is dropped, and everything else keeps its original order.

    Ranking must happen BEFORE keying: a transparent logo keys beautifully and is still not the
    product.

    ONE PRODUCT MEANS ONE UNIT (measured, Nano Care 2026-07-28). The chosen packshot showed TWO
    cartons leaning together, and the art critic's stated reason for the final rejection was
    "2 products in an ad for one named product". Filenames cannot see that, so with a vision
    `caller` the top `probe` candidates are measured and single-unit shots are promoted. Without
    a caller the behaviour is exactly as before — no silent change, no silent cost."""
    out = [str(u) for u in (urls or []) if str(u or "").strip()]
    clean = [u for u in out if not any(h in u.split("?", 1)[0].lower() for h in _CHROME_HINTS)]
    pool = clean or out                      # never return nothing because everything looked like chrome
    want = _slug_words(product_url)
    if want:
        pool = sorted(pool, key=lambda u: -len(_slug_words(u) & want))
    if caller is None or len(pool) < 2:
        return pool

    from poster.product_geometry import measure_product
    head, tail = pool[:max(1, int(probe))], pool[max(1, int(probe)):]
    graded = []
    for i, u in enumerate(head):
        geo = measure_product(u, caller=caller)
        # unmeasured (0) sorts with the singles rather than being punished for a failed probe
        units = geo.unit_count if geo.checked and geo.unit_count else 1
        graded.append((0 if units <= 1 else 1, i, u))
    if any(rank for rank, _i, _u in graded):
        logger.info("[product] %s of %s probed candidates show more than one unit",
                    sum(1 for r, _i, _u in graded if r), len(graded))
    return [u for _r, _i, u in sorted(graded)] + tail


def compose_or_background(background: "str | Path", asset, *, zone: "tuple | None" = None,
                          product_url: str = "", mode: str = "", caller=None,
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
        candidates = rank_candidates(candidates, product_url=product_url, caller=caller)

    pasted = composite_product(background, candidates, zone=zone, product_url=product_url,
                               mode=mode)
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
