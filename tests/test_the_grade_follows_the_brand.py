# -*- coding: utf-8 -*-
"""The colour grade is the brand's own look, not a house style stamped on everyone.

BRIEF (owner, 2026-07-27, rule 3 refactored): no static universal warm Kodak LUT. Derive the grade
from `brand/look.py` — cool and crisp for clinical/scientific brands, warm and golden for organic
lifestyle ones — and keep the 35mm film border OPTIONAL, off by default, so the 9:16 frame stays
available for the product and the captions.

WHY THIS SHAPE. `brand/look.py` already resolves ONE look per brand into four families (clinical /
luxury / vibrant / warm) and its docstring states the reason: "a clinic is cool and clinical, a
snack brand is warm and golden — because stamping one house style on every brand" is what it exists
to prevent. A global warm grade would override exactly that, and would push a deep-blue clinical
brand like Nano Care golden. So the grade keys off the SAME resolver: one source of truth for the
look, two consumers (the prompt, and now the pixels).

MEASURED BEFORE THIS EXISTED: `grep -rn "lut|curves|colorbalance|eq=" reel/` found no grading of
any kind — clips were muxed at whatever each generation returned, which is why a reel assembled
from several generations has no visual consistency at all.
"""
from __future__ import annotations

import pytest

# The shape `brand.look._dna_blob` actually reads off a profile: `tone_of_voice` (a string or a
# {"value": ...}). My first fixture put the words under `visual_identity.mood` — a field the
# resolver never opens — so the blob came back empty and every brand resolved to the default.
# The fixture was wrong, not the resolver.
CLINICAL = {"tone_of_voice": "clean clinical pharmaceutical laboratory"}
ORGANIC = {"tone_of_voice": {"value": "warm homely family kitchen"}}


def test_a_clinical_brand_is_graded_cool_not_golden():
    from reel.grade import grade_for_brand
    g = grade_for_brand(profile=CLINICAL)
    assert g["family"] == "clinical"
    assert g["temperature"] < 0, f"a clinical brand was warmed: {g}"


def test_an_organic_brand_is_graded_warm():
    from reel.grade import grade_for_brand
    g = grade_for_brand(profile=ORGANIC)
    assert g["temperature"] > 0, f"a warm brand was cooled: {g}"


def test_the_grade_keys_off_the_same_resolver_as_the_prompt():
    """One look per brand. If these two ever disagree, a film is shot one way and graded another."""
    # The look TEXT is prose ("cool clean colour palette..."), so matching the family name against
    # it proves nothing. The property that matters is that both sides land on the same FAMILY.
    from brand.look import DEFAULT_LOOK, _LOOKS, look_lock
    from reel.grade import grade_for_brand
    for prof in (CLINICAL, ORGANIC):
        look = look_lock(profile=prof)
        expected = next((k for k, v in _LOOKS.items() if v == look),
                        "warm" if look == DEFAULT_LOOK else None)
        assert grade_for_brand(profile=prof)["family"] == expected, (
            f"the grade and the look-lock disagree for {prof}")


def test_the_grade_is_stable_for_one_brand():
    """Recomputing per scene is how one film ends up in two grades."""
    from reel.grade import grade_for_brand
    assert grade_for_brand(profile=CLINICAL) == grade_for_brand(profile=CLINICAL)


def test_skin_tones_are_protected_in_every_family():
    """Cooling a whole frame turns faces grey. The brief is explicit: preserve natural skin."""
    from reel.grade import grade_filter
    for prof in (CLINICAL, ORGANIC):
        f = grade_filter(profile=prof)
        assert "saturation" in f, f
        # a cool grade must not desaturate below neutral, or skin goes lifeless
        assert "saturation=0." not in f or "saturation=0.9" in f or "saturation=1" in f, f


def test_the_filter_is_real_ffmpeg_and_carries_contrast_and_grain():
    from reel.grade import grade_filter
    f = grade_filter(profile=CLINICAL)
    assert "eq=" in f, "no contrast/colour stage"
    assert "noise=" in f, "no film grain — the brief asks for cinematic depth"


def test_no_universal_kodak_lut_anywhere():
    """The thing the refactor forbids must not creep back in."""
    import pathlib
    root = pathlib.Path(__file__).resolve().parents[1]
    src = (root / "reel" / "grade.py").read_text(encoding="utf-8").lower()
    code = "\n".join(l.split("#")[0] for l in src.splitlines())
    assert "kodak" not in code, "a named film stock is a house style, which is what rule 3 forbids"


# ---------------------------------------------------------------------
# the film frame is opt-in
# ---------------------------------------------------------------------

def test_the_film_frame_is_off_by_default():
    from reel.grade import use_film_frame
    assert use_film_frame() is False


@pytest.mark.parametrize("flag", [True, False])
def test_the_film_frame_is_explicitly_controllable(flag):
    from reel.grade import use_film_frame
    assert use_film_frame(flag) is flag


def test_the_cli_exposes_the_flag():
    import pathlib
    root = pathlib.Path(__file__).resolve().parents[1]
    src = (root / "reel" / "__main__.py").read_text(encoding="utf-8")
    assert "--film-frame" in src, "the brief asks for a CLI flag and there is none"
