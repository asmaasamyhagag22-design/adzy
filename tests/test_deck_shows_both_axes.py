# -*- coding: utf-8 -*-
"""The command deck must show the MARKET (what) and, when known, the MODEL (how) — not a coarse
conflated label.

Step 3 wiring into the dashboard (process.md §8.11). The brand chip used to print `category`, which
for many brands is the business model ("services_b2c" on Coca-Cola) rather than the market. It now
prefers the free-text `market_category`, and shows `business_model` as its own labelled chip when
it is known. Hermetic — pure HTML assembly."""
from __future__ import annotations

from dashboard.war_room import _brand_chips


def test_the_market_chip_prefers_the_free_text_market():
    html = _brand_chips({"market_category": {"value": "مكملات ليبوسومال"},
                         "category": {"value": "ecommerce"}})
    assert "مكملات ليبوسومال" in html
    assert "ecommerce" not in html                # the coarse/model label is not the market


def test_the_model_is_shown_as_its_own_readable_chip():
    html = _brand_chips({"market_category": {"value": "handmade jewellery"},
                         "business_model": "ecommerce"})
    assert "متجر أونلاين" in html                  # ecommerce -> a human word, not the enum token
    assert "handmade jewellery" in html


def test_an_unknown_model_shows_no_model_chip():
    html = _brand_chips({"market_category": {"value": "cafe"}, "business_model": "unknown"})
    assert "أونلاين" not in html and "محلي" not in html


def test_falls_back_to_category_when_market_is_absent():
    html = _brand_chips({"category": {"value": "restaurant"}})
    assert "restaurant" in html                   # un-migrated profile still shows something


def test_nothing_at_all_renders_no_chip():
    assert _brand_chips({}) == ""
