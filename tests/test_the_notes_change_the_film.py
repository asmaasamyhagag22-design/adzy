# -*- coding: utf-8 -*-
"""A gate that only refuses burns a whole render to say what a sentence could have changed.

OWNER (2026-08-01), on why a hand-driven tool wins: «لما آخده لـGoogle Flow بنفس التول اللي معايا
طلع تحفة فنية». The model is the same. What differs is that a person WATCHES, forms a note,
changes the thing the note is about, and looks again.

`reel/film_critic.py` closed the watching half. This is the other half: the notes become a revised
plan, the film is rendered ONCE more, and the better of the two is kept.
"""
from __future__ import annotations

import types

from pydantic import BaseModel


class _Scene(BaseModel):
    veo_prompt: str = ""
    voiceover: str = ""


class _Plan(BaseModel):
    scenes: list = []
    concept: str = "unchanged"


def _plan(n=3):
    return _Plan(scenes=[_Scene(veo_prompt=f"scene {i}", voiceover=f"line {i}") for i in range(n)])


def _verdict(fixes, scenes_for, checked=True):
    return types.SimpleNamespace(fixes=fixes, fix_scenes=scenes_for, checked=checked)


# =====================================================================
# a note changes the scene it is about
# =====================================================================

def test_a_note_lands_on_its_own_scene_only():
    from reel.revision import revise_plan
    out = revise_plan(_plan(), _verdict(["have her take the sachet from the pharmacist"], [2]))
    assert "sachet" in out.scenes[1].veo_prompt
    assert "sachet" not in out.scenes[0].veo_prompt
    assert "sachet" not in out.scenes[2].veo_prompt


def test_a_whole_film_note_reaches_every_scene():
    """Scene 0 means the film itself — its language, its pace, its ending."""
    from reel.revision import revise_plan
    out = revise_plan(_plan(), _verdict(["speak Arabic throughout"], [0]))
    assert all("Arabic" in s.veo_prompt for s in out.scenes)


def test_a_misnumbered_note_is_kept_as_a_film_note_not_dropped():
    """A note pointing at scene 9 of a 3-scene film is still a real observation; losing it
    silently is the failure class this repo keeps finding."""
    from reel.revision import revise_plan
    out = revise_plan(_plan(), _verdict(["end on the product"], [9]))
    assert all("end on the product" in s.veo_prompt for s in out.scenes)


def test_notes_without_numbers_are_treated_as_film_notes():
    from reel.revision import revise_plan
    out = revise_plan(_plan(), _verdict(["make the ending land"], []))
    assert all("make the ending land" in s.veo_prompt for s in out.scenes)


# =====================================================================
# the revision is additive, never a rewrite
# =====================================================================

def test_the_scene_description_still_leads():
    """A model reads the opening of a prompt as the subject; a note promoted to the front
    produces a beautiful shot of the note instead of the scene."""
    from reel.revision import revise_plan
    out = revise_plan(_plan(), _verdict(["add a contact shadow"], [1]))
    assert out.scenes[0].veo_prompt.startswith("scene 0")


def test_the_rest_of_the_plan_is_untouched():
    """It passed the coherence gate — arc, motif, casting and language are already correct.
    Discarding that to act on one note trades a specific defect for an unknown one."""
    from reel.revision import revise_plan
    p = _plan()
    out = revise_plan(p, _verdict(["x"], [1]))
    assert out.concept == "unchanged"
    assert [s.voiceover for s in out.scenes] == [s.voiceover for s in p.scenes]
    assert p.scenes[0].veo_prompt == "scene 0", "the original plan was mutated"


def test_the_notes_are_marked_as_the_critics():
    """A reader of the revised plan must be able to see which lines the director wrote and which
    the critic added."""
    from reel.revision import revise_plan
    out = revise_plan(_plan(), _verdict(["do the thing"], [1]))
    assert "DIRECTOR'S NOTE" in out.scenes[0].veo_prompt


# =====================================================================
# nothing to act on means do not spend
# =====================================================================

def test_no_fixes_means_no_revision():
    """Spending a second render on no instruction is how a revision loop becomes an expensive
    no-op."""
    from reel.revision import revise_plan
    assert revise_plan(_plan(), _verdict([], [])) is None
    assert revise_plan(_plan(), _verdict(["   "], [1])) is None


def test_an_unchecked_verdict_never_triggers_a_re_render():
    """Nobody watched, so there is nothing to act on."""
    from reel.revision import revise_plan
    assert revise_plan(_plan(), _verdict(["a real note"], [1], checked=False)) is None


def test_a_plan_with_no_scenes_is_left_alone():
    from reel.revision import revise_plan
    assert revise_plan(_Plan(scenes=[]), _verdict(["x"], [1])) is None


# =====================================================================
# a revision is not automatically an improvement
# =====================================================================

def _v(hard=4, score=8, checked=True):
    axes = ("product_causes_resolution", "problem_is_attractive",
            "speaks_audience_language", "everything_belongs_to_brand")
    return types.SimpleNamespace(checked=checked, score=score,
                                 **{a: (i < hard) for i, a in enumerate(axes)})


def test_a_better_second_take_wins():
    from reel.revision import better_of
    assert better_of(_v(hard=2, score=5), _v(hard=4, score=8)) == "second"


def test_a_worse_second_take_loses():
    """The second render is a fresh roll of the same dice with one extra instruction — it can
    land the note and lose something the first one had."""
    from reel.revision import better_of
    assert better_of(_v(hard=4, score=9), _v(hard=4, score=6)) == "first"
    assert better_of(_v(hard=4, score=7), _v(hard=3, score=9)) == "first"


def test_a_tie_keeps_the_first():
    """The second cost money to discover it was no better; preferring it would make that spend
    look like progress."""
    from reel.revision import better_of
    assert better_of(_v(hard=3, score=7), _v(hard=3, score=7)) == "first"


def test_an_unwatched_second_take_never_wins():
    from reel.revision import better_of
    assert better_of(_v(hard=1, score=2), _v(checked=False)) == "first"


def test_nothing_here_calls_a_model():
    """The critic already did the judging. A second opinion here would let the two disagree."""
    import inspect

    import reel.revision as r
    src = inspect.getsource(r).lower()
    for forbidden in ("caller(", "generate_content", "gemini"):
        assert forbidden not in src, forbidden


# =====================================================================
# the critic can name the scene at all
# =====================================================================

def test_every_fix_carries_the_scene_it_belongs_to():
    from reel.film_critic import FilmVerdict, _CriticResponse
    assert "fix_scenes" in _CriticResponse.model_fields
    assert "fix_scenes" in FilmVerdict.model_fields


def test_the_critic_is_shown_the_numbered_scene_list():
    """Frames have no numbers; scenes do. Without the list the critic can only say "the ending
    does not land", which nothing can apply."""
    import inspect

    import reel.film_critic as fc
    src = inspect.getsource(fc.critique_film)
    assert "THE SCENES, in order" in src
    assert "scene {i}" in src
    assert "scenes" in inspect.signature(fc.critique_film).parameters


# =====================================================================
# the loop is closed in the ship path, and bounded
# =====================================================================

def test_the_run_revises_only_a_film_that_actually_failed():
    """A film the critic passed has nothing to act on; re-rendering it would spend a full render
    chasing a higher score."""
    import inspect

    import reel.__main__ as M
    src = inspect.getsource(M)
    at = src.find("result, _v, _revised = _revise_once(")
    assert at > 0, "the revision is never called from the ship path"
    guard = src[max(0, at - 400):at]
    assert "_v.passed is False" in guard, "a passing film can trigger a paid re-render"
    assert "_v.checked" in guard, "an unwatched film can trigger a paid re-render"


def test_the_revision_is_a_single_pass():
    """A loop that re-renders until a model is satisfied spends $1.60 a clip until something else
    stops it."""
    import inspect

    import reel.__main__ as M
    src = inspect.getsource(M._revise_once)
    assert "while" not in src, "the revision can iterate"
    assert src.count("render_creative_reel(") == 1


def test_the_revision_can_be_switched_off():
    import inspect

    import reel.__main__ as M
    assert "REEL_REVISION" in inspect.getsource(M)


def test_a_failed_revision_keeps_the_film_we_already_have():
    import inspect

    import reel.__main__ as M
    src = inspect.getsource(M._revise_once)
    at = src.find("except Exception")
    assert "keeping the first cut" in src[at:at + 300]
    assert "return result, verdict, False" in src[at:at + 400]


def test_the_second_film_is_judged_before_it_can_win():
    import inspect

    import reel.__main__ as M
    src = inspect.getsource(M._revise_once)
    assert src.find("_watch(profile, r2.reel_path") < src.find("better_of(verdict, v2)")


def test_the_ship_decision_reuses_the_verdict_it_already_paid_for():
    """Watching the same frames twice for the same answer is a second paid viewing."""
    import inspect

    import reel.__main__ as M
    src = inspect.getsource(M._film_critique)
    assert "verdict if verdict is not None" in src
