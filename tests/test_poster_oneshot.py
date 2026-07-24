"""Hermetic tests for the ONE-SHOT poster pilot (step ب): the design-brief prompt carries the
gated copy verbatim with the exact-render + no-extra-text contract, and the FIDELITY GATE's
pure logic (Arabic-aware normalization + verbatim match + extra-text flagging). The live
Gemini-image render + OCR are measured by benchmark/measure_oneshot_poster.py (costs money)."""
from __future__ import annotations

from poster.oneshot import _norm_text, build_oneshot_prompt, text_fidelity


def test_norm_text_folds_arabic_variants_for_verbatim_compare():
    assert _norm_text("بتقرّبنا") == _norm_text("بتقربنا")          # shadda stripped
    assert _norm_text("مكالمة") == _norm_text("مكالمه")             # ة == ه (renderer variance)
    assert _norm_text("أهلاً بكم!") == _norm_text("اهلا بكم")       # alef unify + tanween + punct
    assert _norm_text("على") == _norm_text("علي")                   # ى == ي
    assert _norm_text("Stay Close.") == _norm_text("stay close")    # case + punctuation
    assert _norm_text("كلام") != _norm_text("سلام")                 # real word changes DON'T fold


def test_oneshot_prompt_carries_copy_verbatim_with_the_contract():
    copy = {"headline": "المسافات بينا كلام", "subheadline": "خدمات تقرّبك", "cta": "اعرف أكتر"}
    p = build_oneshot_prompt(copy, brand_name="WE", palette_names="deep purple, magenta",
                             dna_lines="light streaks over glowing cityscapes", rtl=True,
                             has_logo=True)
    for text in copy.values():
        assert f'"{text}"' in p                                      # verbatim, quoted
    assert "BEGIN TEXT" in p and "END TEXT" in p                     # fenced drawable zone (1.3)
    assert "EXACTLY as written" in p                                 # the exact-render demand
    assert "right-to-left" in p and "SHAPED AND CONNECTED" in p      # Arabic clause
    # The model must NOT draw the logo (it garbles Arabic script) — the reserved corner is a
    # CONTINUATION of the background with an explicit placeholder ban (FIX-C1: the old wording
    # "the real logo is placed into that space afterward" made the model paint a white slot).
    assert "CONTINUATION" in p and "Do NOT paint any placeholder" in p
    assert "no white or blank box" in p
    assert "Do NOT draw the brand's logo" in p
    assert "NO other text" in p                                      # no invented labels
    assert "deep purple" in p and "light streaks" in p               # brand grounding


def test_oneshot_prompt_makes_the_primary_color_dominate_not_just_the_cta():
    # Owner: the ITI poster came out navy when the brand is red — 'the brand color should
    # DOMINATE the composition, not just the CTA'. The FIRST palette color is the primary and
    # the mandate must force it into a LARGE color field, and forbid a generic navy default.
    p = build_oneshot_prompt({"headline": "H"}, brand_name="ITI",
                             palette_names="brick red, deep red, slate blue")
    assert "PRIMARY and must DOMINATE" in p
    assert "LARGE color field" in p
    assert "NOT enough to tint only the small CTA button" in p
    assert "generic default blue/navy" in p           # resist the model's documented navy drift
    assert "brick red" in p                            # the real first palette color reaches the model


def test_oneshot_prompt_skips_empty_fields_and_optional_clauses():
    p = build_oneshot_prompt({"headline": "Stay Close", "subheadline": "", "cta": ""})
    assert '"Stay Close"' in p and "headline" in p                   # role hint OUTSIDE the fence
    assert "subheadline" not in p and "call-to-action" not in p      # empty fields leave no trace
    assert "right-to-left" not in p and "REAL logo" not in p


def test_text_fidelity_verbatim_pass_garble_fail_extra_flagged():
    expected = {"headline": "المسافات بينا كلام", "cta": "اعرف أكتر"}
    ok = text_fidelity(expected, ["المسافات بينا كلام", "اعرف اكتر"])
    assert ok["fields"] == {"headline": True, "cta": True} and ok["rate"] == 1.0
    assert ok["extra_lines"] == []

    garbled = text_fidelity(expected, ["المسافات كلام", "اعرف أكتر"])   # dropped word -> FAIL
    assert garbled["fields"]["headline"] is False and garbled["rate"] == 0.5

    extra = text_fidelity(expected, ["المسافات بينا كلام", "اعرف أكتر", "عرض خاص %50"])
    assert extra["rate"] == 1.0 and extra["extra_lines"] == ["عرض خاص %50"]  # invented label flagged

    assert text_fidelity({}, ["anything"])["rate"] == 1.0            # nothing to verify
