# -*- coding: utf-8 -*-
"""The brand-name guard must work when the name and the products are in different scripts.

MEASURED (nanocare, 2026-07-21) — AFTER the first brand-leak fix landed, the query was still:

    'ecommerce نانو كير ليبوسومال فيتامين'

The profile's `name` is the English "Nano Care Therapeutics" while the offerings are Arabic
("نانو كير ليبوسومال حديد"), so stripping `tokenize(name)` removed nothing: the two never compare
equal. This is the same cross-script gap already solved once for trend seeds, where
`trends.engine.brand_identity_tokens` infers brand words from OFFERING FREQUENCY (a token riding
most offerings is the brand, whatever alphabet it is written in) instead of from the name alone.

Reuse it here rather than write a second, weaker guard. Hermetic."""
from __future__ import annotations

from competitor.discovery import build_match_criteria, build_text_query


def _nanocare():
    products = ["نانو كير ليبوسومال حديد", "نانو كير ليبوسومال سي",
                "نانوكير مالتى كولاجين", "نانو كير ليبوسومال جلوتاثيون"]
    return {"name": {"value": "Nano Care Therapeutics"},
            "category": {"value": "ecommerce"},
            "source_url": {"value": "https://nanocareegypt.com/"},
            "offerings": [{"name": {"value": p}} for p in products]}


def test_an_arabic_brand_word_is_stripped_from_an_english_named_brand():
    q = build_text_query(build_match_criteria(_nanocare()))
    assert "نانو" not in q, q
    assert "كير" not in q, q


def test_a_class_term_riding_every_product_is_dropped_with_the_brand():
    """MEASURED TRADE-OFF, recorded rather than hidden: `brand_identity_tokens` infers brand words
    from offering frequency, and for a brand named "Nano Care" selling "Nano Care Liposomal X",
    BOTH the brand words AND the product class ("ليبوسومال") ride every product. Frequency alone
    cannot separate them.

    We err toward dropping. A leaked brand word makes the search return the brand itself — a
    guaranteed zero — while a dropped class term only costs some recall, and the web engine's own
    query builder (`web_discovery._identity_terms`) still carries the market wording. Wrong-answer
    risk beats missing-answer risk here."""
    q = build_text_query(build_match_criteria(_nanocare()))
    assert "ليبوسومال" not in q


def test_the_latin_brand_word_is_still_stripped():
    p = _nanocare()
    p["offerings"] = [{"name": {"value": "Nano Care Liposomal Iron"}},
                      {"name": {"value": "Nano Care Liposomal C"}},
                      {"name": {"value": "Nano Care Multi Collagen"}},
                      {"name": {"value": "Nano Care Liposomal Glutathione"}}]
    q = build_text_query(build_match_criteria(p)).lower()
    assert "nano" not in q and "care" not in q, q


def test_a_brand_whose_name_shares_nothing_with_products_is_untouched():
    p = {"name": {"value": "Volume Cafe"}, "category": {"value": "cafe"},
         "source_url": {"value": "https://volumeqatar.com/"},
         "offerings": [{"name": {"value": "Tomato Basil Soup"}},
                       {"name": {"value": "Baked Salmon"}}]}
    q = build_text_query(build_match_criteria(p))
    assert q.split()[0] == "cafe"
    assert "tomato" in q.lower()          # ordinary product words are not brand words
