"""AI influencer — greenfield feature, degrade-safe.

The frontend flow: create a portrait (persona knobs) -> validate it -> generate a poster featuring
the influencer + a product. All three are paid image operations with no existing pipeline, and they
403 under D-5, so the DEFAULT is a free Pillow placeholder (a persona/poster card) — the whole flow
works end to end, honestly watermarked "preview". Real generation is deferred behind
BASEERA_ENABLE_PAID_IMAGES (there is no in-repo influencer-image pipeline yet). Never touches reel.

Each function returns a PATCH to shallow-merge into the brand's `ai_influencer` block, which the
frontend reads: {has_portrait, portrait_url, has_poster, poster_url, validated, config{...}}.
"""
from __future__ import annotations

import io
import textwrap
from typing import Optional

from PIL import Image, ImageDraw

from . import webapp_images as images


def _persona_description(cfg: dict) -> str:
    """A human sentence from the persona knobs the frontend sends."""
    bits = []
    for key in ("age", "gender", "skin", "hair_length", "hair_color", "hair_style",
                "expression"):
        v = str(cfg.get(key) or "").strip()
        if v and v.lower() not in ("none", "any", ""):
            bits.append(v.replace("_", " "))
    if str(cfg.get("beard") or "").lower() in ("yes", "true", "1"):
        bits.append("with a beard")
    if str(cfg.get("glasses") or "").lower() in ("yes", "true", "1"):
        bits.append("wearing glasses")
    persona = str(cfg.get("persona") or "").strip()
    desc = ", ".join(bits)
    return f"{persona} — {desc}" if persona and desc else (persona or desc or "brand persona")


def _card(brand: dict, title: str, lines: list[str], *, avatar: bool) -> bytes:
    W, H = 1080, 1350
    colors = brand.get("brand_colors") or ["#22252b"]
    bg = images._hex_to_rgb(colors[0])
    accent = images._hex_to_rgb(colors[1]) if len(colors) > 1 else (210, 190, 160)
    ink = (255, 255, 255) if images._luminance(bg) < 0.55 else (20, 20, 20)

    img = Image.new("RGB", (W, H), bg)
    d = ImageDraw.Draw(img)
    d.text((int(W * 0.09), int(H * 0.06)), title, font=images._font(40), fill=ink)

    if avatar:
        cx, cy, r = W // 2, int(H * 0.34), 210
        d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=accent)
        # simple head+shoulders silhouette inside the circle
        hr = int(r * 0.42)
        d.ellipse([cx - hr, cy - hr - 30, cx + hr, cy + hr - 30], fill=bg)
        d.ellipse([cx - int(r * 0.75), cy + 30, cx + int(r * 0.75), cy + r + 120], fill=bg)

    y = int(H * 0.60)
    for raw in lines:
        for line in textwrap.wrap(raw, width=34)[:3]:
            d.text((int(W * 0.09), y), line, font=images._font(46), fill=ink)
            y += 58
        y += 14

    d.text((int(W * 0.09), H - 46),
           "AI influencer preview · enable image generation for the final render",
           font=images._font(22), fill=ink)
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def _ai(brand: dict) -> dict:
    ai = dict(brand.get("ai_influencer") or {})
    ai.setdefault("config", {})
    return ai


def generate_portrait(brand: dict, cfg: dict) -> dict:
    persona = _persona_description(cfg)
    png = _card(brand, "AI INFLUENCER", [persona], avatar=True)
    url = images.save_generated_png(png, f"{brand.get('domain_key','brand')}_influencer_portrait")
    ai = _ai(brand)
    ai.update({
        "has_portrait": True, "portrait_url": url,
        "validated": False,                          # a new portrait needs re-approval
        "has_poster": False, "poster_url": None,
        "config": {**ai.get("config", {}), "influencer_persona": persona,
                   **{k: cfg.get(k) for k in cfg if k != "domain_key"}},
    })
    return {"ai_influencer": ai}


def validate_portrait(brand: dict, approved: bool) -> dict:
    ai = _ai(brand)
    ai["validated"] = bool(approved)
    return {"ai_influencer": ai}


def generate_influencer_poster(brand: dict, *, product_hint: str = "",
                               scene_description: str = "") -> dict:
    ai = _ai(brand)
    persona = ai.get("config", {}).get("influencer_persona", "brand persona")
    lines = [f"Featuring: {persona}"]
    if product_hint:
        lines.append(f"Product: {product_hint}")
    if scene_description:
        lines.append(f"Scene: {scene_description}")
    png = _card(brand, (brand.get("organization_name") or "BRAND").upper(), lines, avatar=False)
    url = images.save_generated_png(png, f"{brand.get('domain_key','brand')}_influencer_poster")
    ai.update({"has_poster": True, "poster_url": url,
               "config": {**ai.get("config", {}), "product_hint": product_hint,
                          "scene_description": scene_description}})
    return {"ai_influencer": ai}
