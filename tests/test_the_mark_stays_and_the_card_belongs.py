# -*- coding: utf-8 -*-
"""Two owner rulings about how a reel wears its brand (2026-07-28).

    «عايزه لوجو البراند يبقا موجود طول الفيديو»
    «الخاتمه تبقا مميزه بالجو بردو بالالوان الطبيعيه، يعني متخليهوش ابيض لانها خلصت كتير قبل كدا»

MEASURED BEFORE CHANGING ANYTHING:

  * `textlayer._scene_html` gated the mark on `scene.kind in ("intro", "outro", "contact")`, so
    the MIDDLE of every reel — the part people actually watch, and the part that gets clipped and
    reshared — carried no brand at all.
  * `endcard._endcard_html` had exactly two grounds, a charcoal gradient and an ivory one, both
    synthetic. The ivory branch is the white ending being ruled out, and neither has ever seen a
    frame of the film it closes.
"""
from __future__ import annotations

import types


def _scene(kind="body", **kw):
    s = types.SimpleNamespace(kind=kind, headline="", subline="", cta_text="", on_screen_text="",
                              duration_s=3.0, caption_role="", caption_word_delays=None)
    for k, v in kw.items():
        setattr(s, k, v)
    return s


# =====================================================================
# the mark stays on screen
# =====================================================================

def test_the_logo_is_no_longer_gated_on_the_scene_kind():
    import inspect

    import reel.textlayer as tl
    src = inspect.getsource(tl._scene_html)
    code = "\n".join(l.split("#")[0] for l in src.splitlines())
    assert 'show_logo = bool(logo_uri)' in code
    assert 'scene.kind in ("intro", "outro", "contact")' not in code, (
        "the mark is still absent from the middle of every reel")


def test_a_body_scene_gets_the_quiet_form():
    """Presence, not prominence: a mark that pops in on every cut reads as six logos rather than
    one brand, so the body wears it smaller, softer and un-animated."""
    import inspect

    import reel.textlayer as tl
    src = inspect.getsource(tl._scene_html)
    assert "logo_bug" in src
    body = src[src.find("if logo_bug:"):src.find("else:", src.find("if logo_bug:"))]
    assert "opacity" in body and "scale(" in body
    assert "_LOGO_ANIM" not in body, "the body mark re-animates on every cut"


def test_the_bookends_keep_the_full_treatment():
    import inspect

    import reel.textlayer as tl
    src = inspect.getsource(tl._scene_html)
    tail = src[src.find("else:", src.find("if logo_bug:")):]
    assert "_LOGO_ANIM" in tail


def test_include_logo_false_still_means_no_logo():
    """The switch that turns the mark off entirely must survive making it persistent."""
    import inspect

    import reel.textlayer as tl
    for fn in (tl.render_text_sequences,):
        assert "include_logo" in inspect.signature(fn).parameters
    src = inspect.getsource(tl)
    assert "if include_logo else None" in src


# =====================================================================
# the card belongs to the film
# =====================================================================

def test_the_card_can_be_grounded_on_a_real_frame():
    import inspect

    import reel.endcard as ec
    assert "ground_uri" in inspect.signature(ec._endcard_html).parameters
    assert "ground_frame" in inspect.signature(ec.render_endcard).parameters


def test_a_grounded_card_does_not_use_either_synthetic_gradient():
    import reel.endcard as ec
    html = ec._endcard_html(None, "براند", "اعرف أكتر", "brand.test", "#8B1E2D", "#8B1E2D",
                            True, 1080, 1920, elegant=False, dark=False,
                            ground_uri="data:image/jpeg;base64,AAAA")
    assert "url('data:image/jpeg;base64,AAAA')" in html
    assert "#F3ECE2" not in html, "the ivory ending is still being produced"


def test_without_a_frame_the_card_still_renders():
    """A decoration must never cost the card itself."""
    import reel.endcard as ec
    html = ec._endcard_html(None, "brand", "learn more", "brand.test", "#8B1E2D", "#8B1E2D",
                            False, 1080, 1920, elegant=False, dark=True)
    assert "radial-gradient" in html and "background:" in html


def test_the_ground_is_blurred_past_recognition_and_darkened(tmp_path):
    """The point is the film's colour and light, not a freeze-frame the edit forgot to cut."""
    from PIL import Image, ImageStat

    from reel.endcard import _ground_data_uri
    import base64
    import io
    src = tmp_path / "f.png"
    im = Image.new("RGB", (540, 960), (240, 240, 240))
    for x in range(0, 540, 12):                      # hard vertical bars: obvious if they survive
        for y in range(960):
            im.putpixel((x, y), (10, 10, 10))
    im.save(src)

    uri = _ground_data_uri(src, 1080, 1920)
    assert uri.startswith("data:image/jpeg;base64,")
    out = Image.open(io.BytesIO(base64.b64decode(uri.split(",", 1)[1]))).convert("RGB")
    assert out.size == (1080, 1920)
    before = ImageStat.Stat(im.convert("L")).stddev[0]
    after = ImageStat.Stat(out.convert("L")).stddev[0]
    assert after < before * 0.5, f"the frame is still legible (stddev {before:.1f} -> {after:.1f})"
    assert ImageStat.Stat(out.convert("L")).mean[0] < 160, "the ground is not darkened"


def test_an_unusable_frame_yields_no_ground_and_does_not_raise(tmp_path):
    from reel.endcard import _ground_data_uri
    assert _ground_data_uri(None, 1080, 1920) == ""
    assert _ground_data_uri(tmp_path / "missing.png", 1080, 1920) == ""
    bad = tmp_path / "bad.png"
    bad.write_bytes(b"not an image")
    assert _ground_data_uri(bad, 1080, 1920) == ""


def test_the_appender_takes_the_frame_from_the_finished_reel():
    """It already holds the video; the frame one instant before the card is free to take."""
    import inspect

    import reel.endcard as ec
    src = inspect.getsource(ec.append_endcard_to_reel)
    assert "tail_frame(video_path" in src
    assert "ground_frame=ground" in src


def test_a_frame_extraction_failure_falls_back_rather_than_failing_the_card():
    import inspect

    import reel.endcard as ec
    src = inspect.getsource(ec.append_endcard_to_reel)
    at = src.find("tail_frame(video_path")
    tail = src[at:at + 260]
    assert "except Exception" in tail and "ground = None" in tail


def test_the_module_has_the_logger_its_error_paths_use():
    """`logger` was referenced twice in reel/endcard.py and never defined, so BOTH paths raised
    NameError from inside the handler — the code written to explain a lost end-card was the code
    that hid the reason for it. Found when a new keyword argument finally exercised the path."""
    import reel.endcard as ec
    assert hasattr(ec, "logger")
    import inspect
    src = inspect.getsource(ec)
    for name in re.findall(r"\b(logger|log)\.\w+\(", src):
        assert hasattr(ec, name), f"{name} is used but never defined"


import re  # noqa: E402  (used by the guard above)
