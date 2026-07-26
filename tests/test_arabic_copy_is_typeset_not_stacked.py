# -*- coding: utf-8 -*-
"""Arabic copy is TYPESET, not stacked — and it gets a real plate, not a wash.

OWNER, on the Al Fouad La Roche poster (2026-07-26):
  «الكلام المكتوب دايزينه وحش جدا وبيموت الصورة والبوستر بيبقى عريض وتقص»
  «الكلام اللي تحت وحش مش باين ضعيف، طريقة الكتابة مستحيلة صعبة، بتبوّظ المنتج تماماً»
  «شعر إيه مع صن سكرين؟»

MEASURED on that render:
  1. The `lockup` / `stacked_hero` treatments put ONE WORD PER LINE. In Latin that reads as an
     ad lockup ("BUSINESS / IDEAS"); in Arabic it breaks the word-shape rhythm the script is
     read by, forces a huge size to fill the column, and lands the result across the model and
     the product. Arabic is read as connected words — it must WRAP as a block.
  2. The sub-line sits on a GRADIENT. A gradient is a hope; on a bright product shot the light
     grey copy simply vanished. Body copy needs a plate with a measured contrast ratio.
  3. The background carried a straightened-HAIR panel next to a SUNSCREEN. The brand DNA
     (learned from real Al Fouad creatives) names "Before/After" hair shots as a signature —
     true about the brand, wrong for this product. The item being advertised has to outrank a
     learned motif.
"""
from __future__ import annotations

import re


def _html(headline: str, treatment: str, rtl: bool, column_px: int = 900) -> str:
    from poster.schemas import PosterBrief
    from poster.template import _headline_block
    return _headline_block(PosterBrief(business_name="Shop", headline=headline),
                           rtl, treatment=treatment, column_px=column_px)


def test_arabic_headlines_are_never_one_word_per_line():
    """Every stacked treatment must fall back to the wrapping block for RTL copy."""
    text = "بقع الشمس مضايقاكي في الصيف؟"
    for treatment in ("lockup", "stacked_hero", "highlight"):
        html = _html(text, treatment, rtl=True)
        assert 'class="lw' not in html and 'class="hw' not in html, (
            f"{treatment} still stacks Arabic one word per line: {html[:160]}")
        assert "headline" in html


def test_latin_keeps_its_designed_lockup():
    """The treatment is right for Latin display copy — it is the SCRIPT that decides."""
    html = _html("BIG SUMMER SALE", "lockup", rtl=False)
    assert 'class="lw' in html, html[:160]


def test_arabic_body_copy_sits_on_a_plate_not_a_gradient():
    from poster.template import _lower_css
    css = _lower_css("bottom_band", 0.82, "right", True)
    assert "linear-gradient" in css
    assert "backdrop-filter" in css or "rgba(8,12,18,0.82)" in css or "0.82) 72%" in css, css


def test_the_product_outranks_a_learned_motif_in_the_scene_prompt():
    """«شعر إيه مع صن سكرين؟» — the DNA's before/after HAIR signature is true about the brand
    and wrong for a sunscreen. When a product is named, the prompt must forbid unrelated
    categories in frame."""
    from poster.pipeline import _product_scene_guard
    g = _product_scene_guard("La Roche-Posay Anthelios Anti-Dark Spot Fluid SPF50+ 50ml")
    low = g.lower()
    assert "no other product" in low, g
    assert "hair" in low and "unrelated" in low, g
    assert _product_scene_guard("") == ""
