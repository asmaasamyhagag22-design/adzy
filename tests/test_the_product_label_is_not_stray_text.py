# -*- coding: utf-8 -*-
"""The real product's own printed label is not "text nobody authorized".

MEASURED on a live Nano Care render (2026-07-27). The pipeline composited the brand's real
3620x4526 packshot, and the stray-text gate then rejected the poster three times over:

    text nobody authorized was rendered: حديد + حمض الفوليك + فيتامين ب٦ + فيتامي
                                         يقلل التعب والإرهاق
                                         يدعم تكوين كرات الدم الحمراء

Every one of those strings is printed on the box in the photograph. The gate was reading the real
product's packaging as invented copy — and because a failure triggers a re-render, each poster burnt
three paid image generations before returning the same verdict.

THE CAUSE IS THE D-8 PATTERN AGAIN. The excuse already existed and was written for ONE SCRIPT:
`assets_composited` stops Latin lines being judged, added after the gate flagged "LABORATOIRE
DERMATOLOGIQUE" — a French label on a real French product. Nano Care's label is ARABIC, so the
same correct-by-construction text failed, in the language most of this system's brands actually
print in.

THE FIX IS NOT A WIDER EXCUSE. Excusing all Arabic when assets are composited would blind the gate
to invented Arabic copy, which is the only thing it exists to catch. Instead the product asset is
READ, and a line that appears on the asset is approved BECAUSE it appears on the asset — evidence,
not a guess about script or position.
"""
from __future__ import annotations


APPROVED = ["طاقتك كاملة، ومعدتك مرتاحة.", "اسألي عليه في الصيدليات"]
# what the camera saw on the real box
LABEL = ["حديد + حمض الفوليك + فيتامين ب٦", "يقلل التعب والإرهاق", "امتصاص عالي"]


def test_the_arabic_label_on_a_composited_product_is_not_stray():
    from poster.ocr_gate import stray_text_verdict
    v = stray_text_verdict(
        seen_lines=APPROVED + ["حديد + حمض الفوليك + فيتامين ب٦", "يقلل التعب والإرهاق"],
        approved=APPROVED, brand_name="Nano Care",
        product_name="نانوكير ليبوسومال حديد",
        assets_composited=True, product_label_lines=LABEL)
    assert v.checked is True
    assert v.passed is True, f"the product's own label was called stray: {v.stray}"


def test_invented_arabic_copy_is_still_caught():
    """The whole point of the gate. A wider excuse would have blinded it here."""
    from poster.ocr_gate import stray_text_verdict
    v = stray_text_verdict(
        seen_lines=APPROVED + ["أفضل مكمل غذائي في مصر بشهادة الأطباء"],
        approved=APPROVED, brand_name="Nano Care",
        product_name="نانوكير ليبوسومال حديد",
        assets_composited=True, product_label_lines=LABEL)
    assert v.passed is False, "an invented Arabic claim slipped through"
    assert any("أفضل مكمل" in s for s in v.stray)


def test_a_misread_of_the_label_is_forgiven_a_fabrication_is_not():
    """OCR drops a glyph; it does not invent a sentence."""
    from poster.ocr_gate import stray_text_verdict
    near = stray_text_verdict(
        seen_lines=APPROVED + ["حديد + حمض الفوليك + فيتامين ب"],   # one glyph short
        approved=APPROVED, assets_composited=True, product_label_lines=LABEL)
    assert near.passed is True, f"a one-glyph misread of the label failed: {near.stray}"


def test_no_label_reading_leaves_the_old_behaviour_intact():
    """When the asset could not be read, nothing is silently excused."""
    from poster.ocr_gate import stray_text_verdict
    v = stray_text_verdict(
        seen_lines=APPROVED + ["حديد + حمض الفوليك + فيتامين ب٦"],
        approved=APPROVED, assets_composited=True, product_label_lines=[])
    assert v.passed is False, "with no label evidence the gate must still refuse"


def test_the_latin_excuse_still_works():
    """The French-label case that motivated the original excuse must keep passing."""
    from poster.ocr_gate import stray_text_verdict
    v = stray_text_verdict(
        seen_lines=["LABORATOIRE DERMATOLOGIQUE", "طاقتك كاملة، ومعدتك مرتاحة."],
        approved=APPROVED, assets_composited=True)
    assert v.passed is True


def test_the_label_reader_pulls_lines_off_the_asset():
    """The evidence has to come from the asset, so there must be a way to read it."""
    from poster.ocr_gate import product_label_lines
    import inspect
    sig = inspect.signature(product_label_lines)
    assert "caller" in sig.parameters, "no vision caller to read the asset with"
    # no caller = no evidence, never an invented list
    assert product_label_lines(asset=b"", caller=None) == []
