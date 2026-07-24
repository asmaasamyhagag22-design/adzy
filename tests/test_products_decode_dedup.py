# -*- coding: utf-8 -*-
"""1.6 (owner report, measured on nanocare): the services picker showed the SAME products twice —
once clean from the profile offerings (with prices) and once as 151-char percent-encoded URL slugs
('%D9%86%D8%A7...') that _titleize never unquoted. Pins: decode at the source, dedup by a
normalized key (punctuation/case-proof), MERGE duplicates (the priced offering wins name+price,
the page contributes url/image), and drop non-names (>80 chars after decoding)."""
from __future__ import annotations

from urllib.parse import quote

from dashboard.products import _titleize, products_from_manifest

_AR = "نانو-كير-ليبوسومال-فيتامين-d3-mk7-k2"


def _manifest(pages):
    return {"pages": pages, "images_of_interest": [
        {"src": "https://cdn.example/vitd3.jpg", "alt": "",
         "page_url": f"https://shop.example/products/{quote(_AR)}", "role": "content"}]}


def test_titleize_unquotes_percent_encoded_slugs():
    t = _titleize(quote(_AR))
    assert "%" not in t and "نانو" in t


def test_duplicate_offering_and_encoded_page_merge_into_one():
    profile = {"offerings": [{"name": {"value": "نانو كير ليبوسومال فيتامين D3 / (MK7) K2"},
                              "price_text": "220.00 ج.م"}]}
    pages = [{"url": f"https://shop.example/products/{quote(_AR)}",
              "final_url": f"https://shop.example/products/{quote(_AR)}",
              "page_type": "products", "text_blocks": []}]
    out = products_from_manifest(_manifest(pages), limit=12, profile=profile)
    assert len(out) == 1                                       # ONE product, not two
    p = out[0]
    assert p["name"] == "نانو كير ليبوسومال فيتامين D3 / (MK7) K2"   # the priced offering's name
    assert p["price_text"] == "220.00 ج.م"
    assert p["url"].startswith("https://shop.example/products/")     # page contributed the url
    assert p["image"] == "https://cdn.example/vitd3.jpg"             # ...and its on-page image


def test_unnamable_giant_slug_is_dropped_not_shown():
    giant = quote("نص طويل جداً " * 20)
    pages = [{"url": f"https://shop.example/products/{giant}",
              "final_url": f"https://shop.example/products/{giant}",
              "page_type": "products", "text_blocks": []}]
    out = products_from_manifest({"pages": pages, "images_of_interest": []}, limit=12)
    assert out == []                                            # a 200-char string is not a name
