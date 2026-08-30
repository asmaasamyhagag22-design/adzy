# -*- coding: utf-8 -*-
"""Five scenes in five places is five fragments, not an ad.

OWNER (2026-08-01), on a finished reel: «فين السيناريو فين الماركت؟ فيه حاجة مش لوجيك تماماً».

The illogic was in the brief. A commercial is ONE moment photographed from several angles — wide,
medium, close, insert — and the cuts read as an EDIT because every shot is the same world seen
differently. This director was told the opposite, in its own words: "EVERY scene is a DIFFERENT
setting, angle and moment". That line came from a REAL owner note in July («الخلفية مالهاش تفضل
ورايا الريل كله») whose actual subject was a FROZEN COMPOSITION, not a consistent location — and
answering it with "change the setting" is how a coherence complaint became a coherence defect.

So this is not an added rule; it is a CONTRADICTION removed. The prompt was asking for one
continuous scenario and a new location every scene at the same time, and the model resolved that
disagreement whichever way it liked. The tests below pin both halves: the coverage rule is present
AND the instruction that fought it cannot come back.
"""
from __future__ import annotations

import pytest

from reel.creative_director import _MOTION_GUIDANCE, _system_prompt

_MODES = sorted(_MOTION_GUIDANCE)


# =====================================================================
# one situation, covered
# =====================================================================

@pytest.mark.parametrize("mode", _MODES)
def test_every_vertical_is_told_to_cover_one_situation(mode):
    """A brief that only a pharmacy sees is a brief the next brand ships without."""
    p = _system_prompt(4, "ar", mode)
    assert "SHOOT ONE SITUATION, COVERED" in p


def test_the_four_shot_roles_are_named():
    """"Cover it" without the shot list is taste; the list is a brief."""
    p = _system_prompt(4, "ar", "service")
    for role in ("ESTABLISHING", "MEDIUM", "CLOSE", "INSERT"):
        assert role in p, role


def test_the_place_the_person_and_the_wardrobe_must_hold():
    """These three are what a viewer reads as "the same scene"; a model told only "be coherent"
    changes all three and stays coherent with itself."""
    p = _system_prompt(4, "ar", "service")
    for phrase in ("THE SAME PLACE", "THE SAME PERSON", "THE SAME WARDROBE"):
        assert phrase in p, phrase


def test_the_camera_line_demands_a_shot_size_that_changes():
    """The coverage is carried in the veo_prompt or it is not carried at all — Veo never reads
    this brief, only the per-scene prompt the director writes from it."""
    p = _system_prompt(4, "ar", "service")
    assert "SHOT SIZE FIRST" in p
    assert "DIFFERENT from the scene before it" in p


# =====================================================================
# the instruction that caused it cannot come back
# =====================================================================

@pytest.mark.parametrize("mode", _MODES)
def test_the_brief_never_asks_for_a_different_setting_per_scene(mode):
    """The exact regression. Two rules in one prompt, pulling opposite ways, and the older one
    was the more specific."""
    p = _system_prompt(4, "ar", mode)
    assert "DIFFERENT setting" not in p
    assert "implied location each scene" not in p


def test_the_self_check_agrees_with_the_coverage_rule():
    """The SELF-CHECK is the last thing the model reads before it answers, so a stale line there
    outranks everything above it."""
    p = _system_prompt(4, "ar", "service")
    at = p.find("SELF-CHECK")
    assert at > 0
    tail = p[at:]
    assert "DIFFERENT SHOT of the ONE situation" in tail
    assert "PLACE, PERSON and WARDROBE stay the same" in tail


def test_the_owners_original_complaint_is_still_answered():
    """The July note was real: one locked frame behind the person for the whole reel. Dropping it
    to fix August would just trade one owner's rejection for another's."""
    p = _system_prompt(4, "ar", "service")
    assert "SHOT VARIETY" in p
    assert "background must NOT stay behind me" in p
    assert "never hold one locked composition" in p


def test_the_arc_plays_out_inside_the_situation():
    """A five-beat arc is the honest reason a director reaches for five locations; it needs to be
    told where to put the beats instead."""
    p = _system_prompt(4, "ar", "service")
    assert "INSIDE that one situation" in p
    assert "not a change of address" in p
