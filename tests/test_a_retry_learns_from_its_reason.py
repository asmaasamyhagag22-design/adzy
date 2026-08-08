"""A retry that repeats the prompt repeats the defect — and pays for it.

MEASURED 2026-08-02 on the Al Fouad reel. All three generated shots failed scene QA on
`no_junk_generated_text` — «The brand name on the shopping bag becomes garbled text after the
first frame». All three retried with the IDENTICAL prompt. All three failed the same way. Six Veo
purchases at $2.40/$1.60 each, $12.80, one outcome — against a quote of $7.20 that assumed one
purchase per shot.

`_NO_TEXT` already forbids readable text in every prompt, and the generator wrote text anyway, so
saying it again louder is not a fix. The retry removes the SURFACE the lettering keeps landing on.
The product still reaches the film through the insert beat, which composites the brand's real
photograph and cannot garble.
"""
from __future__ import annotations

import inspect

import reel.compositor as comp
from reel.compositor import _escalate


def test_a_lettering_failure_changes_the_prompt():
    for reason in ("The brand name on the shopping bag becomes garbled text after the first frame.",
                   "the branded bag features a garbled image with junk text",
                   "The branded shopping bag contains prominent garbled, unreadable text.",
                   "the label is illegible"):
        assert _escalate(reason), f"no escalation for: {reason}"


def test_a_different_failure_does_not_change_the_prompt():
    """A morphing hand or a vanished product is not a lettering problem, and stripping every
    package from the shot would answer a question nobody asked."""
    for reason in ("the actor presses a sealed pump and it vanishes",
                   "faces morph across the motion",
                   ""):
        assert _escalate(reason) == ""


def test_the_escalation_removes_the_surface_rather_than_repeating_the_ban():
    clause = _escalate("garbled text on the bag")
    assert "Remove every branded surface" in clause
    assert "packaging" in clause and "must not appear here" in clause


def test_the_retry_actually_uses_it():
    src = inspect.getsource(comp.render_reel)
    assert "_escalate(v.reason)" in src, "the retry still re-sends the identical prompt"
    assert "_retry_prompt = scene.visual_prompt + _NO_TEXT + _escalate(v.reason)" in src


def test_the_first_attempt_is_unchanged():
    """Escalation is a RESPONSE to a measured failure. Putting it in the first prompt would strip
    the product from every reel that never had a problem.

    THIS USED TO CHECK SOURCE POSITION — that `_escalate` appeared nowhere before the string
    "regenerating once". That stopped meaning what it says on 2026-08-06, when Axis 3 («إلغاء
    الإعادة العمياء») made the escalation an INPUT to the decision of whether to re-render at all:
    a ban the generator broke is only worth re-buying when the prompt can change, so the policy has
    to know whether one exists BEFORE it answers. Computing it early is not the same as sending it
    early, and a position check cannot tell those apart — the exact "assert on the source instead
    of the output" disease `CLAUDE.md` warns about. The invariant is stated directly instead."""
    src = inspect.getsource(comp.render_reel)
    assert "provider.generate(scene.visual_prompt + _NO_TEXT, **gen_kwargs)" in src, \
        "the first attempt no longer sends the plain prompt"
    assert "_retry_prompt = scene.visual_prompt + _NO_TEXT + _escalate(v.reason)" in src, \
        "the escalation no longer rides the RETRY prompt only"
