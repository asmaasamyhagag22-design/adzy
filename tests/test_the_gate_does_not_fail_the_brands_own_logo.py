# -*- coding: utf-8 -*-
"""A poster must not be rejected for the brand mark the pipeline itself pasted onto it.

MEASURED 2026-08-06 on the NTI poster, end to end:

    poster/pipeline.py:1059   composites NTI's REAL logo onto the render
    poster/pipeline.py:1090   runs the vision QA + this gate on that same image
    verdict                   "text nobody authorized was rendered:
                               National Telecommunication Institute"

That string is the sub-line printed inside NTI's own logo file. The poster shipped `QA pass=False`
and paid for a regeneration that could never satisfy the complaint, because the next render
composites the same logo again.

WHY THE OBVIOUS FIX DOES NOT WORK. Authorising `brief.logo_text` would not have helped: it is
`"NTI"` — the wordmark alone — and the sub-line is not in it. The words are only knowable from the
pixels we pasted.

WHICH IS EXACTLY THE ARGUMENT THE PRODUCT EXEMPTION ALREADY MAKES. `assets_composited` skips Latin
lines because "the Latin on the page comes from those files". A logo is such a file. But the flag
was derived from `_composite_after`, i.e. `bool(product_image)` — so a brand with no product at all
(an institute, a clinic, a service, most of this product's market) could never earn it and failed
forever on its own name.

The gate keeps doing its real job: invented copy in the BRAND'S OWN SCRIPT is still rejected, which
is where fabricated claims actually appear.
"""
from __future__ import annotations

import inspect

import pytest

from poster.ocr_gate import stray_text_verdict

APPROVED = ["زمان كنت بتسافرله. دلوقتي هو اللي جنبك.", "شوف أقرب مركز ليك"]
LOGO_SUBLINE = "National Telecommunication Institute"       # printed inside NTI's logo file
INVENTED_AR = "أفضل معهد تدريب في الشرق الأوسط"              # a claim nobody approved


def _v(seen, **kw):
    return stray_text_verdict(seen_lines=seen, approved=APPROVED, brand_name="NTI",
                              product_name="", **kw)


# =====================================================================
# the measured failure
# =====================================================================

def test_the_brands_own_logo_text_used_to_fail_the_poster():
    """Pinned as data: without the logo counted, NTI's own name is 'unauthorized text'."""
    v = _v(APPROVED + [LOGO_SUBLINE], assets_composited=False, logo_composited=False)
    assert v.passed is False and v.stray == [LOGO_SUBLINE]


def test_a_composited_logo_earns_the_same_exemption_as_a_composited_product():
    v = _v(APPROVED + [LOGO_SUBLINE], assets_composited=False, logo_composited=True)
    assert v.passed is True, v.stray


def test_a_brand_with_no_product_is_the_case_this_is_for():
    """`assets_composited` is `bool(product_image)`. An institute, a clinic or an agency has no
    packshot, so before this the exemption was unreachable for them."""
    assert _v(APPROVED + [LOGO_SUBLINE], assets_composited=False, logo_composited=True).passed


# =====================================================================
# the gate still does its job
# =====================================================================

@pytest.mark.parametrize("logo,product", [(True, False), (False, True), (True, True),
                                          (False, False)])
def test_invented_copy_in_the_brands_script_is_still_rejected(logo, product):
    """The exemption is about LATIN from pasted files. A fabricated Arabic claim is the thing this
    gate exists to catch, and no combination of composited assets may wave it through."""
    v = _v(APPROVED + [INVENTED_AR], assets_composited=product, logo_composited=logo)
    assert v.passed is False
    assert INVENTED_AR in v.stray


def test_a_brand_with_no_logo_and_no_product_is_unchanged():
    """Byte-identical behaviour for every caller that composites nothing."""
    v = _v(APPROVED + ["FIC. TRUSTWORSHY"], assets_composited=False, logo_composited=False)
    assert v.passed is False and v.stray == ["FIC. TRUSTWORSHY"]


def test_the_old_product_exemption_still_works_on_its_own():
    v = _v(APPROVED + ["LABORATOIRE DERMATOLOGIQUE"], assets_composited=True,
           logo_composited=False)
    assert v.passed is True


def test_the_default_is_off_so_no_caller_changes_by_accident():
    assert inspect.signature(stray_text_verdict).parameters["logo_composited"].default is False


# =====================================================================
# wired
# =====================================================================

def test_the_pipeline_tells_the_gate_the_logo_was_pasted():
    """Scoped to THIS call. `logo_composited` is also a field on the vision-QA call a few hundred
    lines up — a bare `find()` lands on that one and would pass while the gate learned nothing."""
    import poster.pipeline as pp
    src = inspect.getsource(pp)
    at = src.find("stray_text_verdict(")
    assert at > 0, "the gate is no longer called"
    call = src[at:src.find(")\n", src.find("product_label_lines=", at))]
    assert "logo_composited=" in call, "the gate is never told the logo was pasted"
    assert "logo_url" in call.split("logo_composited=")[1][:80], (
        "the flag is not derived from whether the brand actually has a logo")


def test_the_verdict_still_reads_could_not_verify_on_an_empty_render():
    v = _v([], logo_composited=True)
    assert v.checked is False and "no text could be read" in v.reason
