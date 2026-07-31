# -*- coding: utf-8 -*-
"""Footage that MOVES, cut at commercial pace — the two rules that kill the slideshow feel.

BRIEF (owner, 2026-07-27), rules 1 and 2 of four:
  1. every video prompt carries handheld/tracking/continuous-motion modifiers, a high motion
     parameter, and negative exclusions for static camera / slideshow / ken-burns / frozen subject;
  2. no single shot exceeds ~1.8 s, and shots are joined by speed ramps or whip pans rather than
     laid end to end.

MEASURED BEFORE WRITING THIS. `grep -rn "negative_prompt|cfg_scale|motion_strength" reel/` returned
NOTHING: the request body was `{model_name, prompt, duration, aspect_ratio, image}` — no
negative prompt and no motion control at all, so every clip was generated at the provider's default
adherence with nothing forbidding a static frame. And `reel/compositor.py:15` states its own
approach: "Hard cuts + per-scene text fades ... rather than xfade crossfades", with no cap anywhere
on how long one shot may run. A director writing a 6-second beat got a 6-second locked-off shot,
which is the slideshow the brief is about.

The motion contract lives in ONE place so a second provider cannot quietly ship without it: adding
Runway or Luma later must inherit the same modifiers and the same exclusions.
"""
from __future__ import annotations


# ---------------------------------------------------------------------
# RULE 1 — the motion contract
# ---------------------------------------------------------------------

def test_the_motion_contract_names_movement_and_forbids_stillness():
    from reel.motion_contract import MOTION_NEGATIVE, MOTION_POSITIVE
    pos = MOTION_POSITIVE.lower()
    for word in ("handheld", "tracking", "continuous", "dynamic camera", "fluid"):
        assert word in pos, f"the positive contract never says {word!r}"
    neg = MOTION_NEGATIVE.lower()
    for word in ("static camera", "tripod", "slideshow", "still photo", "ken burns", "frozen"):
        assert word in neg, f"the negative contract never excludes {word!r}"


def test_every_prompt_gets_the_modifiers_once():
    """Applied twice, a modifier list starts fighting the scene description."""
    from reel.motion_contract import apply_motion_contract
    once = apply_motion_contract("A pharmacist hands a box across the counter.")
    twice = apply_motion_contract(once)
    assert once.lower().count("handheld") == 1
    assert twice == once, "the contract was appended a second time"


def test_the_scene_description_still_leads():
    from reel.motion_contract import apply_motion_contract
    out = apply_motion_contract("A pharmacist hands a box across the counter.")
    assert out.startswith("A pharmacist hands a box"), (
        "the modifiers must trail the scene, not replace it")


def test_the_veo_request_carries_the_negative_prompt_and_the_motion_clause():
    """The contract is worthless if it never reaches the wire — and for Veo it never did.

    FOUND 2026-07-28: `apply_motion_contract` and MOTION_NEGATIVE were applied in exactly one
    place, inside the provider that has since been removed. Veo — the fallback then, the only provider now — received
    neither, so every Veo clip was generated with nothing forbidding a locked-off camera. This
    asserts on the request object Veo is actually handed, not on the constants, because the
    constants were always fine; the wiring was not."""
    from google.genai import types

    from reel.video_provider import _videos_config
    cfg = _videos_config(types, 6)
    neg = str(getattr(cfg, "negative_prompt", "") or "").lower()
    assert "slideshow" in neg, "no negative prompt reaches Veo"
    assert "static camera" in neg

    from reel.motion_contract import apply_motion_contract
    assert "handheld" in apply_motion_contract("a pharmacist reaching for a shelf").lower()

    import inspect

    import reel.video_provider as vp
    src = inspect.getsource(vp.VeoProvider.generate)
    assert "apply_motion_contract" in src, (
        "the positive clause is never applied to the prompt Veo is sent")
    assert "prompt=moving_prompt" in src, (
        "the contract is computed and then the RAW prompt is submitted anyway")


def test_no_adherence_scalar_is_advertised_that_nothing_reads():
    """`MOTION_CFG_SCALE` was the old provider's `cfg_scale`; Veo has no equivalent field. A constant nothing
    reads is worse than no constant — it reads as a control that exists."""
    import reel.motion_contract as mc
    assert not hasattr(mc, "MOTION_CFG_SCALE")


# ---------------------------------------------------------------------
# RULE 2 — pacing
# ---------------------------------------------------------------------

def test_a_long_beat_is_split_into_commercial_length_shots():
    from reel.pacing import MAX_SHOT_S, split_for_pace
    out = split_for_pace([6.0, 1.2, 4.0])
    assert all(d <= MAX_SHOT_S + 1e-6 for d in out), out
    assert abs(sum(out) - 11.2) < 0.01, f"total run time changed: {sum(out)}"
    assert MAX_SHOT_S <= 2.0


def test_a_short_beat_is_left_alone():
    from reel.pacing import split_for_pace
    assert split_for_pace([1.4]) == [1.4]


def test_splitting_never_produces_a_frame_flash():
    """A 3.7 s beat must not become 1.8 + 1.8 + 0.1 — a tenth-of-a-second shot reads as a glitch."""
    from reel.pacing import MIN_SHOT_S, split_for_pace
    for beat in (3.7, 5.9, 7.1, 2.01):
        for d in split_for_pace([beat]):
            assert d >= MIN_SHOT_S, f"{beat}s produced a {d}s flash"


def test_cuts_are_joined_with_motion_not_laid_end_to_end():
    from reel.pacing import transition_for
    a = transition_for(0)
    assert a["kind"] in ("whip", "push"), a
    assert a["duration_s"] <= 0.25, "a transition longer than a beat becomes the content"


def test_transitions_vary_so_the_edit_does_not_tick():
    from reel.pacing import transition_for
    kinds = {transition_for(i)["kind"] for i in range(6)}
    assert len(kinds) > 1, "every cut is the same move — that reads as a template, not an edit"
