"""A claim the linter flags must not reach the poster.

MEASURED 2026-08-02 on a rendered Al Fouad poster for a Uriage spot paste. `claim_risks` did its
job — it flagged «علاج» (therapeutic, `rewrite_required`) and «فوري» (absolute, `rewrite_required`)
— and its one production caller logged them, appended them to `concept.remediation`, and shipped
the copy unchanged. Under a comment that read "THE CLAIM LINTER, on what will actually ship". The
poster printed:

    «علاج يورياچ هيسياك الموضعي للحبوب. مفعول فوري من بالليل للصبح.»

A therapeutic claim and an immediacy promise, on a PHARMACY ad, both detected and both delivered.
The audit then recorded `remediated: true` — because the risk list was non-empty. "Handled" meant
"seen", which is this repo's oldest defect standing in its most expensive place.

The reviewer's constraint still governs the HOW: «عربي مكسور أسوأ من رفض». Nothing edits inside a
sentence it cannot safely edit — verb forms go through the existing softener, and a sentence still
carrying an un-rewritable claim is dropped whole.
"""
from __future__ import annotations

import inspect

from poster.copy_rules import claim_risks, enforce_claim_rules


def _blocking(text: str) -> list:
    return [r for r in claim_risks(text) if r.get("action") == "rewrite_required"]


# --- the exact copy that shipped ---------------------------------------------------------

SHIPPED = "علاج يورياچ هيسياك الموضعي للحبوب. مفعول فوري من بالليل للصبح."


def test_the_line_that_shipped_would_not_ship_now():
    out, actions = enforce_claim_rules(SHIPPED)
    assert "علاج" not in out, "a therapeutic claim is still on a pharmacy poster"
    assert "فوري" not in out, "an immediacy promise is still on a pharmacy poster"
    assert {a["kind"] for a in actions} == {"therapeutic", "absolute"}
    assert all(a["action"] == "dropped" for a in actions)


def test_nothing_the_linter_blocks_survives_enforcement():
    """The general property, not just the one line: whatever `claim_risks` calls
    `rewrite_required`, the enforced text no longer contains."""
    for text in (SHIPPED,
                 "مضمون ١٠٠% نتيجة نهائية.",
                 "الأفضل في السوق. بنقدملك عناية يومية.",
                 "guaranteed permanent results. gentle daily care."):
        out, _ = enforce_claim_rules(text)
        assert not _blocking(out), f"a blocked claim survived: {out!r}"


# --- and it does not break what it cannot fix --------------------------------------------

def test_a_safe_verb_form_is_softened_not_deleted():
    """The softener exists precisely so a rewritable claim keeps its sentence."""
    out, actions = enforce_claim_rules("بيعالج البقع الداكنة بلطف.")
    assert "بيقلل ظهور" in out and "البقع الداكنة" in out
    assert actions and actions[0]["action"] == "softened"


def test_clean_copy_is_returned_untouched():
    clean = "معجون موضعي بالطين الأخضر والزنك. بيهدي البشرة."
    out, actions = enforce_claim_rules(clean)
    assert out == clean and actions == []


def test_an_operational_promise_is_not_deleted_by_this_gate():
    """«يوصلك» is `verify_required`, not `rewrite_required` — it needs a coverage source, not a
    deletion. Dropping it here would silently remove a true, evidenced delivery promise."""
    out, actions = enforce_claim_rules("اطلبيه دلوقتي يوصلك")
    assert out == "اطلبيه دلوقتي يوصلك" and actions == []


def test_a_wholly_unsafe_line_becomes_empty_rather_than_broken_arabic():
    """Deliberate: a blank subheadline is a missing line and a poster survives that. Word-level
    surgery on Arabic morphology does not survive — which is why `claim_risks` refuses to try."""
    out, actions = enforce_claim_rules("علاج نهائي مضمون.")
    assert out == "" and actions


# --- the pipeline actually applies it ----------------------------------------------------

def test_the_pipeline_replaces_the_copy_and_does_not_merely_record_it():
    import poster.pipeline as pp
    src = inspect.getsource(pp)
    assert "enforce_claim_rules" in src, "the pipeline still only detects"
    assert "setattr(concept, _f, _after)" in src, (
        "the enforced text is computed and then not written back — the exact shape of the bug"
    )


def test_the_proof_chips_are_linted_too():
    """Three of the six copy surfaces on that poster were proof chips, and the linter never looked
    at them at all."""
    import poster.pipeline as pp
    assert "proof_points" in inspect.getsource(pp).split("[claims]")[0][-4000:] or \
        "_chips" in inspect.getsource(pp)
