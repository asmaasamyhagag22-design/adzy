# -*- coding: utf-8 -*-
"""A7 residuals (2026-07-18) — scraper correctness.
* CS-124: `_brand_guess` took the LEFTMOST domain label, so a subdomained brand ('store.nike.com')
  searched the open web for the meaningless token 'store' instead of 'nike'.
* CS-159: `_surrounding_text`'s "walk up to a nearby heading" loop reset to the form each hop, so it
  scanned the form's OWN siblings 3× and NEVER inspected an ancestor's siblings — an ancestor-level
  heading (the common '<section><h2>..</h2><div><form>..' layout) was missed.
Hermetic — pure functions, no network."""
from __future__ import annotations


def test_brand_guess_uses_the_registrable_label_not_the_leftmost():
    from scraper.deep_search import _brand_guess
    assert _brand_guess("lcwaikiki.eg") == "lcwaikiki"       # unchanged simple case
    assert _brand_guess("nike.com") == "nike"
    assert _brand_guess("store.nike.com") == "nike"          # subdomain no longer wins
    assert _brand_guess("brand.com.eg") == "brand"           # second-level ccTLD stripped
    assert _brand_guess("shop.brand.co.uk") == "brand"
    assert _brand_guess("localhost") == "localhost"          # single label -> itself


def test_surrounding_text_finds_an_ancestor_level_heading():
    from bs4 import BeautifulSoup

    from scraper.extractors.forms import _surrounding_text
    html = ('<section><h2>Contact Us</h2>'
            '<div class="wrap"><form><input name="email"></form></div></section>')
    form = BeautifulSoup(html, "lxml").find("form")
    assert _surrounding_text(form) == "Contact Us"           # ancestor's previous-sibling heading

    # a heading that is the form's OWN previous sibling still works (regression)
    html2 = '<div><h3>Sign up</h3><form><input name="e"></form></div>'
    form2 = BeautifulSoup(html2, "lxml").find("form")
    assert _surrounding_text(form2) == "Sign up"
