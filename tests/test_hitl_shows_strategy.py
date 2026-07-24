# -*- coding: utf-8 -*-
"""The HITL brief must SHOW the creative strategy — otherwise the whole point is invisible.

The gate now forces a POV, a narrative arc and a visual motif on every reel plan. If the brief she
approves in the dashboard doesn't display them, she is asked to approve ~$11 of rendering without
being able to see the one thing that changed. Hermetic (the brief text is built in code)."""
from __future__ import annotations

from dashboard.run import _reel_brief_lines
from reel.creative_director import CreativeReel, CreativeScene


def _plan():
    return CreativeReel(
        concept="from clutter to control", hook="one app", cta="Order on the app",
        language="ar",
        creative_pov="the quiet relief of one bill replacing a drawer of invoices",
        visual_motif="scattered invoices collapsing into one glowing screen",
        narrative_arc=["problem", "discovery", "proof"],
        scenes=[
            CreativeScene(image_index=0, veo_prompt="invoices scattered on a desk", duration_s=3,
                          arc_beat="problem", on_screen_text="Nano"),
            CreativeScene(image_index=1, veo_prompt="they slide into one screen", duration_s=3,
                          arc_beat="discovery", voiceover="one bill"),
        ])


def test_brief_opens_with_the_point_of_view_arc_and_motif():
    text = "\n".join(_reel_brief_lines(_plan(), flag=lambda s: ""))
    assert "وجهة النظر" in text and "one bill replacing a drawer" in text
    assert "القوس" in text and "problem" in text and "proof" in text
    assert "الموتيف" in text and "glowing screen" in text


def test_each_scene_shows_the_beat_it_serves():
    text = "\n".join(_reel_brief_lines(_plan(), flag=lambda s: ""))
    assert "problem" in text.split("SCENE 1")[1].split("SCENE 2")[0]
    assert "discovery" in text.split("SCENE 2")[1]


def test_an_old_plan_without_strategy_still_renders_a_brief():
    old = CreativeReel(concept="c", hook="h", cta="Order",
                       scenes=[CreativeScene(image_index=0, veo_prompt="v", duration_s=3)])
    text = "\n".join(_reel_brief_lines(old, flag=lambda s: ""))
    assert "CONCEPT" in text and "وجهة النظر" not in text     # omitted, not printed empty
