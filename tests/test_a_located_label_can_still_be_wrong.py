"""A reprojected product label that is still garbled must fall back to the paste.

MEASURED 2026-08-02 on an Al Fouad poster for the Uriage Hyséac paste. The hybrid lane draws the
pack and reprojects the brand's real artwork onto it by homography. It covered URIAGE, Hyséac and
PÂTE SOS — and the line under the brand name shipped as «SAV TAERMALE» instead of «EAU THERMALE».
On a registered pharmaceutical label that is not a typo; it names a different product.

The lane already degrades to the measured cut-and-paste when the drawn label cannot be CORNERED,
under a comment that states the principle exactly: "a drawn label nobody corrected is a garbled
label". It had no answer for the other half — a label that WAS cornered, WAS corrected, and came
out wrong anyway. LOCATED was read as CORRECT, which is this repo's recurring failure shape.

The second half of the same defect: the string gate only read the real label when the asset was
PASTED. On the hybrid lane it was handed an empty list, so it could not know what the box says —
in the one lane where the lettering is at risk.
"""
from __future__ import annotations

import inspect

import poster.pipeline as pp

SRC = inspect.getsource(pp)


def test_a_garbled_label_degrades_the_hybrid_lane():
    i = SRC.index('garbled_text')
    window = SRC[i:i + 1800]
    assert "_hybrid_degraded = True" in window, (
        "a malformed reprojected label still leaves the next attempt on the hybrid lane"
    )


def test_it_degrades_only_once_and_only_on_the_hybrid_lane():
    i = SRC.index('garbled_text')
    window = SRC[i:i + 1800]
    assert "_use_hybrid and not _hybrid_degraded" in window, (
        "the degrade must be guarded — repeating it on the paste lane would be a no-op that reads "
        "like a fix"
    )


def test_the_unlocatable_case_still_degrades():
    """The original half must survive: a label that cannot be cornered still falls back."""
    assert SRC.count("_hybrid_degraded = True") == 2


def test_the_real_label_is_read_on_both_lanes():
    assert "if _primary_product_img else []" in SRC
    assert "_composite_after and _primary_product_img" not in SRC, (
        "the string gate is blind to the real label on exactly the lane that draws it"
    )


def test_the_paste_lane_is_still_reachable_by_configuration():
    """`POSTER_PRODUCT_MODE=composite` forces the measured paste. Keeping that escape hatch is what
    makes the degrade safe: there is somewhere to fall back TO."""
    assert "POSTER_PRODUCT_MODE" in SRC
    assert '_product_mode == "hybrid"' in SRC
