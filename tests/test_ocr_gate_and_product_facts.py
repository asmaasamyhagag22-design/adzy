# -*- coding: utf-8 -*-
"""The last three rules from the reviewer's critique (2026-07-26).

  1. «gate يرفض أي output فيه نص إنجليزي متولّد على منتج… OCR على الـ output → لو فيه نص مش
     موجود في الـ approved copy list → reject» — the product is composited now, so the source of
     invented labels is closed; the GATE that catches any remaining stray text was still missing.
  2. «مفيش أي إشارة لـ SPF 50+ ولا لتقنية UVMUNE… أهم selling point في المنتج ضايع» — the copy
     writer was handed the product's NAME and nothing else, so it could only sell the pharmacy.
  3. «مفيش سعر، مفيش offer، مفيش لينك… الـ conversion path مقطوع» — the catalog now carries the
     real price (EGP 1,100 for this item); it never reached the copy.

Hermetic: pure functions, no model, no network.
"""
from __future__ import annotations


# =====================================================================
# 1) stray rendered text is rejected, not admired
# =====================================================================

def test_text_the_brief_never_asked_for_is_rejected():
    from poster.ocr_gate import stray_text_verdict
    v = stray_text_verdict(
        seen_lines=["احمي بشرتك", "LABORATONE DERHATOLOGIQUE", "ANTFLICHES"],
        approved=["احمي بشرتك", "اطلبيه أونلاين"], brand_name="AlFouad Pharmacies")
    assert v.checked is True and v.passed is False
    assert any("LABORATONE" in s for s in v.stray), v.stray


def test_the_brands_own_name_is_not_stray():
    """The composited logo carries the wordmark — reading it back is not a defect."""
    from poster.ocr_gate import stray_text_verdict
    v = stray_text_verdict(seen_lines=["AlFouad Pharmacies", "احمي بشرتك"],
                           approved=["احمي بشرتك"], brand_name="AlFouad Pharmacies")
    assert v.passed is True, v.stray


def test_the_composited_products_own_label_is_not_stray():
    """The REAL product is pasted in, so its real label appears — and is correct by
    construction. Rejecting it would reject the fix."""
    from poster.ocr_gate import stray_text_verdict
    v = stray_text_verdict(
        seen_lines=["LA ROCHE-POSAY", "ANTHELIOS", "احمي بشرتك"],
        approved=["احمي بشرتك"], brand_name="AlFouad",
        product_name="La Roche-Posay Anthelios Anti-Dark Spot Fluid SPF50+")
    assert v.passed is True, v.stray


def test_nothing_read_is_could_not_verify():
    from poster.ocr_gate import stray_text_verdict
    v = stray_text_verdict(seen_lines=[], approved=["x"], brand_name="B")
    assert v.checked is False and v.passed is None


def test_the_pipeline_runs_the_stray_text_gate():
    import inspect

    import poster.pipeline as pp
    src = "\n".join(l.split("#")[0] for l in inspect.getsource(pp).splitlines())
    assert "stray_text_verdict" in src


# =====================================================================
# 2+3) the product's own facts reach the copywriter
# =====================================================================

def test_the_product_facts_block_carries_specs_and_price():
    from poster.concept import product_facts_block
    b = product_facts_block(name="La Roche-Posay Anthelios Anti-Dark Spot Fluid SPF50+ 50ml",
                            description="UVMUNE 400 filters; corrects and prevents dark spots.",
                            price="1100", currency="EGP")
    assert "SPF50" in b and "UVMUNE" in b
    assert "1100" in b and "EGP" in b
    low = b.lower()
    assert "verbatim" in low or "exactly" in low or "as shown" in low, b


def test_no_product_means_no_block():
    from poster.concept import product_facts_block
    assert product_facts_block(name="", description="", price="", currency="") == ""


def test_a_price_is_never_invented_when_the_page_did_not_show_one():
    from poster.concept import product_facts_block
    b = product_facts_block(name="Some Cream 50ml", description="", price="", currency="")
    assert "price" not in b.lower() or "no price" in b.lower(), b


def test_the_concept_accepts_the_facts():
    import inspect

    from poster.concept import build_creative_concept
    assert "product_facts" in inspect.signature(build_creative_concept).parameters


def test_the_pipeline_passes_the_products_facts():
    import inspect

    import poster.pipeline as pp
    src = "\n".join(l.split("#")[0] for l in inspect.getsource(pp).splitlines())
    assert "product_facts" in src, "the copywriter still only knows the product's name"


def test_the_critic_actually_reports_what_it_read():
    """The gate above is only alive if the verdict carries the lines. A field that is always
    empty is a dead gate that looks alive — the exact failure class this repo keeps paying for."""
    import inspect

    from poster.vision_qa import PosterQAVerdict, poster_vision_qa
    assert "text_lines" in PosterQAVerdict.model_fields
    src = inspect.getsource(poster_vision_qa)
    assert "text_lines" in src and "VERBATIM" in src, "the critic is never asked to transcribe"


def test_the_verdict_carries_the_lines_the_model_returned():
    from poster.vision_qa import poster_vision_qa

    class _R:
        has_latin_text = False; cta_clipped = False; on_brand_color = True
        candid_violation = False; overall_pass = True; reason = "clean"
        logo_ok = True; single_focal = True; strong_typography = True
        cta_prominent = True; script_wellformed = True; invented_product = False
        third_party_mark = False; placement_logical = True; score = 9
        text_lines = ["احمي بشرتك", "LABORATONE DERHATOLOGIQUE"]

    def caller(system, user, schema, **kw):
        return _R(), {}

    import io

    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (8, 8), (0, 0, 0)).save(buf, format="PNG")
    v = poster_vision_qa(buf.getvalue(), caller=caller, arabic=True)
    assert v.text_lines == ["احمي بشرتك", "LABORATONE DERHATOLOGIQUE"], v.text_lines
