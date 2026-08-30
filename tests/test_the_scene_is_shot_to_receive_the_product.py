# -*- coding: utf-8 -*-
"""The scene and the paste must be decided together, or neither can be right.

MEASURED on a live Nano Care run (2026-07-28). The art critic rejected the poster THREE times
out of three, twice in the same words — "the product boxes are floating illogically in the scene,
which undermines the poster's credibility" — and it shipped anyway with `invented_product=True`
disclosed on the result.

The cause was not the shadow and not the zone. Reading the intercepted image calls (`calls.json`
from that run: `has_logo: False, n_product_images: 0` on all three) against the packshot the
compositor pasted, the two halves had never been introduced:

    the packshot                            the generated scene
    ------------------------------------    -------------------------------------
    looks DOWN at the product, ~45 deg      eye level, horizon at the model's chest
    flat high-key studio light              directional window light from the left
    TWO cartons leaning on each other       an ad for ONE named product
    no flat footprint at all                a marble floor across the lower third

A packshot photographed from above cannot stand on a floor photographed from eye level. That is
a contradiction in geometry, and no zone, size or contact shadow resolves it — which is exactly
why three attempts of moving things around produced three identical verdicts.

The owner named both failure modes in one sentence: «لو الموديل اللي رسم بيبوّظه، وفي نفس الوقت
اللزق بيبوّظ الباك جراوند اللي اتعمل ليها جينيريشن». The paste ruins the background because the
background was never made for it. So the placement is DECIDED first, and both halves obey it.
"""
from __future__ import annotations


def _geo(**kw):
    from poster.product_geometry import ProductGeometry
    base = dict(camera_elevation="eye_level", has_flat_base=True, form_factor="carton",
                real_scale_hint="two_hands", aspect=0.8, checked=True)
    base.update(kw)
    return ProductGeometry(**base)


# =====================================================================
# the camera follows the product, not a house rule
# =====================================================================

def test_an_overhead_packshot_makes_the_scene_overhead():
    """The Nano Care case exactly: the only resolution is to match the camera."""
    from poster.product_placement import MODE_FLAT_LAY, decide_placement, scene_instruction
    d = decide_placement(_geo(camera_elevation="high_angle", has_flat_base=False))
    assert d.mode == MODE_FLAT_LAY
    text = scene_instruction(d).lower()
    assert "looking down" in text
    assert "eye level" not in text.replace("do not shoot it at eye level", "")


def test_a_level_packshot_with_a_base_asks_for_a_surface_at_eye_level():
    from poster.product_placement import MODE_STANDING, decide_placement, scene_instruction
    d = decide_placement(_geo())
    assert d.mode == MODE_STANDING
    text = scene_instruction(d).lower()
    assert "eye level" in text and "surface" in text


def test_a_tilted_product_is_never_told_to_stand():
    """«a shadow under a base drawn in mid-air is a shadow in mid-air» — so do not ask for one."""
    from poster.product_placement import MODE_STANDING, decide_placement
    d = decide_placement(_geo(has_flat_base=False))
    assert d.mode != MODE_STANDING


def test_an_unmeasured_asset_produces_no_confident_instruction():
    """The third state: not measured must not become a confident claim about the camera."""
    from poster.product_placement import decide_placement, scene_instruction
    d = decide_placement(_geo(checked=False))
    assert d.confident is False
    assert scene_instruction(d) == ""


def test_the_light_direction_crosses_from_the_packshot_to_the_scene():
    from poster.product_placement import decide_placement, scene_instruction
    d = decide_placement(_geo(light_clock=9, light_hard=True))
    text = scene_instruction(d).lower()
    assert "left" in text and "hard" in text


# =====================================================================
# the size and the place are DECIDED, never constant
# =====================================================================

def test_the_placement_is_not_a_constant():
    """Two different products must not land in the same rectangle."""
    from poster.product_placement import decide_placement
    a = decide_placement(_geo(form_factor="sachet", real_scale_hint="palm"))
    b = decide_placement(_geo(form_factor="apparel", real_scale_hint="large"))
    assert a.zone != b.zone
    assert a.height_frac < b.height_frac


def test_the_product_keeps_off_the_copy():
    """«كل الاحتمالات بناءً ع المنتج والبوستر مكتوب عليه إيه» — the copy is an input."""
    from poster.product_placement import decide_placement, zone_rect

    def _overlap(r, c):
        return not (r[0] + r[2] <= c[0] or c[0] + c[2] <= r[0]
                    or r[1] + r[3] <= c[1] or c[1] + c[3] <= r[1])

    for box in ([0.06, 0.07, 0.88], [0.06, 0.62, 0.88], [0.44, 0.45, 0.52]):
        d = decide_placement(_geo(), text_box=box)
        rect = zone_rect(d.zone, aspect=0.8, canvas_aspect=0.8)
        copy = (box[0], box[1], box[2], 0.22)
        assert not _overlap(rect, copy), f"product {rect} sits on the copy {copy}"


def test_moving_the_copy_moves_the_product():
    """The contrast retry relocates the headline; a product decided against the OLD box would be
    reasoning about a layout that no longer exists."""
    from poster.product_placement import decide_placement
    top = decide_placement(_geo(), text_box=[0.06, 0.07, 0.88])
    bottom = decide_placement(_geo(), text_box=[0.06, 0.66, 0.88])
    assert top.zone != bottom.zone


def test_the_product_stays_inside_the_frame():
    from poster.product_placement import decide_placement, zone_rect
    for aspect in (0.35, 0.8, 1.0, 2.4):
        d = decide_placement(_geo(aspect=aspect), text_box=[0.06, 0.07, 0.88])
        x, y, w, h = zone_rect(d.zone, aspect=aspect, canvas_aspect=0.8)
        assert 0.0 <= x and x + w <= 1.0001, (aspect, x, w)
        assert 0.0 <= y and y + h <= 1.0001, (aspect, y, h)


# =====================================================================
# the prompt actually carries the decision (written is not wired)
# =====================================================================

def test_the_scene_guard_uses_the_decision():
    from poster.pipeline import _product_scene_guard
    from poster.product_placement import decide_placement
    d = decide_placement(_geo(camera_elevation="overhead", has_flat_base=False))
    block = _product_scene_guard("نانوكير", composited=True, placement=d).lower()
    assert "looking down" in block, "the decision never reached the prompt"
    assert "lower third" not in block, (
        "the generic lower-third floor rule is still being sent to a scene that must be shot "
        "from above — the two instructions contradict each other")


def test_the_generic_rule_survives_when_nothing_was_decided():
    """Removing the fallback would trade a weak answer for none."""
    from poster.pipeline import _product_scene_guard
    block = _product_scene_guard("نانوكير", composited=True).lower()
    assert "lower third" in block and "surface" in block


def test_the_pipeline_decides_before_it_generates():
    """Order of operations is the whole fix; assert it on the source, not on a docstring."""
    import inspect

    import poster.pipeline as P
    src = "\n".join(l.split("#")[0] for l in inspect.getsource(P).splitlines())
    decide = src.find("_placement = _decide(")
    generate = src.find("_generate_background(", decide)
    paste = src.find("compose_or_background(", decide)
    assert decide > 0, "no placement decision is taken in the render loop"
    assert 0 < generate and decide < generate, "the scene is generated before the decision"
    assert 0 < paste and decide < paste, "the product is pasted before the decision"


def test_the_composite_is_told_the_mode():
    """The shadow's physics depend on it; passing the zone alone would silently keep the old
    standing-object shadow under a flat-lay."""
    import inspect

    import poster.pipeline as P
    src = inspect.getsource(P)
    at = src.find("_mode = compose_or_background(")
    assert at > 0, "the composite is not called from the render loop"
    call = "\n".join(src[at:].splitlines()[:8])
    assert "mode=" in call, f"the placement mode never reaches the compositor:\n{call}"
    assert "zone=" in call, f"the decided zone never reaches the compositor:\n{call}"
