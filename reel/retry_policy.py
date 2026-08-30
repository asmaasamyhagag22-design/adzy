# -*- coding: utf-8 -*-
"""AXIS 3 — WHEN A FAILED CLIP IS WORTH BUYING TWICE.

OWNER'S DIRECTIVE (2026-08-02, `info/STATE.md`): «إلغاء الإعادة العمياء… فالإعادة المتبقية لازم
تتقيّد بأسباب تقنية بس».

The money for the first clip is already spent. The only question a retry answers is whether
spending AGAIN can change the outcome — and for three whole classes of failure it provably cannot:

  1. **NOBODY LOOKED.** A `checked=False` verdict (no caller, unreadable frames, a call timeout)
     is a failure of the JUDGE, not of the clip. A second render does not make us able to see it,
     and `compositor.render_reel` ships the moving clip either way — so the retry buys a different
     unjudged clip for $2.40 and changes nothing.

  2. **NOTHING NEW TO ASK.** MEASURED 2026-08-02 on the Al Fouad reel: all three generated shots
     failed on `no_junk_generated_text`, all three retried on the IDENTICAL prompt, all three
     failed the same way — six purchases, $12.80, one outcome. `_NO_TEXT` already forbids readable
     text and the generator wrote it anyway; repeating a ban is not a request. A ban-violation is
     SYSTEMATIC, so it is only worth re-buying when the prompt can be CHANGED (see
     `compositor._escalate`, which removes the surface the lettering lands on).

  3. **IT IS A MATTER OF TASTE.** `ad_grade` — "a professional brand would actually run this" — is
     a judgement, not a defect. Re-rolling the same prompt against an aesthetic verdict is a coin
     flip at full price, and the film has a critic and a revision pass (`reel/revision.py`) whose
     whole job is to change the PLAN when the ad is weak.

What survives is what the directive asks for: the TECHNICAL defects, where the generator violated
no instruction and simply rolled badly. A warped face or a morphing limb is genuinely stochastic —
the same prompt really can come back clean — so those still buy a second take.

This module decides only; the compositor spends.
"""
from __future__ import annotations

from typing import Any

# Axes a re-roll can genuinely change: nothing was ASKED FOR and refused, the generation just came
# back wrong. Ordered as they read in a verdict.
_STOCHASTIC_AXES = (
    "faces_intact_across_motion",   # a warped or melted face
    "no_morphing_artifacts",        # limbs/objects losing structure across frames
    "action_plausible",             # a physically impossible action
    "setting_faithful",             # the world drifted away from its seed
)

# Axes where the model broke an EXPLICIT ban. Repeating the ban is not a request — these are worth
# re-buying only when the prompt can say something different.
_BAN_VIOLATION_AXES = (
    "no_junk_generated_text",
    "no_third_party_marks",
)

# A judgement, not a defect. The critic + revision pass own this, not the render loop.
_TASTE_AXES = ("ad_grade",)

# Questions the brief may have already answered. Under Axis 1 a generated shot carries NO product —
# the film's product beats are composited inserts — so failing one of these is the gate asking for
# something the prompt forbids. `compositor` already stops passing the product hint for such
# scenes; this is the second door.
_PRODUCT_AXES = ("product_faithful", "product_persists")


def failed_axes(verdict: Any) -> list[str]:
    """Every axis the judge marked False, in a stable order."""
    axes = _STOCHASTIC_AXES + _BAN_VIOLATION_AXES + _TASTE_AXES + _PRODUCT_AXES
    return [a for a in axes if getattr(verdict, a, True) is False]


def should_retry(verdict: Any, *, escalation: str = "", scene_has_product: bool = False) -> tuple:
    """`(retry, why)` — whether this failed clip is worth buying a second time.

    `escalation` is the extra clause the retry would add (`compositor._escalate`); an empty string
    means the retry would re-send the identical prompt. `scene_has_product` says the shot is
    supposed to contain the product, so a product verdict on it is a real defect rather than the
    gate asking for something the brief forbade.

    The default is NOT to spend. A reason is always returned so the log says which rule decided —
    the difference between a decision someone can argue with and a silent charge.
    """
    if getattr(verdict, "overall_pass", None) is True:
        return False, "the clip passed"
    if not getattr(verdict, "checked", False):
        return False, ("the judge never ran, so this is our blindness and not the clip's defect — "
                       "a second render cannot fix it")

    failed = failed_axes(verdict)
    if not failed:
        # Failed overall with no axis named — the judge disliked it without saying where. That is
        # the taste case wearing no label.
        return False, "the verdict names no defect to fix"

    # ON A SHOT THAT IS SUPPOSED TO CARRY THE PRODUCT, a product complaint is STOCHASTIC: the model
    # was asked to hold something and drifted off it, which is precisely what a second take can fix.
    # On a shot the brief forbids the product from, the same complaint is the gate asking a question
    # the brief already answered — and it is what cost $2.40 a time before Axis 1 (`compositor.py`
    # now also stops passing the product hint for those scenes).
    stochastic = set(_STOCHASTIC_AXES) | (set(_PRODUCT_AXES) if scene_has_product else set())
    if scene_has_product:
        actionable = [a for a in failed if a not in _TASTE_AXES]
    else:
        actionable = [a for a in failed if a not in _TASTE_AXES and a not in _PRODUCT_AXES]
        if set(failed) & set(_PRODUCT_AXES) and not actionable:
            return False, ("the only complaint is about a product this shot is forbidden to show — "
                           "the gate is asking the question the brief already answered")

    if not actionable:
        return False, ("the only complaint is that the shot is not good enough, which the film "
                       "critic and the revision pass exist to answer — not another render")

    if set(actionable) & stochastic:
        return True, f"a re-roll can plausibly fix {', '.join(sorted(set(actionable)))}"

    # Everything left is a ban the generator broke. Worth re-buying only with a changed prompt.
    if str(escalation or "").strip():
        return True, f"the prompt can be changed for {', '.join(sorted(set(actionable)))}"
    return False, (f"{', '.join(sorted(set(actionable)))} is a ban the generator already broke, "
                   "and there is nothing new to ask — the identical prompt measured the identical "
                   "failure three times")
