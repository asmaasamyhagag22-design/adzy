# -*- coding: utf-8 -*-
"""Footage that MOVES, cut at commercial pace — the two rules that kill the slideshow feel.

BRIEF (owner, 2026-07-27), rules 1 and 2 of four:
  1. every video prompt carries handheld/tracking/continuous-motion modifiers, a high motion
     parameter, and negative exclusions for static camera / slideshow / ken-burns / frozen subject;
  2. no single shot exceeds ~1.8 s, and shots are joined by speed ramps or whip pans rather than
     laid end to end.

MEASURED BEFORE WRITING THIS. `grep -rn "negative_prompt|cfg_scale|motion_strength" reel/` returned
NOTHING: the Kling request body was `{model_name, prompt, duration, aspect_ratio, image}` — no
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


def test_the_kling_request_carries_the_negative_prompt_and_motion():
    """The contract is worthless if it never reaches the wire."""
    from reel.video_provider import KlingProvider

    sent = {}

    class _T:
        """Matches the real transport's shape (keyword-only headers/json_body), so the test
        exercises the same call the network class would receive."""

        def post(self, url, *, headers=None, json_body=None):
            sent.update(json_body or {})
            return {"data": {"task_id": "t1"}}

        def get(self, url, *, headers=None):
            return {"data": {"task_status": "succeed",
                             "task_result": {"videos": [{"url": "https://x.test/v.mp4"}]}}}

        def download(self, url, dest):
            return dest

    p = KlingProvider(access_key="a", secret_key="b", transport=_T())
    # A seed frame is required: Kling t2v is refused by design (an unseeded clip is invented
    # footage), so a test without one would never reach the request body at all.
    p.generate("a pharmacist reaching for a shelf", out_path="/tmp/x.mp4",
               duration_s=5, width=1080, height=1920,
               reference_image="https://x.test/seed.jpg")

    assert "negative_prompt" in sent, "no negative prompt reaches Kling"
    assert "slideshow" in str(sent["negative_prompt"]).lower()
    assert "cfg_scale" in sent, "no motion/adherence parameter reaches Kling"
    assert 0.7 <= float(sent["cfg_scale"]) <= 0.85, sent["cfg_scale"]
    assert "handheld" in str(sent.get("prompt", "")).lower()


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
