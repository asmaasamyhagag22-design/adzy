# -*- coding: utf-8 -*-
"""«شوف الكلام ليه بيطلع مكعبل» — the verbatim gate treated "nothing to check" as "checked".

MEASURED on the shipped `poster.oneshot.text_fidelity`:

    text_fidelity({}, ["آخرياع شأفب الن عدبل", "بتطيت اليففن"])   ->  rate 1.0
    text_fidelity({"headline": "", "cta": "  "}, ["كلام غريب"])   ->  rate 1.0

and the gate is `if verdict["rate"] < 1.0: continue`. So an EMPTY expectation set opens the gate
for any image at all — including one whose Arabic is scrambled glyph-soup. The docstring states
the behaviour outright ("rate=1.0 with no expected fields (nothing to verify)"), which is why it
survived: it reads as a considered default rather than as the hole it is.

THIS IS THE SESSION'S RECURRING FAILURE CLASS, in the one place it costs the most: absence of
evidence read as evidence of success. The same shape as the dashboard's green "All key fields
present!" (keyed on a field nothing writes) and as "No Competitors Found" (a spent search budget).

WHY IT MATTERS HERE. `poster/oneshot.py` asks an image model to DRAW the copy, and nano banana
cannot write Arabic — measured on Abu Auf, three attempts, `fidelity=0.0` every time. The gate is
the only thing standing between that and the client, and the fallback behind it (the classic
overlay, where code renders the type) produces correct Arabic: the same brand, same code, one run
later, scored 9/10 with `script_wellformed: true`. So when this gate leaks, the client gets
gibberish that a working path was ready to render properly.

THE FIX IS A THIRD STATE, not a stricter number. "Verified", "failed" and "could not be verified"
are three different facts, and only the first may open a gate.
"""
from __future__ import annotations

from poster.oneshot import text_fidelity


# --- nothing to verify is not a pass -------------------------------------------------------------

def test_an_empty_expectation_set_does_not_certify_an_image():
    v = text_fidelity({}, ["آخرياع شأفب الن عدبل", "بتطيت اليففن"])
    assert v["rate"] < 1.0 or v.get("verified") is False, (
        "an image full of scrambled glyphs cleared the copy-fidelity gate")


def test_expectations_that_are_all_blank_do_not_certify_either():
    v = text_fidelity({"headline": "", "cta": "   "}, ["كلام غريب"])
    assert v["rate"] < 1.0 or v.get("verified") is False


def test_the_unverifiable_case_is_named_not_just_scored():
    """A caller has to be able to tell "the copy was wrong" from "we could not check" — they call
    for different actions (retry the render vs. fix the pipeline)."""
    v = text_fidelity({}, ["anything"])
    assert "verified" in v, "no way to distinguish a failure from an unchecked run"
    assert v["verified"] is False


# --- and real verification still works exactly as before ------------------------------------------

def test_copy_that_is_actually_present_still_passes():
    expected = {"headline": "صاحبك اللي طب عليك فجأة", "cta": "اطلب تشكيلتك"}
    seen = ["صاحبك اللي طب عليك فجأة", "اطلب تشكيلتك", "Abu Auf"]
    v = text_fidelity(expected, seen)
    assert v["rate"] == 1.0
    assert v.get("verified") is True


def test_copy_the_model_mangled_still_fails():
    """The Abu Auf case: the model drew Arabic-looking shapes instead of the requested words."""
    expected = {"headline": "صاحبك اللي طب عليك فجأة"}
    v = text_fidelity(expected, ["آخرياع شأفب الن عدبل"])
    assert v["rate"] == 0.0


def test_partial_copy_is_a_partial_rate():
    expected = {"headline": "اطلب دلوقتي", "cta": "تشكيلة كاملة"}
    v = text_fidelity(expected, ["اطلب دلوقتي"])
    assert 0.0 < v["rate"] < 1.0


def test_extra_lines_are_still_reported():
    v = text_fidelity({"headline": "اطلب دلوقتي"}, ["اطلب دلوقتي", "junk line"])
    assert "junk line" in " ".join(v["extra_lines"])


# --- the gate that reads it must honour the third state -------------------------------------------

def test_the_pipeline_gate_refuses_an_unverified_render():
    import inspect
    import poster.pipeline as pp
    src = inspect.getsource(pp)
    assert "verified" in src, (
        "the pipeline still keys only on `rate`, so an unverifiable render can pass")
