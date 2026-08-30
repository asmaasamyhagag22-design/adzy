# -*- coding: utf-8 -*-
"""EVERY copy element uses the measured ink — not just the ones I remembered.

OWNER (2026-07-26, after saying it on the FIRST poster and every one since):
«ومن أول بوسط بقولك على الكلام الأبيض اللي فوق الزرار وانت مش هنا».

She was right every time. The ink measurement was threaded into `.canvas` and `.brand`, while
the two elements she was actually pointing at kept hardcoded near-white constants:

    .sub          color:#e6ecf2      <- the line above the button, every poster
    .offerings li color:#f3f7fb      <- the proof chips

So on a bright scene the headline flipped to dark and the subline stayed invisible. A fix that
covers "the parts I thought of" is not a fix.
"""
from __future__ import annotations

import re


def _css(ink: str) -> str:
    from poster.schemas import PosterBrief
    from poster.template import render_poster_html
    brief = PosterBrief(business_name="Shop", headline="عنوان", subheadline="سطر تحت العنوان",
                        cta_text="اطلبي", offerings=["ميزة"])
    return render_poster_html(brief, None, copy_ink=ink)


def test_no_copy_element_keeps_a_hardcoded_near_white():
    html = _css("#141414")
    for dead in ("#e6ecf2", "#f3f7fb", "#f5f3ef"):
        assert dead not in html, f"{dead} survived the ink measurement"


def test_the_subline_and_chips_follow_the_dark_ink():
    html = _css("#141414")
    sub = re.search(r"\.sub \{\{?(.*?)\}\}?", html, re.S)
    assert sub and "#141414" in sub.group(1), sub.group(1) if sub else html[:200]


def test_white_ink_still_renders_white_copy():
    html = _css("#ffffff")
    assert "#ffffff" in html


def test_the_chip_plate_follows_the_ink_too():
    """A dark chip plate under dark ink is a black box with black text."""
    dark = _css("#141414")
    m = re.search(r"\.offerings li \{\{?(.*?)\}\}?", dark, re.S)
    assert m, dark[:200]
    body = m.group(1)
    assert "rgba(10,14,20,.58)" not in body, "dark chips kept their dark plate under dark ink"
