# -*- coding: utf-8 -*-
"""The prompt that actually reaches the director, checked as a whole.

OWNER, 2026-08-08: «ما تثقيش في إن البرومبت بقى صادق لمجرد إن الـcode اتعدل. اطبعي الـfinal
assembled prompt… لو لقيتي فيه أي حاجة من دول يبقى لسه فيه contamination من الـservice branch.»

She is right, and this file is that check. Every earlier test here measured a FRAGMENT — a flag, a
call site, one clause. The defect was never in a fragment: it was two correct fragments assembled
into one message that contradicted itself.

The list below is hers, verbatim in meaning. Any of these surviving into a product-focused brief
means the stockist branch leaked back in.
"""
from __future__ import annotations

import pytest

from reel.creative_director import _system_prompt, _vertical_mode

# Phrases that may not appear in a PRODUCT-FOCUSED brief. Lower-cased substring match, because the
# failure is semantic and a reworded leak is still a leak.
_CONTAMINATION = (
    "do not build the film around one product",
    "this brand's product is its service",
    "no dedicated product photo exists",
    "an ad for that item's maker",
    "they are being served, not applying a cream",
)


def _pharmacy() -> dict:
    return {
        "name": "AlFouad Pharmacies",
        "category": "ecommerce",
        "description": ("online retailer offering health and beauty products: skincare, hair care, "
                        "vitamins, perfumes and pharmaceutical items"),
        "catalog": [{"name": "Vichy Dercos Densi-Solutions", "url": "https://x/p/1",
                     "image_url": "https://x/1.webp"}],
    }


def _assembled(product_ad: bool) -> str:
    p = _pharmacy()
    featured = "Vichy Dercos Densi-Solutions Hair Mass Creator - 100ml" if product_ad else None
    return _system_prompt(4, "ar", _vertical_mode(p, featured or "", product_ad=product_ad),
                          featured=featured, product_known=product_ad, product_ad=product_ad)


@pytest.mark.parametrize("phrase", _CONTAMINATION)
def test_no_stockist_clause_survives_into_a_product_focused_brief(phrase):
    got = _assembled(product_ad=True).lower()
    assert phrase not in got, (
        f"the assembled prompt still carries {phrase!r} — the service branch leaked back into a "
        "reel whose subject the caller named, which is the contradiction that produced reels about "
        "the pharmacy instead of the product")


def test_the_brand_reel_keeps_the_stockist_rule():
    """The rule is CORRECT for a brand reel. Removing it everywhere would trade one defect for
    another: «جايب حد بيستعمل برودكت، فين السيناريو فين الماركت»."""
    got = _assembled(product_ad=False).lower()
    assert "do not build the film around one product" in got, (
        "a pharmacy with no product named is still a stockist, and its reel is about the shop")


def test_the_product_focused_brief_says_what_the_film_is():
    """Not merely the ABSENCE of the wrong instruction — the PRESENCE of the right one. The owner's
    test: the assembled prompt must mean "a product ad for Vichy Dercos, sold by AlFouad"."""
    got = _assembled(product_ad=True)
    low = got.lower()
    assert "product-focused reel" in low and "overrides any rule below" in low, (
        "nothing in the prompt states that this is a product ad, so the routing fix is the only "
        "thing holding the contradiction back")
    assert "the featured product is the hero" in low
    assert "the stockist / service rule does not apply here" in low
    assert "seller and the cta destination" in low, (
        "the retailer's role in a product ad is unstated, so the film can drift back to the shop")
    assert "Vichy Dercos" in got, "the product is not named in its own brief"


def test_a_retailer_fact_may_not_become_a_product_claim():
    """Two brands are on screen and their evidence is not interchangeable. «Trusted by over 7,000
    customers daily» is true OF ALFOUAD; attached to Vichy it is a fabricated product claim."""
    low = _assembled(product_ad=True).lower()
    assert "claim ownership" in low, "nothing tells the director whose evidence is whose"
    assert "identity below belongs to the retailer" in low
    assert "product claims only from" in low


def test_product_evidence_and_generation_seed_are_named_as_different_things():
    """The owner's distinction: the director knowing the product exists is not the same as Veo
    being seeded on its packshot. Conflating them is what produced 'no dedicated product photo'."""
    low = _assembled(product_ad=True).lower()
    assert "product evidence" in low and "generation seed" in low, (
        "the brief does not separate what the director KNOWS from what the generator is FED")
    assert "composites it" in low, "the brief no longer says the real photograph reaches the film"
