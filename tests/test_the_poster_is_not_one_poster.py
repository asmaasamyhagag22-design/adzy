# -*- coding: utf-8 -*-
"""The same brand must not get the same poster forever — and the button is not always a button.

OWNER (2026-08-06): «طريقة الكتابة اللي عالبوستر متبقاش ثنية نفس الطريقة» · «الزرار ده مش منطقي
ينزل نفس الشكل مرة».

MEASURED, and it was two separate causes:

  1. **THE CALL TO ACTION HAD NO WAY TO VARY AT ALL.** `poster/template.py` offers four real
     treatments — pill, block, outline, text_arrow — and its own comment explains why the fourth
     matters: *"A premium poster often HAS no button, and a system that can only draw buttons
     cannot make one."* But `_DesignSpecResponse` had no `cta_style` field, so every poster the
     LLM art-director designed fell through to `PosterDesignSpec.cta_style`'s default, `"pill"`.
     Measured over 8 runs of the LLM path: **pill x8**. The other three were reachable only from
     the no-caller fallback, which the full pipeline never takes.

  2. **THE FALLBACK VARIES ONLY IF IT IS GIVEN A SEED.** `default_design_spec` mixes a
     `variation_seed` into its hash precisely so "the SAME brand also varies between runs (owner:
     'every poster looks the same')" — measured: **1 distinct design across 8 runs without it, 6
     with it**.

After: the director CHOOSES the treatment from the brand's register, and an older model that
omits the field falls back to the per-run seed rather than to a pill. Measured over the same 8
runs: **4 distinct styles**.
"""
from __future__ import annotations

import collections
import inspect

import pytest

import poster.art_director as ad
from poster.schemas import PosterBrief, PosterDesignSpec
from poster.template import _CTA_STYLES, default_design_spec
from poster.variation import build_variation

BRIEF = PosterBrief(business_name="NTI", headline="headline here", cta_text="Learn more")


class _Resp:
    """What the design call returns. Deliberately WITHOUT `cta_style` — an older model, or one
    that skips an optional field, must still produce variety."""
    layout = "bottom_band"
    headline_treatment = "block"
    accent_word = "last"
    text_align = "left"
    scrim_strength = 0.8
    show = ["logo", "headline", "cta"]
    accent_hex = ""
    rationale = ""
    marketing_archetype = "magazine_editorial"
    text_box = None
    logo_xy = None


def _spec(resp_cls, seed):
    return ad.build_design_spec(BRIEF, caller=lambda *a, **k: (resp_cls(), None),
                                profile={}, variation=build_variation(seed=seed))


# =====================================================================
# the button
# =====================================================================

def test_the_director_may_choose_how_the_cta_is_drawn():
    """The field has to EXIST for the choice to be possible at all."""
    assert "cta_style" in ad._DesignSpecResponse.model_fields
    src = inspect.getsource(ad)
    assert "cta_style: HOW the call to action" in src, "the model is never told what it means"
    for style in _CTA_STYLES:
        assert style in src, f"{style} is not offered to the director"


def test_a_chosen_style_is_honoured():
    class R(_Resp):
        cta_style = "text_arrow"
    assert _spec(R, 1).cta_style == "text_arrow"


def test_a_model_that_omits_the_style_still_varies():
    """The measured regression: pill x8. The fallback is the per-run seed, never a constant."""
    seen = collections.Counter(_spec(_Resp, i).cta_style for i in range(8))
    assert len(seen) >= 3, f"the button barely varies: {dict(seen)}"
    assert seen["pill"] < 8


def test_an_invalid_style_falls_back_rather_than_crashing():
    class R(_Resp):
        cta_style = "neon_blinking"
    assert _spec(R, 3).cta_style in _CTA_STYLES


def test_a_poster_without_a_button_is_possible():
    """The whole point of the fourth treatment — an institute or a heritage house does not wear a
    retail button. A system that can only draw buttons cannot make one."""
    assert "text_arrow" in _CTA_STYLES
    seen = {_spec(_Resp, i).cta_style for i in range(12)}
    assert "text_arrow" in seen, "no run in twelve produced a button-less CTA"


# =====================================================================
# the layout
# =====================================================================

def test_the_same_brand_repeats_itself_without_a_seed():
    """The defect, as data — this is what 'every poster looks the same' actually is."""
    seen = {(default_design_spec(BRIEF).layout, default_design_spec(BRIEF).cta_style)
            for _ in range(8)}
    assert len(seen) == 1


def test_the_seed_makes_the_same_brand_vary():
    seen = {(default_design_spec(BRIEF, variation_seed=i).layout,
             default_design_spec(BRIEF, variation_seed=i).cta_style) for i in range(8)}
    assert len(seen) >= 4, f"only {len(seen)} distinct designs across 8 seeds"


def test_the_llm_path_passes_the_run_seed_to_its_fallback():
    """Two of the three fallbacks inside `build_design_spec` already did; the third — the one
    reached when the model answers but omits a field — is the one this closes."""
    src = inspect.getsource(ad.build_design_spec)
    assert src.count("variation_seed_int(variation)") >= 3, (
        "a fallback path still builds a design without the run's seed, so it will repeat")


def test_every_layout_and_style_is_reachable():
    """A menu nobody can order from is not a menu."""
    layouts = {default_design_spec(BRIEF, variation_seed=i).layout for i in range(60)}
    styles = {default_design_spec(BRIEF, variation_seed=i).cta_style for i in range(60)}
    from poster.template import _LAYOUTS
    assert layouts == set(_LAYOUTS)
    assert styles == set(_CTA_STYLES)


def test_the_default_is_still_a_pill_for_callers_that_set_nothing():
    """Backward compatibility: a caller that builds a spec by hand gets exactly what it used to."""
    assert PosterDesignSpec.model_fields["cta_style"].default == "pill"
