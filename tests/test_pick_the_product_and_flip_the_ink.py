# -*- coding: utf-8 -*-
"""Two roots the v9 log named exactly (2026-07-26).

  1. `[product] real asset composited` ran on all three attempts — and the thing it composited
     was the SITE LOGO. The gallery collects every <img> on the product page, the header logo is
     a clean transparent PNG, so it won the "first candidate that keys cleanly" race and was
     pasted at 44% of the canvas: «large, blurry graphics… a giant distorted logo artifact
     dominates the background» (the art critic's own words, three attempts running).
  2. `[contrast] worst 2.72 -> 2.01 -> 1.16 (FAIL)` on every attempt. The gate measures
     correctly and the remedy was wrong: on a bright scene the answer is not to move white copy
     somewhere else, it is to STOP WRITING IN WHITE.
"""
from __future__ import annotations


# --- 1) pick the PRODUCT, not the chrome -------------------------------------------------------

def test_the_products_own_slug_outranks_the_site_logo():
    from poster.product_composite import rank_candidates
    urls = [
        "https://shop.test/cdn/shop/files/Logo_colors.png?width=125",
        "https://shop.test/cdn/shop/files/payment-visa.svg",
        "https://shop.test/cdn/shop/files/la-roche-anthelios-uvmune-50ml.jpg?width=1200",
    ]
    best = rank_candidates(urls, product_url="https://shop.test/products/la-roche-sunblock-anthelios-uvmune")
    assert "anthelios" in best[0], best


def test_obvious_chrome_is_dropped_entirely():
    from poster.product_composite import rank_candidates
    urls = ["https://s.test/logo.png", "https://s.test/icon-cart.svg",
            "https://s.test/flags/eg.png", "https://s.test/sprite.png",
            "https://s.test/products/serum-40ml.jpg"]
    out = rank_candidates(urls, product_url="https://s.test/products/serum-40ml")
    assert out and "serum" in out[0]
    assert not any(k in u for u in out for k in ("logo", "icon-", "sprite", "flags/"))


def test_with_no_slug_signal_the_order_is_preserved():
    from poster.product_composite import rank_candidates
    urls = ["https://s.test/a.jpg", "https://s.test/b.jpg"]
    assert rank_candidates(urls, product_url="") == urls


def test_the_composite_ranks_before_it_keys(monkeypatch, tmp_path):
    import poster.product_composite as pc
    from PIL import Image

    tried = []
    monkeypatch.setattr(pc, "_fetch", lambda u: (tried.append(u), None)[1])
    bg = tmp_path / "bg.png"
    Image.new("RGB", (600, 800), (40, 40, 40)).save(bg)
    pc.composite_product(bg, ["https://s.test/logo.png", "https://s.test/products/x-50ml.jpg"],
                         product_url="https://s.test/products/x-50ml")
    assert tried and "x-50ml" in tried[0], tried


# --- 2) on a bright scene, write in dark ink ---------------------------------------------------

def test_ink_flips_to_dark_on_a_bright_background(tmp_path):
    from PIL import Image

    from poster.contrast_gate import ink_for_background
    p = tmp_path / "bg.png"
    Image.new("RGB", (400, 500), (232, 232, 236)).save(p)
    ink = ink_for_background(p, zones=[(0.05, 0.6, 0.9, 0.3)])
    assert ink.lower() in ("#141414", "#111111", "#0f1115"), ink


def test_ink_stays_white_on_a_dark_background(tmp_path):
    from PIL import Image

    from poster.contrast_gate import ink_for_background
    p = tmp_path / "bg.png"
    Image.new("RGB", (400, 500), (22, 24, 30)).save(p)
    assert ink_for_background(p, zones=[(0.05, 0.6, 0.9, 0.3)]).lower() == "#ffffff"


def test_an_unreadable_background_keeps_white(tmp_path):
    from poster.contrast_gate import ink_for_background
    bad = tmp_path / "x.png"
    bad.write_bytes(b"nope")
    assert ink_for_background(bad, zones=[(0, 0, 1, 1)]).lower() == "#ffffff"


def test_the_template_uses_the_measured_ink():
    import inspect

    import poster.template as T
    src = "\n".join(l.split("#")[0] for l in inspect.getsource(T).splitlines())
    assert "copy_ink" in src, "the copy colour is still a constant"


def test_the_pipeline_measures_the_ink():
    import inspect

    import poster.pipeline as pp
    src = "\n".join(l.split("#")[0] for l in inspect.getsource(pp).splitlines())
    assert "ink_for_background" in src
