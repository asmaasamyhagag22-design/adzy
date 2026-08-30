# -*- coding: utf-8 -*-
"""The concept decides WHERE the product belongs — not the renderer improvising.

Owner (2026-07-20): the poster dropped the medicine box anywhere, any way. Placement physics were
added to the render prompt (tests/test_poster_product_placement.py), but "where does THIS product
belong in THIS scene" is a creative decision that must be made WITH the concept — in the SAME model
call, so it costs nothing extra (owner's ruling against multiplying LLM calls).

D-8: the field asks for the product's own natural support, never "a surface" — that would be a
tabletop rule masquerading as a universal one. Hermetic (no model calls)."""
from __future__ import annotations

from poster.concept import CreativeConcept, _placement_rule


def test_concept_carries_a_product_placement_field():
    assert CreativeConcept().product_placement == ""   # back-compat default for old concepts


def test_placement_is_requested_only_when_a_product_is_featured():
    assert "product_placement" in _placement_rule("Liposomal Glutathione")
    # a service brand with no featured product is not asked to place a nonexistent object
    assert _placement_rule(None) == "" and _placement_rule("") == ""


def test_placement_instruction_is_category_universal():
    p = _placement_rule("Running Shoe").lower()
    # the owner's phrasing: a natural support for the product's KIND, with non-tabletop examples
    assert "natural support" in p
    for example in ("ground", "road", "room", "hand"):
        assert example in p, example
    # tied to the scene the concept itself invented, and the failure mode named
    assert "visual_idea" in p
    assert "float" in p


def test_placement_rule_reaches_the_concept_prompt(monkeypatch):
    # The rule must actually be SENT, not just exist: capture the system prompt the caller sees.
    import poster.concept as pc
    seen = {}

    def fake_caller(system, user, schema, **kw):
        seen["system"] = system
        raise RuntimeError("stop after capture")     # concept falls back; we only need the prompt

    pc.build_creative_concept({"name": {"value": "Nano"}}, caller=fake_caller,
                              featured_product="Liposomal Glutathione")
    assert "product_placement" in seen.get("system", "")
