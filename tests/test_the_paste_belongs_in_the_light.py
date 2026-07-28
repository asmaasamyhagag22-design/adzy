# -*- coding: utf-8 -*-
"""A paste reads as a paste until it agrees with the scene's light.

OWNER (2026-07-28), naming the four reasons a composite looks fake: no contact or cast shadow;
lighting and colour that do not match the scene; a mismatched camera angle; and no interaction
with anything in the frame. The angle is settled upstream — the scene is now photographed from
the elevation the packshot was. This file covers what the compositor itself owes:

  * a CAST shadow in the direction the scene's own light implies (the module had one fixed
    horizontal ellipse and no notion of where the light was)
  * brightness and colour temperature nudged toward the destination — and REFUSED when the
    correction needed would take a registered brand colour out of true. Nano Care's red going
    pink is a worse defect than a box that is slightly too bright, so the caps are deliberate
    and the refusal is the feature.
  * ONE unit for a one-product ad: the packshot the pipeline chose showed two cartons, and
    "2 products in an ad for one named product" was the critic's stated reason for the final
    rejection. Filenames cannot see that.
"""
from __future__ import annotations

import io


def _png(color, size=(200, 300), bg=(255, 255, 255)):
    from PIL import Image
    im = Image.new("RGB", size, bg)
    w, h = size
    for x in range(w // 4, 3 * w // 4):
        for y in range(h // 6, 5 * h // 6):
            im.putpixel((x, y), color)
    buf = io.BytesIO()
    im.save(buf, format="PNG")
    return buf.getvalue()


def _bg(tmp_path, color=(140, 150, 160), size=(800, 1000)):
    from PIL import Image
    p = tmp_path / "bg.png"
    Image.new("RGB", size, color).save(p)
    return p


# =====================================================================
# where the light is
# =====================================================================

def test_a_lit_side_is_detected():
    """A background brighter on the left is lit from the left."""
    from PIL import Image

    from poster.product_composite import _estimate_light
    im = Image.new("RGB", (400, 400), (60, 60, 60))
    for x in range(200):
        for y in range(400):
            im.putpixel((x, y), (210, 210, 210))
    clock = _estimate_light(im, 150, 150, 100, 100)
    assert clock in (8, 9, 10), f"light read as {clock} o'clock on a left-lit frame"


def test_a_flat_background_reports_no_direction():
    """The third state: unreadable lighting must not become a confident wrong cast."""
    from PIL import Image

    from poster.product_composite import _estimate_light
    flat = Image.new("RGB", (400, 400), (128, 128, 128))
    assert _estimate_light(flat, 150, 150, 100, 100) == 0


def test_the_clock_vector_points_where_the_clock_says():
    from poster.product_composite import _clock_vector
    assert _clock_vector(12)[1] < -0.9        # 12 o'clock is up the frame
    assert _clock_vector(3)[0] > 0.9          # 3 o'clock is to the right
    assert _clock_vector(6)[1] > 0.9          # 6 o'clock is down
    assert _clock_vector(9)[0] < -0.9         # 9 o'clock is to the left


def test_a_cast_shadow_is_drawn_away_from_the_light(tmp_path):
    """End to end on pixels: with the key light on the left, the ground darkens on the RIGHT."""
    from PIL import Image

    from poster.product_composite import composite_product
    from PIL import Image as I
    bg = tmp_path / "bg.png"
    im = I.new("RGB", (800, 1000), (90, 90, 90))
    for x in range(400):                      # brighter left half => light from the left
        for y in range(1000):
            im.putpixel((x, y), (200, 200, 200))
    im.save(bg)

    out = composite_product(bg, _png((200, 30, 40)), zone=(0.40, 0.50, 0.30),
                            mode="standing_on_surface", out_path=str(tmp_path / "o.png"))
    assert out
    res = Image.open(out).convert("L")
    W, H = res.size
    # sample the ground band just under the product, left vs right of its centre
    y0, y1 = int(H * 0.80), int(H * 0.88)
    cx = int(W * 0.55)
    left = [res.getpixel((x, y)) for y in range(y0, y1, 2) for x in range(cx - 90, cx - 20, 2)]
    right = [res.getpixel((x, y)) for y in range(y0, y1, 2) for x in range(cx + 20, cx + 90, 2)]
    assert sum(right) / len(right) < sum(left) / len(left), (
        "the ground is not darker on the side away from the light — no cast shadow was laid down")


def test_a_flat_lay_gets_no_standing_contact_ellipse(tmp_path):
    """Under an overhead camera there is no 'underneath' — an ellipse there is the mid-air
    shadow this module has warned about since 2026-07-27."""
    import inspect

    import poster.product_composite as pc
    src = inspect.getsource(pc.composite_product)
    body = src[src.find("if contact_shadow"):]
    assert "if not overhead" in body, (
        "the contact ellipse is drawn regardless of the placement mode")


# =====================================================================
# how far the product may be moved toward the scene
# =====================================================================

def test_a_small_difference_is_harmonised(tmp_path):
    from PIL import Image

    from poster.product_composite import _harmonize
    prod = Image.new("RGBA", (200, 300), (180, 175, 170, 255))
    bg = Image.new("RGB", (400, 400), (168, 166, 164)).convert("RGBA")
    out, note = _harmonize(prod, bg, 0, 0, 200, 300)
    assert "matched" in note, note
    assert out is not prod


def test_a_large_correction_is_refused_and_said_out_loud():
    """Brand colour beats scene match. Silence here would be the old failure class: a correction
    that happened and was never reported."""
    from PIL import Image

    from poster.product_composite import _harmonize
    prod = Image.new("RGBA", (200, 300), (230, 30, 40, 255))     # Nano Care red
    bg = Image.new("RGB", (400, 400), (20, 20, 90)).convert("RGBA")   # a deep blue room
    out, note = _harmonize(prod, bg, 0, 0, 200, 300)
    assert out is prod, "the brand colour was pulled toward the room"
    assert "cap" in note.lower(), note


def test_the_caps_are_tight_enough_to_protect_a_brand_colour():
    from poster.product_composite import _MAX_CHANNEL_SHIFT, _MAX_LUMA_SHIFT
    assert _MAX_LUMA_SHIFT <= 0.15
    assert _MAX_CHANNEL_SHIFT <= 0.08


def test_harmonisation_never_raises_on_a_degenerate_asset():
    from PIL import Image

    from poster.product_composite import _harmonize
    empty = Image.new("RGBA", (10, 10), (0, 0, 0, 0))
    bg = Image.new("RGB", (40, 40), (100, 100, 100)).convert("RGBA")
    out, note = _harmonize(empty, bg, 0, 0, 10, 10)
    assert out is empty and note


# =====================================================================
# one product means one unit
# =====================================================================

def test_a_single_unit_packshot_outranks_a_two_unit_one(monkeypatch):
    import poster.product_composite as pc
    import poster.product_geometry as pg

    class _G:
        def __init__(self, n):
            self.unit_count, self.checked = n, True

    monkeypatch.setattr(pg, "measure_product",
                        lambda url, **kw: _G(2 if "pair" in str(url) else 1))
    ranked = pc.rank_candidates(["https://x/pair.jpg", "https://x/solo.jpg"],
                                caller=object())
    assert ranked[0].endswith("solo.jpg"), ranked


def test_without_a_caller_the_ranking_is_unchanged():
    """No silent change and no silent cost for callers that never opted in."""
    import poster.product_composite as pc
    urls = ["https://x/b.jpg", "https://x/a.jpg"]
    assert pc.rank_candidates(list(urls)) == urls


def test_a_failed_probe_does_not_demote_a_candidate(monkeypatch):
    """A transient measurement failure must not reorder the gallery — 'not measured' is not
    'has two units'."""
    import poster.product_composite as pc
    import poster.product_geometry as pg

    class _G:
        unit_count, checked = 0, False

    monkeypatch.setattr(pg, "measure_product", lambda url, **kw: _G())
    urls = ["https://x/a.jpg", "https://x/b.jpg", "https://x/c.jpg"]
    assert pc.rank_candidates(list(urls), caller=object()) == urls


# =====================================================================
# nobody decided is not the same as this was chosen
# =====================================================================

def test_an_absent_decision_is_logged_not_silently_defaulted(tmp_path, caplog):
    import logging

    from poster.product_composite import composite_product
    with caplog.at_level(logging.INFO, logger="poster.product_composite"):
        composite_product(_bg(tmp_path), _png((200, 30, 40)),
                          out_path=str(tmp_path / "o.png"))
    assert any("no placement decision" in r.message for r in caplog.records), (
        "the compositer fell back to the unmeasured zone without saying so")
