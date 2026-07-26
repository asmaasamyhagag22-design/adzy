# -*- coding: utf-8 -*-
"""The four rules left from the reviewer's pipeline critique (2026-07-26).

Three were shipped in 94e6556 (assets composited, Arabic typeset, product outranks the learned
motif). These are the rest, each pinned to the sentence that asked for it:

  1. «تشيك برمجي على WCAG contrast ratio بين النص والبكسلز اللي تحته. أقل من 4.5:1 = reject»
     — every contrast fix so far has been a judgement call about gradients. A ratio is a number.
  2. «text zones و product zone منفصلين في الـ template… الكومبوزيتور يحط النص في safe zones
     محسوبة» — «النص غطّى المنتج… تغطية المنتج بالتايبوجرافي خطيئة أساسية».
  3. «الـ Director JSON يحدد subject_descriptors مختلفة صراحة لكل شخصية» — «البنتين شكلهم شبه
     بعض جداً… المخ بيقراها كأنها نفس الشخص مرتين».
  4. «الـ CTA buttons ما بتاخدش نقطة في الآخر… الجملة طويلة زيادة… اسم الصيدلية اتكرر 3 مرات».

Hermetic: pure functions and Pillow, no model, no network.
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image


# =====================================================================
# 1) a contrast RATIO, not a judgement call
# =====================================================================

def test_the_wcag_ratio_matches_the_standard():
    from poster.contrast_gate import wcag_ratio
    assert round(wcag_ratio((255, 255, 255), (0, 0, 0)), 1) == 21.0
    assert round(wcag_ratio((255, 255, 255), (255, 255, 255)), 1) == 1.0
    # the measured Al Fouad case: white copy on a 185/255 grey
    assert wcag_ratio((255, 255, 255), (185, 185, 185)) < 2.0


def test_white_copy_on_a_bright_band_is_rejected(tmp_path):
    from poster.contrast_gate import check_copy_contrast
    png = tmp_path / "p.png"
    Image.new("RGB", (1080, 1350), (208, 208, 210)).save(png)
    v = check_copy_contrast(png, zones=[(0.06, 0.60, 0.88, 0.18)], ink="#ffffff")
    assert v.checked is True
    assert v.passed is False
    assert v.worst_ratio < 4.5
    assert v.zones and v.zones[0]["ratio"] == v.worst_ratio


def test_white_copy_on_a_dark_band_passes(tmp_path):
    from poster.contrast_gate import check_copy_contrast
    png = tmp_path / "p.png"
    Image.new("RGB", (1080, 1350), (18, 20, 26)).save(png)
    v = check_copy_contrast(png, zones=[(0.06, 0.60, 0.88, 0.18)], ink="#ffffff")
    assert v.passed is True and v.worst_ratio >= 4.5


def test_an_unreadable_file_is_could_not_verify_not_a_pass(tmp_path):
    """The house law: absence is never success."""
    from poster.contrast_gate import check_copy_contrast
    bad = tmp_path / "junk.png"
    bad.write_bytes(b"not an image")
    v = check_copy_contrast(bad, zones=[(0.1, 0.1, 0.5, 0.2)], ink="#fff")
    assert v.checked is False and v.passed is None


def test_the_pipeline_runs_the_contrast_gate():
    import inspect

    import poster.pipeline as pp
    src = "\n".join(l.split("#")[0] for l in inspect.getsource(pp).splitlines())
    assert "check_copy_contrast" in src, "the render still ships copy nobody measured"


# =====================================================================
# 2) the copy never sits on the product
# =====================================================================

def test_a_text_box_that_overlaps_the_product_is_moved_off_it():
    from poster.product_composite import DEFAULT_ZONE
    from poster.template import avoid_product_zone
    # a box straddling the product's column
    box = avoid_product_zone([0.30, 0.36, 0.60], DEFAULT_ZONE)
    x, y, w = box
    px, py, ph = DEFAULT_ZONE
    horizontally_clear = (x + w) <= px + 0.02 or x >= (px + 0.40) - 0.02
    vertically_clear = (y + 0.22) <= py + 0.02 or y >= (py + ph) - 0.02
    assert horizontally_clear or vertically_clear, box


def test_a_text_box_already_clear_of_the_product_is_left_alone():
    from poster.product_composite import DEFAULT_ZONE
    from poster.template import avoid_product_zone
    box = [0.06, 0.06, 0.40]
    assert avoid_product_zone(list(box), DEFAULT_ZONE) == box


def test_the_template_applies_the_rule_when_a_product_is_composited():
    import inspect

    import poster.template as T
    src = "\n".join(l.split("#")[0] for l in inspect.getsource(T).splitlines())
    assert "avoid_product_zone" in src


# =====================================================================
# 3) two people in a frame are two DIFFERENT people
# =====================================================================

def test_the_scene_prompt_demands_distinct_subjects():
    from poster.pipeline import _cast_variation_rule
    r = _cast_variation_rule().lower()
    assert "different" in r or "distinct" in r
    assert "age" in r and "hair" in r


def test_the_rule_is_in_the_background_prompt():
    import inspect

    import poster.pipeline as pp
    src = "\n".join(l.split("#")[0] for l in inspect.getsource(pp._generate_nano_background).splitlines())
    assert "_cast_variation_rule" in src


# =====================================================================
# 4) the CTA is a button, not a sentence
# =====================================================================

def test_a_cta_loses_its_full_stop():
    from poster.copy_rules import tidy_cta
    assert tidy_cta("اطلبيه أونلاين من صيدليات الفؤاد.", brand_name="صيدليات الفؤاد") \
        == "اطلبيه أونلاين"
    assert tidy_cta("Order now.", brand_name="Shop") == "Order now"


def test_a_long_cta_is_shortened_not_wrapped():
    from poster.copy_rules import tidy_cta
    out = tidy_cta("اطلبي الحل من ويبسايت صيدليات الفؤاد دلوقتي حالاً",
                   brand_name="صيدليات الفؤاد")
    assert len(out.split()) <= 4, out
    assert "صيدليات الفؤاد" not in out


def test_a_clean_short_cta_is_untouched():
    from poster.copy_rules import tidy_cta
    assert tidy_cta("اطلبي دلوقتي", brand_name="صيدليات الفؤاد") == "اطلبي دلوقتي"


def test_the_pipeline_tidies_the_cta():
    import inspect

    import poster.pipeline as pp
    src = "\n".join(l.split("#")[0] for l in inspect.getsource(pp).splitlines())
    assert "tidy_cta" in src
