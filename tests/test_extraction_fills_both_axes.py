# -*- coding: utf-8 -*-
"""The extractor must fill both axes — the market in the brand's words, the model from the site.

Step 2 of splitting the conflated `category` (see process.md §8.11). The LLM identity prompt already
asks for one `category`; it now also asks for `market_category` (free text — what is sold) and
`business_model` (closed — how it sells). Hermetic — prompt shape + the response→profile mapping."""
from __future__ import annotations

from business_profile.llm.prompts import build_identity_prompt
from business_profile.llm.responses import IdentityResponse
from tests.test_llm_extractor import _small_pack


def _prompt():
    return build_identity_prompt(_small_pack())


def test_the_prompt_asks_for_the_market_in_the_brands_own_words():
    p = _prompt()
    assert "market_category" in p
    assert "own words" in p or "free text" in p or "not from a list" in p
    # and it must say it is NOT the closed list `category` uses
    assert "jewell" in p.lower() or "supplements" in p.lower() or "beverage" in p.lower()


def test_the_prompt_asks_how_the_business_sells_from_a_closed_set():
    p = _prompt()
    assert "business_model" in p
    for token in ("ecommerce", "local", "service", "hybrid"):
        assert token in p, token


def test_the_response_carries_both_new_fields():
    r = IdentityResponse(market_category_value="handmade jewellery",
                              business_model_value="ecommerce")
    assert r.market_category_value == "handmade jewellery"
    assert r.business_model_value == "ecommerce"


def test_both_fields_are_optional_so_old_responses_still_parse():
    r = IdentityResponse(category_value="ecommerce")
    assert r.market_category_value is None
    assert r.business_model_value is None
