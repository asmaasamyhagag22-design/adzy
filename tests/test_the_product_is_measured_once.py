# -*- coding: utf-8 -*-
"""Measuring the packshot, and confirming the surface the model actually rendered.

Both halves of "the scene and the paste must agree" need a measurement, and both must be able to
say NOTHING WAS MEASURED. That distinction is the one this repo keeps paying for: a `True` that
means "verified" and a `True` that means "there was nothing to check" are not the same value, and
`checked=False` / an unchanged zone is how each of these says the second one out loud.
"""
from __future__ import annotations


def _caller(**fields):
    """A multimodal caller stub in the shape `poster.vision_qa` uses."""
    def _call(system, user, model, group_name=None, images=None):
        assert images, "the packshot was never attached to the measurement call"
        return model(**fields), {}
    return _call


def _png(tmp_path, size=(200, 300), color=(200, 30, 40)):
    from PIL import Image
    p = tmp_path / "prod.png"
    Image.new("RGB", size, color).save(p)
    return str(p)


# =====================================================================
# measuring the asset
# =====================================================================

def test_no_caller_means_not_measured(tmp_path):
    from poster.product_geometry import measure_product
    geo = measure_product(_png(tmp_path), caller=None, use_cache=False)
    assert geo.checked is False
    assert "no vision caller" in geo.reason
    assert geo.aspect > 0, "the deterministic measurements should still be available"


def test_a_failed_call_is_not_a_measurement(tmp_path):
    from poster.product_geometry import measure_product

    def _boom(*a, **kw):
        raise RuntimeError("429")

    geo = measure_product(_png(tmp_path), caller=_boom, use_cache=False)
    assert geo.checked is False and "failed" in geo.reason


def test_an_unknown_elevation_is_not_silently_accepted(tmp_path):
    """A model answering 'slightly above' must not create a fifth branch nobody handles."""
    from poster.product_geometry import measure_product
    geo = measure_product(_png(tmp_path), caller=_caller(camera_elevation="slightly above"),
                          use_cache=False)
    assert geo.camera_elevation == "unknown"


def test_an_overhead_packshot_is_flagged_and_cannot_stand(tmp_path):
    from poster.product_geometry import measure_product
    geo = measure_product(_png(tmp_path),
                          caller=_caller(camera_elevation="high_angle", has_flat_base=True),
                          use_cache=False)
    assert geo.is_overhead is True
    assert geo.can_stand is False, (
        "a packshot photographed from above must never be told to stand on an eye-level floor")


def test_an_unmeasured_base_is_not_a_yes(tmp_path):
    from poster.product_geometry import ProductGeometry
    geo = ProductGeometry(camera_elevation="eye_level", has_flat_base=None, checked=True)
    assert geo.can_stand is False


def test_the_aspect_comes_from_the_pixels(tmp_path):
    from poster.product_geometry import measure_product
    geo = measure_product(_png(tmp_path, size=(400, 200)), caller=None, use_cache=False)
    assert 1.9 < geo.aspect < 2.1


def test_a_failed_measurement_is_never_cached(tmp_path, monkeypatch):
    """Caching a failure would pin a brand to 'unknown' forever after one transient error."""
    import poster.product_geometry as pg
    monkeypatch.setenv("POSTER_GEOMETRY_CACHE", str(tmp_path / "cache"))
    path = _png(tmp_path)

    def _boom(*a, **kw):
        raise RuntimeError("429")

    assert pg.measure_product(path, caller=_boom).checked is False
    geo = pg.measure_product(path, caller=_caller(camera_elevation="eye_level"))
    assert geo.checked is True, "the transient failure was cached over the real answer"


def test_a_good_measurement_is_cached(tmp_path, monkeypatch):
    import poster.product_geometry as pg
    monkeypatch.setenv("POSTER_GEOMETRY_CACHE", str(tmp_path / "cache"))
    path = _png(tmp_path)
    calls = {"n": 0}

    def _counting(system, user, model, group_name=None, images=None):
        calls["n"] += 1
        return model(camera_elevation="eye_level"), {}

    pg.measure_product(path, caller=_counting)
    pg.measure_product(path, caller=_counting)
    assert calls["n"] == 1, "the same packshot was paid for twice"


# =====================================================================
# confirming the surface the scene actually rendered
# =====================================================================

def _scene_with_table(tmp_path, table_at=0.72):
    """A frame with one strong horizontal edge — a table lip against a wall."""
    from PIL import Image
    W, H = 600, 800
    im = Image.new("RGB", (W, H), (210, 205, 200))
    for y in range(int(H * table_at), H):
        for x in range(W):
            im.putpixel((x, y), (90, 70, 55))
    p = tmp_path / "scene.png"
    im.save(p)
    return str(p)


def test_a_standing_product_is_moved_onto_the_measured_table(tmp_path):
    from poster.placement import confirm_product_zone
    from poster.product_placement import MODE_STANDING, PlacementDecision
    d = PlacementDecision(mode=MODE_STANDING, zone=(0.35, 0.30, 0.28), confident=True)
    zone = confirm_product_zone(_scene_with_table(tmp_path, 0.72), d,
                                aspect=0.8, canvas_aspect=0.75)
    base = zone[1] + zone[2]
    assert abs(base - 0.72) < 0.02, (
        f"the product's base is at {base:.2f} but the table the model rendered is at 0.72")


def test_a_flat_background_leaves_the_decision_alone(tmp_path):
    """Not finding a surface is not the same as there being none — and it is certainly not a
    licence to move the product somewhere unverified."""
    from PIL import Image

    from poster.placement import confirm_product_zone
    from poster.product_placement import MODE_STANDING, PlacementDecision
    p = tmp_path / "flat.png"
    Image.new("RGB", (600, 800), (170, 170, 170)).save(p)
    d = PlacementDecision(mode=MODE_STANDING, zone=(0.35, 0.30, 0.28), confident=True)
    assert confirm_product_zone(str(p), d) == d.zone


def test_a_flat_lay_is_not_stood_on_a_table(tmp_path):
    """Only a standing product needs a floor; nudging a flat-lay onto a table lip would move it
    for a reason that does not apply to it."""
    from poster.placement import confirm_product_zone
    from poster.product_placement import MODE_FLAT_LAY, PlacementDecision
    d = PlacementDecision(mode=MODE_FLAT_LAY, zone=(0.35, 0.30, 0.28), confident=True)
    assert confirm_product_zone(_scene_with_table(tmp_path), d) == d.zone


def test_an_unreadable_background_never_raises(tmp_path):
    from poster.placement import confirm_product_zone
    from poster.product_placement import MODE_STANDING, PlacementDecision
    d = PlacementDecision(mode=MODE_STANDING, zone=(0.35, 0.30, 0.28), confident=True)
    assert confirm_product_zone(str(tmp_path / "missing.png"), d) == d.zone
