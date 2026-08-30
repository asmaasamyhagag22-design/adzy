# -*- coding: utf-8 -*-
"""AXIS 3 — a failed clip is only bought twice when the second one can be different.

OWNER'S DIRECTIVE (2026-08-02, `info/STATE.md`): «إلغاء الإعادة العمياء… فالإعادة المتبقية لازم
تتقيّد بأسباب تقنية بس».

The money for the first clip is spent before this decision is reached, so the only question is
whether spending AGAIN changes the outcome. Three classes where it provably cannot:

  * NOBODY LOOKED — `checked=False` is the JUDGE failing, not the clip. A second render does not
    make us able to see it, and the compositor ships the moving clip either way.
  * NOTHING NEW TO ASK — MEASURED 2026-08-02 on the Al Fouad reel: three shots failed on
    `no_junk_generated_text`, three retried on the IDENTICAL prompt, three failed the same way.
    Six purchases, $12.80, one outcome.
  * TASTE — `ad_grade` is a judgement. Re-rolling the same prompt against an aesthetic verdict is
    a coin flip at full price; `reel/film_critic.py` + `reel/revision.py` change the PLAN instead.

What survives is what the directive asks for: technical defects the generator was never told not
to make. A warped face is genuinely stochastic and a second take really can come back clean.
"""
from __future__ import annotations

import inspect

import pytest

from reel.compositor import _escalate
from reel.retry_policy import failed_axes, should_retry
from reel.scene_qa import SceneQAVerdict


def _v(**kw):
    kw.setdefault("checked", True)
    kw.setdefault("overall_pass", False)
    return SceneQAVerdict(**kw)


# =====================================================================
# what is NOT worth re-buying
# =====================================================================

def test_an_unjudged_clip_is_not_re_rendered():
    """A NOT_CHECKED verdict means nobody could look. Buying a second unjudged clip spends $2.40
    to arrive at the same place."""
    v = SceneQAVerdict(overall_pass=None, checked=False, reason="scene QA call failed")
    retry, why = should_retry(v, escalation=_escalate(v.reason))
    assert retry is False
    assert "judge never ran" in why


def test_a_taste_verdict_is_not_re_rendered():
    v = _v(ad_grade=False, reason="not something a professional brand would run")
    retry, why = should_retry(v, escalation=_escalate(v.reason))
    assert retry is False
    assert "not good enough" in why


def test_a_verdict_that_names_no_defect_is_not_re_rendered():
    v = _v(reason="I do not like it")
    assert should_retry(v, escalation="")[0] is False


def test_a_ban_the_generator_broke_is_not_re_rendered_without_a_changed_prompt():
    """The measured case, generalised: `_NO_TEXT` is already in every prompt. Repeating a ban is
    not a request, so a ban violation with no escalation available is not worth re-buying."""
    v = _v(no_junk_generated_text=False, reason="the shot is unusable")   # no lettering words
    assert _escalate(v.reason) == "", "this reason must have no escalation for the test to mean it"
    retry, why = should_retry(v, escalation=_escalate(v.reason))
    assert retry is False, why
    assert "nothing new to ask" in why


def test_a_third_party_mark_gets_its_own_escalation_and_is_re_rendered():
    """BRAND SAFETY, not cosmetics (P-23). A verdict about a mark uses none of the lettering
    vocabulary, so without a clause of its own the policy would correctly decline the retry — and
    the clip would SHIP with a competitor's logo in it. It gets a clause."""
    for reason in ("a competitor logo is visible on the shelf",
                   "the actor wears a branded garment with a visible trademark",
                   "a third-party brand mark appears on the cup"):
        clause = _escalate(reason)
        assert clause, f"no escalation for: {reason}"
        assert "Remove every logo" in clause
        retry, why = should_retry(_v(no_third_party_marks=False, reason=reason),
                                  escalation=clause)
        assert retry is True, why


def test_a_product_complaint_on_a_shot_that_bans_the_product_is_not_re_rendered():
    """Axis 1 removed the product from every generated shot. A gate still asking 'where is it?'
    is asking the question the brief answered — and each answer cost a full render."""
    v = _v(product_persists=False, reason="the advertised product is completely absent")
    retry, why = should_retry(v, escalation=_escalate(v.reason), scene_has_product=False)
    assert retry is False
    assert "forbidden to show" in why


# =====================================================================
# what IS worth re-buying
# =====================================================================

@pytest.mark.parametrize("field,reason", [
    ("faces_intact_across_motion", "faces morph across the motion"),
    ("no_morphing_artifacts", "the hand loses structure between frames"),
    ("action_plausible", "the actor presses a sealed pump"),
    ("setting_faithful", "the room is not the one in the seed"),
])
def test_a_technical_defect_still_buys_a_second_take(field, reason):
    """Nothing was asked for and refused — the generation simply rolled badly, and a second roll
    genuinely can come back clean. This is the half the directive keeps."""
    v = _v(**{field: False}, reason=reason)
    retry, why = should_retry(v, escalation=_escalate(reason))
    assert retry is True, why
    assert field in why


def test_a_lettering_failure_still_retries_because_the_prompt_changes():
    """`_escalate` removes the surface the lettering lands on, so there IS something new to ask."""
    reason = "The brand name on the shopping bag becomes garbled text after the first frame."
    assert _escalate(reason)
    retry, why = should_retry(_v(no_junk_generated_text=False, reason=reason),
                              escalation=_escalate(reason))
    assert retry is True and "prompt can be changed" in why


def test_a_product_complaint_on_a_hero_shot_does_retry():
    """Where the product is SUPPOSED to be, losing it is a drift a second take can fix."""
    v = _v(product_persists=False, reason="the product vanishes halfway")
    assert should_retry(v, escalation="", scene_has_product=True)[0] is True


def test_a_passing_clip_is_never_re_rendered():
    assert should_retry(SceneQAVerdict(overall_pass=True, checked=True))[0] is False


# =====================================================================
# it is wired, and the decision is visible
# =====================================================================

def test_the_compositor_asks_the_policy_before_spending():
    import reel.compositor as comp
    src = inspect.getsource(comp.render_reel)
    assert "should_retry(" in src, "the compositor still retries on any non-PASS"
    at = src.find("should_retry(")
    assert src.find("provider.generate(_retry_prompt", at) > at, (
        "the retry render is not gated behind the policy")


def test_a_declined_retry_reaches_the_audit_trail():
    """A saving nobody can see reads as a gate that stopped working."""
    import reel.compositor as comp
    src = inspect.getsource(comp.render_reel)
    assert "retry_declined" in src
    assert "NOT regenerating" in src


def test_failed_axes_names_every_complaint():
    v = _v(faces_intact_across_motion=False, ad_grade=False, no_morphing_artifacts=False)
    assert set(failed_axes(v)) == {"faces_intact_across_motion", "no_morphing_artifacts",
                                   "ad_grade"}
