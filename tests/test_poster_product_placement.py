# -*- coding: utf-8 -*-
"""Product PLACEMENT logic — owner (2026-07-20): "البوستر بيحط علبة الدوا في أي مكان وبأي شكل مش
مهتم هو ده مكان منطقي ولا لأ."

The root cause was that the props prompt said only "place them naturally" — no rule for WHERE or
WHY — and the vision critic never judged placement, so a floating, wrongly-scaled, shadowless box
passed. This adds the physics of placement to the prompt and a `placement_logical` criterion to the
critic.

D-8 (owner's correction): the rule is NOT "on a surface or in a hand" — that is true for a medicine
box and false for shoes (the ground), a car (the road), a sofa (a room), a building (its site). The
universal form is a NATURAL SUPPORT APPROPRIATE TO THE PRODUCT'S OWN CATEGORY. Hermetic (no renders).
"""
from __future__ import annotations

from poster.oneshot import _props_rules
from poster.pipeline import _qa_image_fixable
from poster.vision_qa import PosterQAVerdict


def test_placement_physics_appear_only_when_a_product_is_attached():
    with_product = _props_rules(n_products=1, n_places=0)
    assert "PRODUCT PLACEMENT" in with_product
    # nothing product-specific leaks into a place-only (service brand) render
    assert "PRODUCT PLACEMENT" not in _props_rules(n_products=0, n_places=2)


def test_placement_rule_is_category_universal_not_surface_only():
    r = _props_rules(n_products=1, n_places=0)
    # the universal phrasing the owner asked for — support that suits the product's own category
    assert "natural support" in r.lower()
    # and it must SAY that the support varies by category, with concrete non-tabletop examples
    for example in ("ground", "road", "room"):
        assert example in r.lower(), example
    # the failure mode being outlawed
    assert "float" in r.lower()


def test_placement_rule_covers_scale_light_and_label():
    r = _props_rules(n_products=1, n_places=0).lower()
    assert "scale" in r                       # realistic size against neighbouring objects
    assert "shadow" in r                      # contact shadow tying it to its support
    assert "light" in r                       # lit by the SAME light as the scene
    assert "label" in r and "camera" in r     # label readable, not distorted away from camera


def test_placement_must_be_where_the_product_is_actually_used():
    r = _props_rules(n_products=1, n_places=0).lower()
    assert "used" in r or "kept" in r or "displayed" in r


def test_illogical_placement_is_a_retryable_image_failure():
    # A floating/absurdly-placed product is fixable by re-rolling the image, so it must enter the
    # existing retry budget rather than shipping.
    v = PosterQAVerdict(placement_logical=False)
    assert _qa_image_fixable(v) is True
    assert _qa_image_fixable(PosterQAVerdict()) is False       # default verdict stays clean


def test_the_concepts_chosen_placement_reaches_the_render_prompt():
    # The concept decides WHERE (poster/concept._placement_rule); that decision is worthless if the
    # render prompt never sees it. It must be quoted as the binding instruction for this scene.
    from poster.oneshot import build_oneshot_prompt
    p = build_oneshot_prompt(
        {"headline": "x"}, brand_name="Nano", visual_idea="a bright kitchen morning",
        product_placement="on the marble counter beside her cup, her hand reaching for it",
        n_products=1)
    assert "on the marble counter beside her cup" in p
    # and without a decided placement the physics rules still stand on their own
    p2 = build_oneshot_prompt({"headline": "x"}, brand_name="Nano", n_products=1)
    assert "PRODUCT PLACEMENT" in p2


def test_placement_criterion_defaults_true_for_old_verdicts():
    # Back-compat: a verdict from before this criterion (or a model that omits it) must not
    # retroactively fail every poster.
    assert PosterQAVerdict().placement_logical is True
