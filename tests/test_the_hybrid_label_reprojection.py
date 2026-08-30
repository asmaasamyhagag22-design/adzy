# -*- coding: utf-8 -*-
"""The model's light and geometry, the brand's own lettering — the agency hybrid.

OWNER'S PRODUCTION RULE (2026-07-28): «بنخلي الـ AI يولد المشهد والعلبة عشان ناخُد منه الإضاءة،
الظلال، والزاوية الـ 3D التحفة. في المونتاج بنجيب التصميم الأصلي عالي الجودة للعلبة ونعمله إسقاط
فوق العلبة اللي الـ AI رسمها».

Both previous positions were measured and both were fatal alone:

  * generation alone — the A/B in `test_the_product_label_is_not_banned.py` got the box right and
    still mis-set one Arabic letter («جديد» for «حديد»), which on a registered supplement is a
    trademark distortion.
  * pasting alone — three consecutive Nano Care runs: "the product boxes are floating
    illogically", then "a flat graphic floating illogically over the scene, not placed within it".

The hybrid keeps the model's pixels for everything a paste cannot manufacture and replaces only
the artwork. Verified on real assets before this file was written: SIFT matched the real packshot
against the drawn box at 33/97 inliers (34%) on the label-protected render, and REFUSED the
label-banned one at 25% — the arm where the model had invented the packaging. That ordering is
the evidence the technique keys on structure the model preserved, not on wishful thinking.
"""
from __future__ import annotations

import io

from PIL import Image, ImageDraw


def _artwork(w=300, h=400):
    """Four coloured corners, so a misordered or folded warp is unmistakable."""
    art = Image.new("RGB", (w, h), (250, 250, 250))
    d = ImageDraw.Draw(art)
    d.rectangle([0, 0, w // 5, h // 6], fill=(255, 0, 0))               # TL red
    d.rectangle([w - w // 5, 0, w, h // 6], fill=(0, 255, 0))           # TR green
    d.rectangle([w - w // 5, h - h // 6, w, h], fill=(0, 0, 255))       # BR blue
    d.rectangle([0, h - h // 6, w // 5, h], fill=(255, 255, 0))         # BL yellow
    return art


def _png_bytes(im):
    buf = io.BytesIO()
    im.save(buf, format="PNG")
    return buf.getvalue()


# =====================================================================
# the warp itself
# =====================================================================

def test_the_artwork_lands_corner_for_corner_on_a_tilted_face(tmp_path):
    from poster.label_reprojection import LabelQuad, reproject_label
    W, H = 600, 800
    scene = Image.new("RGB", (W, H), (60, 70, 90))
    quad_px = [(150, 300), (430, 250), (470, 620), (180, 660)]
    ImageDraw.Draw(scene).polygon(quad_px, fill=(200, 190, 180))
    sp = tmp_path / "scene.png"
    scene.save(sp)
    ap = tmp_path / "art.png"
    _artwork().save(ap)

    quad = LabelQuad(found=True, corners=[(x / W, y / H) for x, y in quad_px])
    out = reproject_label(str(sp), str(ap), quad, shading=False, feather=False,
                          out_path=str(tmp_path / "o.png"))
    assert out
    res = Image.open(out).convert("RGB")
    cx = sum(p[0] for p in quad_px) / 4.0
    cy = sum(p[1] for p in quad_px) / 4.0
    expect = [(255, 0, 0), (0, 255, 0), (0, 0, 255), (255, 255, 0)]
    for (px, py), want in zip(quad_px, expect):
        sx, sy = int(px * 0.85 + cx * 0.15), int(py * 0.85 + cy * 0.15)
        got = res.getpixel((sx, sy))
        assert got == want, f"corner ({px},{py}) got {got}, expected {want} — the warp is folded"


def test_the_artwork_does_not_tile_across_the_poster(tmp_path):
    """The transform paints the artwork's whole plane; without the polygon mask the label would
    cover the entire canvas."""
    from poster.label_reprojection import LabelQuad, reproject_label
    W, H = 400, 400
    scene = Image.new("RGB", (W, H), (10, 10, 10))
    sp = tmp_path / "s.png"
    scene.save(sp)
    ap = tmp_path / "a.png"
    _artwork().save(ap)
    quad = LabelQuad(found=True, corners=[(0.3, 0.3), (0.6, 0.3), (0.6, 0.7), (0.3, 0.7)])
    out = reproject_label(str(sp), str(ap), quad, shading=False, out_path=str(tmp_path / "o.png"))
    res = Image.open(out).convert("RGB")
    assert res.getpixel((20, 20)) == (10, 10, 10), "the artwork escaped the label face"
    assert res.getpixel((W - 20, H - 20)) == (10, 10, 10)


def test_the_solver_is_exact_on_an_identity_mapping():
    from poster.label_reprojection import perspective_coeffs
    sq = [(0.0, 0.0), (100.0, 0.0), (100.0, 200.0), (0.0, 200.0)]
    c = perspective_coeffs(sq, sq)
    assert c is not None
    for got, want in zip(c, [1, 0, 0, 0, 1, 0, 0, 0]):
        assert abs(got - want) < 1e-6, c


def test_a_degenerate_correspondence_is_refused():
    from poster.label_reprojection import perspective_coeffs
    flat = [(0.0, 0.0), (10.0, 0.0), (20.0, 0.0), (30.0, 0.0)]      # all collinear
    assert perspective_coeffs(flat, flat) is None


# =====================================================================
# refusing to guess
# =====================================================================

def test_a_folded_quad_is_rejected():
    from poster.label_reprojection import LabelQuad, validate_quad
    bowtie = LabelQuad(found=True, corners=[(0.2, 0.2), (0.6, 0.6), (0.6, 0.2), (0.2, 0.6)])
    ok, why = validate_quad(bowtie)
    assert ok is False and "convex" in why


def test_an_offcanvas_quad_is_rejected():
    from poster.label_reprojection import LabelQuad, validate_quad
    ok, why = validate_quad(LabelQuad(found=True, corners=[(0.2, 0.2), (1.6, 0.2),
                                                          (1.6, 0.6), (0.2, 0.6)]))
    assert ok is False and "outside" in why


def test_a_sliver_is_rejected():
    """An edge-on face carries no legible artwork; warping onto it only smears."""
    from poster.label_reprojection import LabelQuad, validate_quad
    ok, why = validate_quad(LabelQuad(found=True, corners=[(0.20, 0.20), (0.99, 0.20),
                                                           (0.99, 0.24), (0.20, 0.24)]))
    assert ok is False, why


def test_a_tiny_face_is_rejected():
    from poster.label_reprojection import LabelQuad, validate_quad
    ok, why = validate_quad(LabelQuad(found=True, corners=[(0.5, 0.5), (0.52, 0.5),
                                                           (0.52, 0.52), (0.5, 0.52)]))
    assert ok is False and "small" in why


def test_no_caller_and_no_match_means_the_scene_is_returned_unchanged(tmp_path):
    """The third state. Half-applying a label is worse than not applying one."""
    from poster.label_reprojection import apply_real_label
    sp = tmp_path / "s.png"
    Image.new("RGB", (300, 300), (128, 128, 128)).save(sp)
    ap = tmp_path / "a.png"
    Image.new("RGB", (50, 50), (200, 30, 40)).save(ap)
    out, note = apply_real_label(str(sp), str(ap), caller=None)
    assert out == str(sp)
    assert note


def test_a_thin_match_is_refused():
    """The inlier floors are the point: a homography from noise is still a homography."""
    from poster.label_reprojection import MIN_INLIERS, MIN_INLIER_RATIO, LabelMatch
    assert MIN_INLIERS >= 8 and MIN_INLIER_RATIO >= 0.2
    m = LabelMatch(matched=False, inliers=5, matches=100)
    assert m.matched is False and m.inlier_ratio < MIN_INLIER_RATIO


def test_unrelated_images_do_not_match():
    """Noise against noise must not produce a located product."""
    from poster.label_reprojection import match_label
    import random
    random.seed(7)
    a = Image.new("RGB", (300, 300))
    b = Image.new("RGB", (300, 300))
    a.putdata([(random.randrange(256), random.randrange(256), random.randrange(256))
               for _ in range(300 * 300)])
    b.putdata([(random.randrange(256), random.randrange(256), random.randrange(256))
               for _ in range(300 * 300)])
    m, t = match_label(a, b)
    assert m.matched is False, f"noise matched noise: {m.reason}"
    assert t is None


# =====================================================================
# the light comes from the scene, the letters from the file
# =====================================================================

def test_the_shading_field_carries_the_scenes_light_not_its_ink():
    """A field that still holds the drawn label's own contrast bleaches the real artwork — the
    first version of this did exactly that, and the patch was visible across the box."""
    from poster.label_reprojection import _LIGHT_BLUR, _LIGHT_SWING, _shading_field
    assert _LIGHT_BLUR >= 0.15, "too tight a blur leaves ink in the lighting map"
    assert _LIGHT_SWING <= 0.35, "too wide a swing lets a highlight erase the artwork"
    scene = Image.new("RGB", (400, 400), (40, 40, 40))
    for x in range(200, 400):                       # a lit half
        for y in range(400):
            scene.putpixel((x, y), (220, 220, 220))
    field = _shading_field(scene.convert("RGBA"), [(100, 100), (300, 100), (300, 300), (100, 300)])
    assert field is not None
    assert field.getpixel((280, 200)) > field.getpixel((120, 200)), (
        "the field does not brighten on the lit side")


def test_shading_is_optional_and_never_required(tmp_path):
    from poster.label_reprojection import LabelQuad, reproject_label
    sp = tmp_path / "s.png"
    Image.new("RGB", (400, 400), (90, 90, 90)).save(sp)
    ap = tmp_path / "a.png"
    _artwork().save(ap)
    quad = LabelQuad(found=True, corners=[(0.3, 0.3), (0.7, 0.3), (0.7, 0.7), (0.3, 0.7)])
    for shading in (True, False):
        assert reproject_label(str(sp), str(ap), quad, shading=shading,
                               out_path=str(tmp_path / f"o{shading}.png"))


# =====================================================================
# the pipeline actually runs it (written is not wired)
# =====================================================================

def test_the_hybrid_prompt_asks_for_the_product_instead_of_a_hole():
    from poster.pipeline import _product_scene_guard
    hybrid = _product_scene_guard("نانوكير", composited=False, hybrid=True).lower()
    assert "render the product itself" in hybrid
    assert "leave its place empty" not in hybrid
    paste = _product_scene_guard("نانوكير", composited=True).lower()
    assert "do not draw the product itself" in paste


def test_the_hybrid_lets_the_products_own_mark_be_drawn():
    """The blanket 'never letter this brand anywhere' ban contradicts 'render this package', and
    a model resolving that contradiction draws a blank box."""
    import inspect

    import poster.pipeline as P
    src = inspect.getsource(P._generate_nano_background)
    assert "if _bn and not hybrid" in src, (
        "the brand-mark ban still applies while the model is being asked to render the packaging")


def test_the_render_loop_reprojects_before_it_judges():
    """QA must see what ships. Judging the plate before the label is corrected would reject a
    poster for a defect the next line removes."""
    import inspect

    import poster.pipeline as P
    src = "\n".join(l.split("#")[0] for l in inspect.getsource(P).splitlines())
    reproject = src.find("apply_real_label(")
    # the CALL, not the def — `src.find("_excuse_expected_brand(")` lands on the definition,
    # which sits earlier in the file and inverts the comparison.
    qa = src.find("verdict = _excuse_expected_brand(")
    assert reproject > 0, "the hybrid never calls the reprojection"
    assert qa > 0, "the classic QA block has moved"
    assert reproject < qa, "the art critic judges the frame before the real label is applied"


def test_a_failed_reprojection_degrades_to_the_paste():
    """One strike, then the measured path — not three attempts of the same miss."""
    import inspect

    import poster.pipeline as P
    src = inspect.getsource(P)
    assert "_hybrid_degraded = True" in src
    at = src.find("_hybrid_degraded = False")
    loop = src.find("for attempt in range(max_qa_retries", at)
    assert 0 < at < loop, "the degrade flag resets every attempt, so it can never take effect"


def test_the_mode_can_be_forced_back_to_the_paste():
    """The owner's own summary keeps cut-and-paste as the trusted lane; it must stay reachable."""
    import inspect

    import poster.pipeline as P
    assert "POSTER_PRODUCT_MODE" in inspect.getsource(P)


def test_the_hybrid_does_not_ban_the_text_it_just_asked_for():
    """MEASURED live (2026-07-28): the background prompt's blanket "render NO text of any kind"
    sat directly above "render the product's printed packaging". The model cannot obey both, and
    what it actually did was render text BOTH on the box and across the scene — "RAPETICS" and
    «الحجاز الحصان الحصبيان» on a poster that asked for neither, which the stray-text gate then
    correctly reported. A contradiction is not a strict rule; it is an unspecified one."""
    import inspect

    import poster.pipeline as P
    src = inspect.getsource(P._generate_nano_background)
    at = src.find("CRITICAL: produce")
    assert at > 0
    block = src[at:at + 1600]
    assert "if hybrid else" in block, (
        "the same absolute no-text rule is sent whether or not the model was asked to render "
        "the product's own label")
    hybrid_half = block[:block.find("if hybrid else")]
    assert "ONLY text" in hybrid_half and "product's own packaging" in hybrid_half
    assert "signage" in hybrid_half, "the exemption must stay one surface wide"


def test_the_paste_lane_keeps_the_absolute_ban():
    """Nothing about the hybrid may loosen the lane that never draws a product."""
    import inspect

    import poster.pipeline as P
    src = inspect.getsource(P._generate_nano_background)
    at = src.find("if hybrid else")
    tail = src[at:at + 500]
    assert "Render NO text, letters, words, numbers, logos, captions or UI of any " in tail


def test_a_local_packshot_actually_reaches_the_image_call(tmp_path):
    """MEASURED live: `_image_bytes_from_url` handled only remote URLs, so a local packshot was
    silently dropped and the model — asked to render "the attached product" with nothing attached
    — invented a blue carton with an atom on it. Everything else in this pipeline takes a path or
    a URL; this one function did not, and it is the one that decides what the model SEES."""
    from poster.pipeline import _image_bytes_from_url
    p = tmp_path / "packshot.png"
    Image.new("RGB", (40, 40), (200, 30, 40)).save(p)
    data, mime = _image_bytes_from_url(str(p))
    assert data, "a local packshot still never reaches the image model"
    assert mime == "image/png"


def test_a_missing_local_path_is_still_nothing():
    from poster.pipeline import _image_bytes_from_url
    data, _m = _image_bytes_from_url("C:/nope/does-not-exist.jpg")
    assert data is None


def test_the_evidence_free_fallback_does_not_run_by_default(tmp_path, monkeypatch):
    """A route that cannot tell a hit from a miss must not be the automatic second try."""
    monkeypatch.delenv("POSTER_LABEL_VISION_FALLBACK", raising=False)
    from poster.label_reprojection import apply_real_label
    sp = tmp_path / "s.png"
    Image.new("RGB", (300, 300), (128, 128, 128)).save(sp)
    ap = tmp_path / "a.png"
    Image.new("RGB", (60, 60), (200, 30, 40)).save(ap)

    called = {"n": 0}

    def _caller(*a, **kw):
        called["n"] += 1
        raise AssertionError("the vision fallback ran without being asked for")

    out, note = apply_real_label(str(sp), str(ap), caller=_caller)
    assert out == str(sp) and called["n"] == 0, note


# =====================================================================
# a homography that enough points agree on can still be impossible
# =====================================================================

def test_a_vanishing_line_through_the_artwork_is_refused():
    """MEASURED live (2026-07-28): 23/73 inliers — over both floors — produced a transform whose
    vanishing line fell inside the frame, and the artwork exploded into a radial smear across the
    whole poster. Counting agreeing points says nothing about whether they agree on something
    physically possible."""
    import numpy as np

    from poster.label_reprojection import _homography_is_sane
    # w = h20*x + h21*y + 1 passes through zero inside a 400x400 artwork
    H = np.array([[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [-1.0 / 200.0, 0.0, 1.0]])
    ok, why = _homography_is_sane(H, 400, 400, 800, 800)
    assert ok is False, why
    assert "vanishing" in why or "folds" in why


def test_an_identity_homography_is_sane():
    import numpy as np

    from poster.label_reprojection import _homography_is_sane
    ok, why = _homography_is_sane(np.eye(3), 300, 400, 800, 800)
    assert ok is True, why


def test_an_absurd_scale_is_refused():
    import numpy as np

    from poster.label_reprojection import _homography_is_sane
    ok, why = _homography_is_sane(np.diag([400.0, 400.0, 1.0]), 300, 400, 800, 800)
    assert ok is False and ("area" in why or "larger" in why), why


def test_a_non_finite_transform_is_refused():
    import numpy as np

    from poster.label_reprojection import _homography_is_sane
    bad = np.eye(3)
    bad[0, 0] = float("inf")
    ok, why = _homography_is_sane(bad, 300, 400, 800, 800)
    assert ok is False and "finite" in why


def test_the_matcher_runs_both_gates_before_it_claims_a_match():
    """Written is not wired, for the third time this session."""
    import inspect

    import poster.label_reprojection as L
    src = inspect.getsource(L.match_label)
    h_gate = src.find("_homography_is_sane(")
    q_gate = src.find("validate_quad(_projected)")
    claim = src.find("m.matched = True")
    assert 0 < h_gate < claim, "the homography is never sanity-checked before it is trusted"
    assert 0 < q_gate < claim, "the projected face is never validated before it is trusted"


def test_no_pro_model_is_pinned_while_it_404s():
    """A correction of this file's own earlier claim, kept because the reasoning matters.

    The hybrid was briefly pinned to `gemini-3-pro-image-preview` on the argument that only the
    pro model reproduces an attached packshot faithfully. PROBED DIRECTLY on 2026-07-28: both
    `gemini-3-pro-image-preview` and `gemini-3-pro-image` return 404 on this project, and the
    A/B whose 33/97 match had been credited to pro had silently failed over to flash. Same model
    on both sides — so the real driver of that match was the product's SIZE in frame, not the
    model, and the pin bought a wasted round-trip per attempt.

    `resolve_oneshot_model()` still selects pro the moment IMAGE_LANE says so and Google
    re-opens the id; nothing here forecloses that."""
    import inspect

    import poster.pipeline as P
    code = "\n".join(l.split("#")[0] for l in
                     inspect.getsource(P._generate_nano_background).splitlines())
    assert "gemini-3-pro-image" not in code, (
        "a model id that 404s on this project is pinned in code, so every attempt pays a wasted "
        "round-trip before the chain serves flash anyway")


def test_the_owners_image_lane_is_finally_honoured():
    """`resolve_oneshot_model()` implements «خليك برو»; this function used to hardcode past it,
    so setting IMAGE_LANE changed nothing for the path that renders most posters."""
    import inspect

    import poster.pipeline as P
    src = inspect.getsource(P._generate_nano_background)
    assert "resolve_oneshot_model()" in src
    assert "oneshot._MODEL_CHAIN[0]" not in src


def test_the_pro_pin_still_falls_over_if_the_id_retires():
    """A pinned model that 404s must not kill a client's render."""
    import poster.oneshot as O
    assert len(O._MODEL_CHAIN) >= 2


def test_the_hybrid_sizes_the_product_as_a_hero():
    """Two independent reasons agreeing. MEASURED: a tight product shot matched at 33/97
    correspondences; production scenes sizing the carton at 20% of the canvas found 10-23 and the
    geometry gates refused nearly all of them — SIFT has nothing to key on in two hundred pixels
    of carton. And the owner's reason for the hybrid says the same from the other side: «العميل
    ممكن يكبر الصورة عشان يقرا المكونات». A label nobody can read never needed correcting."""
    from poster.product_geometry import ProductGeometry
    from poster.product_placement import HYBRID_MIN_HEIGHT_FRAC, decide_placement
    geo = ProductGeometry(camera_elevation="high_angle", form_factor="sachet",
                          real_scale_hint="palm", aspect=0.8, checked=True)
    plain = decide_placement(geo, text_box=[0.06, 0.07, 0.88])
    hero = decide_placement(geo, text_box=[0.06, 0.07, 0.88], hybrid=True)
    assert hero.height_frac >= HYBRID_MIN_HEIGHT_FRAC
    assert hero.height_frac > plain.height_frac
    assert hero.height_frac <= 0.34, "the hero floor must not defeat the bench-sized ceiling"


def test_the_hero_floor_is_wired_into_the_render_loop():
    import inspect

    import poster.pipeline as P
    assert "hybrid=_hybrid_lane" in inspect.getsource(P), (
        "the placement decision never learns it is feeding the hybrid")


def test_a_silent_model_failover_is_disclosed():
    """MEASURED 2026-07-28: both pro image ids 404 on this project, the chain served flash, and
    nothing said so — which cost a wrong conclusion when an A/B credited to pro had in fact run
    on flash. The ledger recorded the truth; nobody was told."""
    import inspect

    import poster.oneshot as O
    src = inspect.getsource(O.generate_oneshot_poster)
    assert "cand_model != tried[0]" in src, "the failover is still silent"
