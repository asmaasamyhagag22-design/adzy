# -*- coding: utf-8 -*-
"""P-10 (2026-07-18): the content-calendar prompt (strategy._persona_block) saw only
name/category/tagline/audience/tone + offering NAMES. It was BLIND to the brand's real PRICES
(_profile_topics dropped price_text), its COUNTRY, the campaign OBJECTIVE (media_plan), and the
SWOT/TOWS strategy — so the calendar could not write a priced offer, a country-anchored angle, or
a strength-led narrative. Thread them into the persona the LLM sees. Hermetic — pure string build."""
from __future__ import annotations

from strategy.builder import _persona_block

_PROFILE = {
    "name": {"value": "Baseera Clinic"},
    "source_url": "https://baseera.sa/",
    "category": {"value": "clinic"},
    "tagline": {"value": "نتائج تدوم"},
    "offerings": [
        {"name": {"value": "Laser session"}, "price_text": {"value": "800 SAR"}},
        {"name": {"value": "Consultation"}, "price_text": {"value": "150 SAR"}},
    ],
}
_SWOT = {"strengths": [{"text": "board-certified dermatologists"}],
         "opportunities": [{"text": "rising demand for laser in Riyadh"}]}
_MEDIA_PLAN = {"objective": {"value": "lead_generation"}}


def test_persona_block_is_backward_compatible():
    block = _persona_block(_PROFILE)
    assert "Baseera Clinic" in block and "Laser session" in block


def test_persona_block_now_carries_prices_country_objective_and_swot():
    block = _persona_block(_PROFILE, swot=_SWOT, media_plan=_MEDIA_PLAN)
    assert "800 SAR" in block and "150 SAR" in block            # real price points reach the LLM
    assert "Saudi Arabia" in block                              # evidenced country anchors the angle
    assert "lead_generation" in block                           # campaign objective biases the mix
    assert "board-certified dermatologists" in block            # SWOT strength to amplify
    assert "rising demand for laser" in block                   # SWOT opportunity to seize
