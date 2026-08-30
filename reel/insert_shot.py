# -*- coding: utf-8 -*-
"""The product insert — the one shot an agency never asks a video model to make.

WHY THIS EXISTS. Every attempt to get Veo to hold a legible pack has failed, and the failures were
measured, not felt: the label is redrawn every frame, so it warps; the scene QA gate then kills the
clip; three renders at $1.60 each burned on one scene and the per-run budget died before the last
scenes got any motion at all. `reel/creative.py::_product_asset` documents that conclusion in full
and stops PASTING a pack into moving footage for the same reason — a static cut-out riding on a
moving plate reads, in the gate's own words, as "a STATIC IMAGE CRUDELY OVERLAID".

An agency does not solve this. It CUTS. The pack shot is its own beat: a second and a half, the
product alone, lit and held — no actor, no camera pass through a room, nothing for a generator to
get wrong. That beat is what this module renders, and it renders it OURSELVES:

  * the label is pixel-identical, because it IS the brand's own photograph;
  * nothing is generated, so nothing can be rejected — no Veo call, no QA retry, no spend;
  * it lands in the same world, because the background is the previous clip's own last frame,
    defocused the way a long lens defocuses a room behind a close subject.

THIS IS NOT THE KEN BURNS FALLBACK THE OWNER REJECTED («انا مش عايزه Ken Burns ف الريل»). That is a
rescue: a scene that was supposed to be alive fails, and a photograph is panned across in its place
for four seconds while the film pretends nothing happened. This is a DESIGNED beat, written into
the plan, one and a half seconds long, of a subject that is meant to be still — and if it cannot be
built (no real packshot, nothing keys) it returns None and the scene stays a generated one. It
never substitutes for a shot that failed.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional, Sequence

# The canvas the insert is DESIGNED for. Deliberately the canonical reel height and not the
# caller's `height`: a preview rendered at 0.25 scale is still a preview of the real film, and a
# source too small for the real film is too small, whatever we happen to be rendering right now.
from .schemas import REEL_H

logger = logging.getLogger(__name__)

# A pack shot is read, not watched. Under a second it flashes; past two it stalls a 15-second film.
INSERT_S = 1.4

# The push is what makes it a SHOT and not a freeze — small enough that the label never softens,
# large enough that the frame is visibly alive at every moment.
# LOCKED OFF (owner, 2026-08-02: «الريل فيه إنه يزوم»). The push was 7% over the beat —
# a slow zoom on a STILL photograph, which is the one move that announces the shot is not
# footage. `_intra`/`_shake` are already disabled for an insert for the same reason, and
# the comment there says it: every commercial shoots its pack shot on a locked head.
_PUSH = 0.0

# The background is a room behind a close subject, so it is defocused, not merely blurred: the
# radius is a fraction of the frame width, which keeps the look identical at preview scale.
_DEFOCUS_FRAC = 0.016

# The product's share of the frame height. An insert is CLOSE — this is the shot where the viewer
# is meant to read the name — so it is far past the poster's MAX_HEIGHT_FRAC (0.34), which is
# sized to leave a poster's copy somewhere to live.
_HEIGHT_FRAC = 0.46
_TOP_FRAC = 0.29                    # slightly above centre; the shadow needs the room below

# A light travelling over the pack is how a product is actually lit for camera, and it is the
# cheapest honest motion there is — the frame changes even where nothing moves.
_SWEEP_PEAK = 0.13
_SWEEP_SIGMA = 0.30


def _ease(t: float) -> float:
    """Ease-in-out. A linear push starts and stops with a visible jerk at the cut."""
    t = max(0.0, min(1.0, float(t)))
    return t * t * (3.0 - 2.0 * t)


# The hero-product resolver lives with the fetcher it depends on (`api.webapp_images`), so
# the POSTER lane can ask the same question the REEL lane asks and get the same answer.
# One pasted link, one resolution — the two lanes disagreeing about what the ad is about
# is the defect this whole thread exists to close. Re-exported here because this is where
# the reel has always imported it from.
from api.webapp_images import _HERO_CACHE, hero_product_facts  # noqa: F401


def product_candidates(profile: Optional[dict], limit: int = 4,
                       hero_url: str = "") -> list[str]:
    """The brand's own images, best-first, for the insert to cut to.

    THE CATALOGUE LEADS, and that ordering is the whole difference between a product shot and a
    photo of the shop. `profile["catalog"]` is built deterministically by `scraper.catalog` from
    the brand's own PRODUCT PAGES, so every `image_url` in it is a picture OF ONE ITEM FOR SALE —
    usually the packshot the store photographed on white, which is exactly what keys cleanly.
    `visual.content_images` is everything else the crawl kept: interiors, staff, banners, a hero
    of the storefront. It follows, so it is only reached when there is no catalogue at all.

    Then the poster's `rank_candidates`, which drops page chrome by filename — the measured reason
    a site logo once won the "first thing that keys cleanly" race and was composited at 44% of a
    poster. No vision caller is passed: this runs inside a render, and a probe here would spend on
    every reel to reorder a list the filenames already order.
    """
    if not isinstance(profile, dict):
        return []
    urls: list[str] = []
    # THE ONE THE OWNER NAMED GOES FIRST (Axis 1's product selector, owner 2026-08-03: she pastes
    # a product link and the film is about THAT item). Matched against the catalogue by canonical
    # page URL so the hero is the packshot the store photographed, not a guess from the same page.
    # An unmatched link is not an error and not silently ignored either — the catalogue order
    # stands and the caller can see the hero did not resolve.
    # THE NAMED PRODUCT, kept as its own list so the ranker below cannot mix it back in with the
    # candidates it is supposed to lead.
    hero: list[str] = []
    if hero_url:
        for u in (hero_product_facts(profile, hero_url).get("images") or []):
            u = str(u or "").strip()
            if u and u not in urls:
                urls.append(u)
                hero.append(u)
    for item in (profile.get("catalog") or []):
        u = str((item.get("image_url") if isinstance(item, dict) else "") or "").strip()
        if u and u not in urls:
            urls.append(u)
    visual = profile.get("visual") or {}
    if isinstance(visual, dict):
        for u in (visual.get("content_images") or []):
            u = str(u or "").strip()
            if u and u not in urls:
                urls.append(u)
    if not urls:
        return []
    try:
        from poster.product_composite import rank_candidates
        # THE HERO IS NOT A CANDIDATE — IT IS THE ANSWER. MEASURED on the shipped Vichy reel: the
        # link resolved, the packshot was placed first, and then this ranker re-sorted the WHOLE
        # list. `rank_candidates` scores by FILENAME, and the storefront named the Vichy shot
        # `1_10.webp` while the catalogue entries carry descriptive names, so the one image the
        # owner asked for scored last. The film's own critic caught the result and the reel shipped
        # anyway: «the film builds a story for one brand's product (Vichy) then inexplicably cuts to
        # a completely different, unrelated product (ACM)».
        #
        # Ranking is for CHOOSING when nobody has chosen. Once the caller names the product, the
        # only job left is ordering the fallbacks behind it.
        if hero:
            ranked = hero + [u for u in rank_candidates([u for u in urls if u not in hero])
                             if u not in hero]
        else:
            ranked = rank_candidates(urls)
    except Exception:  # noqa: BLE001 — a ranking failure must not cost the insert
        ranked = urls
    out = [str(u) for u in ranked][:max(1, int(limit))]
    if hero and (not out or out[0] != hero[0]):
        # Belt and braces: whatever happened above, a named product leads or the caller is told.
        logger.info("[insert] the named product was displaced by ranking — restoring it to the lead")
        out = [str(hero[0])] + [u for u in out if u != hero[0]]
    return out[:max(1, int(limit))]


# A PRODUCT IS SHAPED LIKE A PRODUCT, and a web banner is not — which is the only difference the
# keying can't see and the filenames can't either.
#
# MEASURED (Al Fouad, 2026-08-01). Three of its four best candidates keyed perfectly and composited
# into a beautiful insert of... a homepage strip: "Solutions For Every Concern", a "Shop By Concern"
# button, six products in a row. A banner sits on flat white exactly like a packshot does, so every
# test upstream of this one passes it. What gives it away is the SHAPE: it is five times wider than
# it is tall. A bottle is about 0.4, a carton 0.8, a jar 1.0, a tube on its side 1.5.
#
# This is deliberately a proportion and not a text detector: it needs no OCR, no model, no language,
# and it says the same thing about a banner in Arabic, Thai or Portuguese.
_MIN_ASPECT, _MAX_ASPECT = 0.30, 1.75

# AND A PRODUCT IS PHOTOGRAPHED, WHICH A 120-PIXEL ICON IS NOT.
#
# MEASURED 2026-08-06 on NTI (a government training institute — a brand with no packshot at all,
# which is exactly when this goes wrong). Its best-ranked candidate was `linkchain3.png`, a
# **120x120 decorative chain icon**: it keys perfectly off its flat background and its aspect is
# 1.00, so every test above passes it, and it would have been composited as the HERO PRODUCT SHOT
# in the beat immediately before the logo.
#
# This is the same failure `poster/product_composite.rank_candidates` was written for — "a site
# logo once won the 'first thing that keys cleanly' race and was composited at 44% of a poster" —
# wearing a filename the keyword filter does not know.
#
# The missing signal is SIZE, and it separates the two classes with an enormous margin. Trimmed
# heights, measured:
#     real packshots (Al Fouad, Nano Care)   664 - 1615 px
#     NTI decorations                        108 -  219 px
#
# So the rule is derived from what the insert actually DRAWS rather than picked: the pack is
# rendered at `_HEIGHT_FRAC` of a 1920-tall frame (883 px), and a source that has to be blown up
# more than 2x to get there is not a photograph — it is an icon, and it would be a blurry one.
# Nothing here is keyed to a vertical: a brand with real product photography sails through, and a
# brand with none correctly gets no insert (the beat then generates, which `insert_clip` already
# handles by returning None).
_MAX_UPSCALE = 2.0
_MIN_PRODUCT_PX = int(REEL_H * _HEIGHT_FRAC / _MAX_UPSCALE)      # 1920 * 0.46 / 2 = 441


def _keyed_products(candidates: Sequence, limit: int = 3) -> list[bytes]:
    """The candidates that both CUT OUT cleanly and are shaped like one object, as raw bytes.

    Returning bytes rather than URLs means the compositor does not re-fetch what was just fetched
    to measure — and it keeps the decision here, where the reason can be logged, instead of inside
    a generic paste that has no idea what an insert is.
    """
    import io

    out: list[bytes] = []
    try:
        from poster.product_composite import _key_studio_background, _load, _trim
    except Exception:  # noqa: BLE001
        return out
    for cand in candidates:
        if len(out) >= max(1, int(limit)):
            break
        name = str(cand).rsplit("/", 1)[-1][:48] if isinstance(cand, str) else "<bytes>"
        try:
            im = _load(cand)
            if im is None:
                continue
            keyed = _key_studio_background(im)
            if keyed is None:
                logger.info("[insert] %s does not cut out cleanly — skipped", name)
                continue
            t = _trim(keyed)
            if t.width < 8 or t.height < 8:
                continue
            aspect = t.width / t.height
            if not (_MIN_ASPECT <= aspect <= _MAX_ASPECT):
                logger.info("[insert] %s is %.1f:1 — a banner shape, not a product; skipped",
                            name, aspect)
                continue
            if t.height < _MIN_PRODUCT_PX:
                logger.info("[insert] %s is only %dpx tall — an icon or a decoration, not a "
                            "photographed product (needs >=%dpx to fill the shot); skipped",
                            name, t.height, _MIN_PRODUCT_PX)
                continue
            buf = io.BytesIO()
            im.save(buf, "PNG")
            out.append(buf.getvalue())
        except Exception:  # noqa: BLE001 — one bad candidate never costs the rest
            continue
    return out


# The product beat's ground: the previous shot, calmed until it reads as a studio wall.
# Saturation first (colour is what makes a defocused room still look like a room), then a lift
# toward a warm off-white — the reference spot's plinth is off-white, not pure white, because pure
# white kills the contact shadow the composite needs to sit down.
_PLINTH_SAT = 0.35
_PLINTH_LIFT = 0.55
_PLINTH_TINT = (247, 245, 242)


def build_insert_frame(background: "str | Path", product, *, out_path: "str | Path",
                       width: int, height: int, product_url: str = "") -> Optional[str]:
    """The insert's single frame: the previous shot's world, defocused, with the real pack on it.

    Returns None when nothing usable can be built — never a defocused room with no product in it,
    which would be a beat that says nothing while looking deliberate.
    """
    from PIL import Image, ImageFilter

    dest = Path(str(out_path))
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        bg = Image.open(str(background)).convert("RGB")
    except Exception:  # noqa: BLE001
        logger.info("[insert] background %s is unreadable — no insert", background)
        return None

    # Cover-fit the tail frame to the reel canvas. The tail is already 9:16 in a normal render;
    # this makes a preview-scaled or oddly-sized frame behave the same way.
    scale = max(width / bg.width, height / bg.height)
    bg = bg.resize((max(1, int(bg.width * scale)), max(1, int(bg.height * scale))), Image.LANCZOS)
    left, top = (bg.width - width) // 2, (bg.height - height) // 2
    bg = bg.crop((left, top, left + width, top + height))
    bg = bg.filter(ImageFilter.GaussianBlur(radius=max(2.0, width * _DEFOCUS_FRAC)))
    # AND THEN IT IS LIFTED AND CALMED, so the pack has somewhere to stand.
    #
    # MEASURED against the reference reel the owner supplied (NanoCare Liposomal Iron, 2026-08-03):
    # its product beat is a lit plinth in a bright, almost empty room — the pack is the only thing
    # with edges in the frame. Ours was the previous shot's world, defocused: still the same
    # kitchen, the same person-shaped blob, the same mid-tones, so the packshot read as a sticker
    # on a photograph rather than as a shot of its own.
    #
    # The tail is KEPT rather than replaced — it is the reason the beat belongs to this film and
    # not to a stock library — but it is desaturated and lifted toward white until it behaves like
    # a studio wall. Deliberately not a flat plate: a solid ground is where a composite looks
    # composited.
    from PIL import ImageEnhance
    bg = ImageEnhance.Color(bg).enhance(_PLINTH_SAT)
    bg = Image.blend(bg, Image.new("RGB", bg.size, _PLINTH_TINT), _PLINTH_LIFT)

    usable = _keyed_products(list(product) if isinstance(product, (list, tuple)) else [product])
    if not usable:
        logger.info("[insert] no candidate is both cleanly cut out and product-shaped — no insert")
        return None

    plate = dest.with_name(dest.stem + "_plate.png")
    bg.save(plate)

    try:
        from poster.product_composite import compose_or_background
        path, mode = compose_or_background(
            plate, usable, zone=(0.0, _TOP_FRAC, _HEIGHT_FRAC),
            product_url=str(product_url or ""), align="center", out_path=dest)
    except Exception as exc:  # noqa: BLE001
        logger.info("[insert] compositing failed (%s: %s) — no insert", type(exc).__name__, exc)
        return None
    finally:
        try:
            plate.unlink()
        except OSError:
            pass

    if mode == "none":
        # FAIL-CLOSED, and this is the whole point of using the NAMED fallback. `compose_or_background`
        # returns the untouched background under mode "none"; shipping that would put a defocused
        # empty room in the middle of the film and call it a product shot.
        logger.info("[insert] no candidate produced a product — no insert")
        return None
    logger.info("[insert] frame built (%s)", mode)
    return str(path)


def _sweep(im, t: float):
    """A soft highlight travelling across the frame — the light, not the camera, moving."""
    from PIL import Image, ImageChops

    if _SWEEP_PEAK <= 0:
        return im
    w, h = im.size
    # Built small and scaled up: a 48-wide gradient resized to 1080 is smooth and ~500x cheaper
    # than evaluating the falloff per pixel.
    sw, sh = 48, max(1, int(48 * h / max(1, w)))
    centre = -0.35 + 1.7 * max(0.0, min(1.0, float(t)))
    mask = Image.new("L", (sw, sh))
    px = mask.load()
    for y in range(sh):
        for x in range(sw):
            d = (x / max(1, sw - 1)) * 0.75 + (y / max(1, sh - 1)) * 0.25
            e = (d - centre) / _SWEEP_SIGMA
            px[x, y] = int(255 * _SWEEP_PEAK * pow(2.718281828, -e * e))
    band = mask.resize((w, h), Image.BILINEAR)
    return ImageChops.add(im, Image.merge("RGB", (band, band, band)))


def render_insert(frame: "str | Path", out_path: "str | Path", *, duration_s: float,
                  width: int, height: int, fps: int = 30, work_dir: "str | Path | None" = None,
                  sweep: bool = True) -> Optional[str]:
    """Encode the insert: a slow centred push-in on `frame`, with the light passing over it."""
    import tempfile

    from PIL import Image

    from .ffmpeg_tools import run_ffmpeg

    dest = Path(str(out_path))
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        base = Image.open(str(frame)).convert("RGB")
    except Exception:  # noqa: BLE001
        return None

    n = max(2, int(round(max(0.2, float(duration_s)) * int(fps))))
    tmp = Path(work_dir) if work_dir else Path(tempfile.mkdtemp(prefix="insert_"))
    tmp.mkdir(parents=True, exist_ok=True)
    try:
        for k in range(n):
            t = k / (n - 1)
            s = 1.0 + _PUSH * _ease(t)
            w2, h2 = max(width, int(round(width * s))), max(height, int(round(height * s)))
            im = base.resize((w2, h2), Image.LANCZOS)
            lx, ly = (w2 - width) // 2, (h2 - height) // 2
            im = im.crop((lx, ly, lx + width, ly + height))
            if sweep:
                im = _sweep(im, t)
            # JPEG at 96 for the intermediate: the x264 pass below is lossier than this is, and
            # PNG-encoding 40+ full-size frames is the slowest thing in the whole render.
            im.save(tmp / f"f{k:05d}.jpg", quality=96, subsampling=0)
        run_ffmpeg([
            "-framerate", str(int(fps)), "-i", str(tmp / "f%05d.jpg"),
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-profile:v", "high",
            "-preset", "medium", "-crf", "18", str(dest),
        ])
    except Exception as exc:  # noqa: BLE001
        logger.info("[insert] encode failed (%s: %s)", type(exc).__name__, exc)
        return None
    finally:
        for f in tmp.glob("f*.jpg"):
            try:
                f.unlink()
            except OSError:
                pass
    return str(dest) if dest.is_file() else None


def insert_clip(background: "str | Path", product, *, out_path: "str | Path",
                duration_s: float, width: int, height: int, fps: int = 30,
                product_url: str = "", work_dir: "str | Path | None" = None) -> Optional[str]:
    """The whole beat: frame + encode. None means "there is no insert to cut to" — the caller
    keeps its generated scene rather than shipping a hole."""
    import tempfile

    tmp = Path(work_dir) if work_dir else Path(tempfile.mkdtemp(prefix="insert_"))
    tmp.mkdir(parents=True, exist_ok=True)
    frame = build_insert_frame(background, product, out_path=tmp / "insert_frame.png",
                               width=width, height=height, product_url=product_url)
    if not frame:
        return None
    return render_insert(frame, out_path, duration_s=duration_s, width=width, height=height,
                         fps=fps, work_dir=tmp)
