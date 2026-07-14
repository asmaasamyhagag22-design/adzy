"""Reel creative contracts compose into the director system prompt (Phase A).

The 8 [BRACKET-TAG] contracts are what a creative director enforces so the failing classes
(product hidden, cliché hook, split CTA, mixed dialect, dead air, verbatim captions, generic,
infeasible) don't recur every run. The coherence gate (reel/coherence.py) re-checks each in code.
"""
from __future__ import annotations

from reel.creative_director import _locked_register, _system_prompt
from reel.reel_contracts import CONTRACT_TAGS, REEL_CRAFT_CONTRACTS


def test_all_contract_tags_compose_into_the_system_prompt():
    p = _system_prompt(6, "ar", "generic", register=_locked_register("ar"))
    for tag in CONTRACT_TAGS:
        assert tag in p, tag
    assert REEL_CRAFT_CONTRACTS in p


def test_screens_block_keeps_abstract_but_adds_real_screen_exception():
    # the "product hidden" root cause: screens are forced abstract — the fix carves a hero-product
    # exception routed to real_screen post-compositing, without dropping the "Veo renders junk" rule.
    p = _system_prompt(6, "en", "generic")
    assert "ABSTRACT" in p                                   # the original hard rule is intact
    assert 'product_overlay="real_screen"' in p             # the exception that SHOWS a software product


def test_locked_register_is_named_in_the_prompt_and_universal():
    ar = _system_prompt(6, "ar", "generic", register=_locked_register("ar"))
    assert "LOCKED DIALECT" in ar and "Egyptian colloquial" in ar
    # a non-Arabic brand locks a conversational register of its own language (universal, no hack)
    assert _locked_register("en").startswith("conversational")
    assert "Egyptian" in _locked_register("ar")            # Arabic defaults to Egyptian colloquial


def test_new_scene_fields_are_specified_so_the_model_emits_them():
    p = _system_prompt(6, "ar", "generic")
    for field in ("hero_product", "product_overlay", "caption_role", "sfx", "sound_mood"):
        assert field in p, field
    # reel-level fields appear in the forced JSON shape
    for field in ("dialect", "sound_design", "brand_unique_claim"):
        assert field in p, field


def test_dead_air_is_no_longer_encouraged():
    # the old prompt said "one or two scenes SHOULD be silent" — that invited dead air. A silent
    # scene is now allowed ONLY when sound carries it.
    p = _system_prompt(6, "ar", "generic")
    assert "SHOULD be silent" not in p
    assert "dead air" in p
