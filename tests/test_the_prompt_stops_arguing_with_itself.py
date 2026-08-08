# -*- coding: utf-8 -*-
"""What reaches Veo is the SHOT, not a stack of bans that contradict it.

MEASURED 2026-08-06 on a real scene prompt, by reconstructing exactly what the provider receives:

    the director's SCENE      144 chars   21%
    motion directive + look   228         33%
    the no-text ban           169         24%
    the motion contract       156         22%
    ------------------------------------------
    TOTAL to Veo              697

Three defects in that one string, all confirmed against Google's own Veo 3.1 guide:

  1. THE BAN WAS STATED TWICE — once by `creative._motion_directive` and once by
     `compositor._NO_TEXT`. Two statements of one rule do not double it; they take the space the
     shot needed.

  2. THE BAN WAS PURE NEGATION. Google's guide: "describe what you wish to exclude" affirmatively —
     their own example is "a desolate landscape with no buildings or roads" over "no man-made
     structures". Ours was five stacked negations. This is the most expensive measured failure in
     the project: three Al Fouad shots all failed `no_junk_generated_text`, all retried, all failed
     the same way, $12.80 for one outcome.

  3. THE MOTION CONTRACT OVERRODE THE DIRECTOR. `apply_motion_contract` appended "handheld tracking
     shot, dynamic camera motion, subtle camera shake" unconditionally — so a prompt asking for a
     LOCKED-OFF MACRO on a label ended with a demand for a shaking tracking camera. Google's guide
     states Veo "interprets structure literally" and weights what comes first, so a contradicting
     tail does not cancel out; it competes.

The still-frame ban — the whole reason `motion_contract.py` exists — is carried by the NEGATIVE
prompt, which rides every request either way. Nothing was traded away to fix this.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from reel.creative import _motion_directive
from reel.motion_contract import (
    MOTION_NEGATIVE, MOTION_POSITIVE, MOTION_POSITIVE_SUBJECT_ONLY,
    apply_motion_contract, names_a_camera,
)

_LOOK = "soft daylight, warm neutral grade, shallow depth of field"


def _to_veo(director_wrote: str, *, hero=False) -> str:
    """Rebuild the string the provider actually receives, the way the compositor builds it."""
    from reel.motion_contract import FRAME_IS_UNPRINTED
    sb = director_wrote.rstrip() + "\n" + _motion_directive(
        SimpleNamespace(hero_product=hero), look=_LOOK)
    return apply_motion_contract(sb + FRAME_IS_UNPRINTED)


# =====================================================================
# 1) one rule, stated once
# =====================================================================

def test_the_text_ban_is_stated_once():
    prompt = _to_veo("Medium shot, hand-held. A woman takes a paper bag from a pharmacist.")
    low = prompt.lower()
    said = low.count("no readable text") + low.count("blank and unprinted")
    assert said == 1, f"the text ban appears {said} times in one prompt"


def test_the_motion_directive_no_longer_carries_a_text_ban():
    """It belongs to the compositor, which puts it on every prompt including retries and
    fallbacks. Repeating it here only shortened the shot."""
    d = _motion_directive(SimpleNamespace(hero_product=False), look="")
    assert "no readable text" not in d.lower()


# =====================================================================
# 2) the ban is a description, not a list of negations
# =====================================================================

def test_the_ban_describes_the_frame_instead_of_listing_negations():
    """Google's guidance, applied: say what IS there. A count of bare 'no X' clauses is the
    measure — the phrasing the vendor calls weakest."""
    import inspect

    import reel.compositor as comp
    from reel.motion_contract import FRAME_IS_UNPRINTED
    clause = FRAME_IS_UNPRINTED.lower()
    negations = clause.count("no ") + clause.count("never")
    assert negations <= 2, f"the ban is still {negations} stacked negations"
    assert "blank" in clause or "plain" in clause or "unprinted" in clause, \
        "the ban never says what the frame DOES look like"
    # and it is the one the compositor actually sends
    assert "_NO_TEXT = FRAME_IS_UNPRINTED" in inspect.getsource(comp.render_reel)


# =====================================================================
# 3) the contract never overrides the director's camera
# =====================================================================

@pytest.mark.parametrize("wrote", [
    "Macro close-up, locked-off. The pharmacist's hands slide a small white box across a counter.",
    "Wide establishing shot, slow push in. A bright pharmacy at golden hour.",
    "Medium shot, hand-held. A woman reaches across the counter.",
])
def test_a_named_camera_is_not_overruled(wrote):
    assert names_a_camera(wrote)
    out = apply_motion_contract(wrote)
    assert MOTION_POSITIVE_SUBJECT_ONLY in out, "the subject-motion clause is missing"
    assert "handheld tracking shot" not in out.replace(wrote, ""), \
        "a generic camera instruction was stapled onto a prompt that already chose one"


def test_a_locked_off_macro_is_not_told_to_shake():
    wrote = "Macro close-up, locked-off. A white box, still, on a lit counter."
    out = apply_motion_contract(wrote)
    for word in ("subtle camera shake", "dynamic camera motion", "tracking shot"):
        assert word not in out.replace(wrote, ""), f"'{word}' contradicts a locked-off macro"


def test_a_prompt_with_no_camera_still_gets_the_full_contract():
    """Where the director named nothing, the contract is the only thing asking for a moving
    camera — it must still arrive in full."""
    wrote = "A woman walks into a bright pharmacy and looks around."
    assert names_a_camera(wrote) is False
    assert MOTION_POSITIVE in apply_motion_contract(wrote)


def test_the_still_frame_ban_never_left():
    """The module exists to forbid the slideshow. That job is the NEGATIVE prompt's, and it rides
    every request regardless of which positive clause was chosen — so narrowing the positive
    clause costs nothing."""
    for w in ("static camera", "locked-off frame", "slideshow", "ken burns zoom", "freeze frame"):
        assert w in MOTION_NEGATIVE


def test_applying_the_contract_twice_is_still_a_no_op():
    for wrote in ("Macro close-up, locked-off. A white box on a counter.",
                  "A woman walks into a pharmacy."):
        once = apply_motion_contract(wrote)
        assert apply_motion_contract(once) == once


# =====================================================================
# the shot gets more of the prompt than it used to
# =====================================================================

def test_the_shot_is_a_larger_share_of_what_veo_reads():
    """It was 21%. The exact number will move as the look lock changes; what must not come back is
    the prompt being mostly boilerplate."""
    wrote = ("Medium shot, hand-held. A woman in her 30s with curly hair reaches across a pharmacy "
             "counter and takes a paper bag from the pharmacist, smiling.")
    prompt = _to_veo(wrote)
    share = len(wrote) / len(prompt)
    assert share > 0.24, f"the shot is only {share:.0%} of the prompt again"
