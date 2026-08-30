# -*- coding: utf-8 -*-
"""A "style reference" that is a finished ad is not a style reference.

THE ROOT under every poster symptom this session (2026-07-26). `_brand_style_refs` pulls
indiscriminately from `brand_dna.references_seen` and `profile.visual.content_images`, and those
lists are the brand's HOMEPAGE BANNERS. Two, downloaded and inspected:

    special_offer.jpg   a 3-D lettering graphic reading "Special Offer" — pure text
    Desktop_14.webp     7210x1685: "Pharmacy You Can Trust · Licensed Pharmacist · Verified
                        Products · 15+ Years of Proven Experience · 17 Branches Across Egypt",
                        an "Explore More" button, the logo, and a pharmacist

They are attached to the image model as visual conditioning on every classic-path render
(pipeline.py:312 -> :352). The model imitates them faithfully, which is where the garbled
English ("FIC. TRUSTWORSHY"), the fake wordmark ("FoMad") and the other brands' products
(Centrum, Limitless) came from. The only counter-measure was PROSE in `_product_scene_guard`,
and prose loses to pixels — the same lesson the product and the face taught earlier today.

OWNER'S RULING: in a PRODUCT ad, drop the references entirely. For a brand-wide ad they are
screened, because the same banners produce the same garbled text there.
"""
from __future__ import annotations

import io

from PIL import Image, ImageDraw


def _img(w: int, h: int, *, text: str = "", boxes: int = 0) -> bytes:
    """A synthetic candidate. `text` draws REAL glyphs (the measure counts ink/paper crossings
    per row, so hand-drawn bars are not a fair stand-in for type)."""
    im = Image.new("RGB", (w, h), (238, 238, 240))
    d = ImageDraw.Draw(im)
    if text:
        from PIL import ImageFont
        try:
            font = ImageFont.truetype("arial.ttf", max(14, h // 14))
        except OSError:
            font = ImageFont.load_default()
        line = "Pharmacy You Can Trust Licensed Verified Products 15 Years"
        for row in range(max(3, h // 40)):
            d.text((12, 10 + row * max(16, h // 12)), line, fill=(12, 12, 18), font=font)
    for i in range(boxes):
        x = 20 + i * (w // max(1, boxes))
        d.rectangle([x, h // 3, x + w // 6, 2 * h // 3], fill=(40, 90, 190))
    buf = io.BytesIO()
    im.save(buf, format="PNG")
    return buf.getvalue()


# --- the screen itself -----------------------------------------------------------------------

def test_a_text_heavy_graphic_is_rejected_on_its_text():
    """Deliberately NOT banner-shaped, so the text measure is what has to catch it — the real
    `special_offer.jpg` is square (1:1) and scored 0.33 on this measure."""
    from poster.reference_screen import screen_reference
    v = screen_reference(_img(900, 1100, text="x"))
    assert v.usable is False
    assert "text" in v.reason.lower(), v.reason


def test_a_wide_website_banner_is_rejected_on_shape_alone():
    from poster.reference_screen import screen_reference
    v = screen_reference(_img(2400, 500))
    assert v.usable is False and "banner" in v.reason.lower(), v.reason


def test_a_clean_photographic_reference_passes():
    from poster.reference_screen import screen_reference
    im = Image.new("RGB", (900, 1200))
    px = im.load()
    for y in range(1200):                      # a smooth gradient scene, no lettering
        for x in range(900):
            px[x, y] = (120 + y // 20, 130 + y // 25, 150)
    buf = io.BytesIO(); im.save(buf, format="PNG")
    v = screen_reference(buf.getvalue())
    assert v.usable is True, v.reason


def test_an_unreadable_asset_is_not_usable_and_says_why():
    from poster.reference_screen import screen_reference
    v = screen_reference(b"not an image")
    assert v.usable is False and v.reason


# --- how the pipeline uses it ----------------------------------------------------------------

def test_a_named_product_drops_every_visual_reference(monkeypatch, tmp_path):
    """OWNER: «شيلها خالص في إعلان المنتج». The scene is built from the DNA's WORDS plus the
    composited product — language cannot leak somebody else's bottle into the frame."""
    import poster.oneshot as oneshot
    import poster.pipeline as pp

    seen = {}

    def fake_gen(prompt, out_path, **kw):
        seen["refs"] = kw.get("product_images")
        Image.new("RGB", (64, 96), (200, 200, 200)).save(out_path)
        return out_path

    monkeypatch.setattr(oneshot, "generate_oneshot_poster", fake_gen)
    monkeypatch.setattr(pp, "_brand_style_refs", lambda *a, **k: ["https://x/banner.jpg"])
    monkeypatch.setattr(pp, "_image_bytes_from_url", lambda u: (b"IMG", "image/jpeg"))
    pp._generate_nano_background({}, None, "a scene", out_dir=str(tmp_path),
                                 product_name="La Roche-Posay Anthelios",
                                 composite_product_after=True)
    assert not seen.get("refs"), (
        f"a product ad still received visual references: {seen.get('refs')}")


def test_a_brand_ad_keeps_only_screened_references(monkeypatch, tmp_path):
    import poster.oneshot as oneshot
    import poster.pipeline as pp

    seen = {}

    def fake_gen(prompt, out_path, **kw):
        seen["refs"] = kw.get("product_images") or []
        Image.new("RGB", (64, 96), (200, 200, 200)).save(out_path)
        return out_path

    banner, clean = _img(2400, 500), _img(900, 1200)
    monkeypatch.setattr(oneshot, "generate_oneshot_poster", fake_gen)
    monkeypatch.setattr(pp, "_brand_style_refs",
                        lambda *a, **k: ["https://x/banner.jpg", "https://x/clean.jpg"])
    monkeypatch.setattr(pp, "_image_bytes_from_url",
                        lambda u: ((banner if "banner" in u else clean), "image/png"))
    pp._generate_nano_background({}, None, "a scene", out_dir=str(tmp_path))
    refs = seen.get("refs") or []
    assert len(refs) == 1, f"the banner survived the screen: {len(refs)} ref(s)"
    assert refs[0][0] == clean


def test_the_rejection_is_logged_with_its_numbers(monkeypatch, tmp_path):
    import poster.oneshot as oneshot
    import poster.pipeline as pp

    lines = []
    monkeypatch.setattr(oneshot, "generate_oneshot_poster",
                        lambda p, o, **k: (Image.new("RGB", (64, 96)).save(o), o)[1])
    monkeypatch.setattr(pp, "_brand_style_refs", lambda *a, **k: ["https://x/banner.jpg"])
    monkeypatch.setattr(pp, "_image_bytes_from_url", lambda u: (_img(2400, 500), "image/png"))
    pp._generate_nano_background({}, None, "a scene", out_dir=str(tmp_path),
                                 log=lambda m: lines.append(str(m)))
    blob = " ".join(lines)
    assert "refs" in blob and "reject" in blob.lower(), blob


# --- the logo may not be invented ------------------------------------------------------------

def test_the_prompt_bans_the_brands_own_mark_by_name(monkeypatch, tmp_path):
    """The real logo is composited on top, so any OTHER mark in frame is fake by definition."""
    import poster.oneshot as oneshot
    import poster.pipeline as pp

    seen = {}

    def fake_gen(prompt, out_path, **kw):
        seen["prompt"] = prompt
        Image.new("RGB", (64, 96), (200, 200, 200)).save(out_path)
        return out_path

    monkeypatch.setattr(oneshot, "generate_oneshot_poster", fake_gen)
    monkeypatch.setattr(pp, "_brand_style_refs", lambda *a, **k: [])
    pp._generate_nano_background({"name": {"value": "AlFouad Pharmacies"}}, None, "a scene",
                                 out_dir=str(tmp_path))
    p = (seen.get("prompt") or "").lower()
    assert "alfouad" in p and ("do not" in p or "never" in p), p[-400:]


# --- أ-3: the stray-text gate stops being blind to Latin -------------------------------------

def test_invented_text_is_caught_by_the_critic_not_by_string_distance():
    """WHY THE GATE DOES NOT TRY THIS: separating a real label word from an invented one by
    string distance was measured to fail in both directions — «LABORATOIRE» and «ULTRA-LONG UVA
    PROTECTION» are real label text that appears in no product NAME, while «FIC. TRUSTWORSHY»
    is near-plausible. So Latin on a composited render is not judged from a transcription; the
    critic judges the PIXELS and answers `garbled_text` directly."""
    from poster.ocr_gate import stray_text_verdict
    v = stray_text_verdict(
        seen_lines=["FoMad", "LABORATOIRE DERMATOLOGIQUE", "FIC. TRUSTWORSHY"],
        approved=["احمي بشرتك"], brand_name="AlFouad Pharmacies",
        product_name="La Roche-Posay Anthelios Anti-Dark Spot Fluid",
        assets_composited=True)
    assert v.stray == [], f"the transcription gate is guessing at Latin again: {v.stray}"

    from poster.vision_qa import PosterQAVerdict
    assert "garbled_text" in PosterQAVerdict.model_fields, (
        "nothing asks the critic whether any rendered text is malformed")


# --- أ-4: one product in frame, measured ------------------------------------------------------

def test_the_critic_is_asked_how_many_products_are_in_frame():
    import inspect

    from poster.vision_qa import PosterQAVerdict, poster_vision_qa
    assert "product_count" in PosterQAVerdict.model_fields
    src = inspect.getsource(poster_vision_qa)
    assert "product_count" in src, "the critic is never asked to count"


def test_more_than_one_product_fails_a_named_product_ad():
    from poster.vision_qa import product_count_ok
    assert product_count_ok(1, product_named=True) is True
    assert product_count_ok(3, product_named=True) is False
    assert product_count_ok(3, product_named=False) is True      # a brand ad may show a range
    assert product_count_ok(0, product_named=True) is None       # nothing counted -> unverified
