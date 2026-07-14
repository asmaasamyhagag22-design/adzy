# -*- coding: utf-8 -*-
"""Render #3 §2-§4/§6-§8 — locked blocks + prompt builders. Hermetic.

The byte-for-byte invariant is a CODE assert (sha256), not a hope: any drift in a locked
block between seed calls raises before a single token is spent."""
from __future__ import annotations

import pytest

from reel.render3.prompts import (
    GLOBAL_NEGATIVE, assert_locked, character_block, character_sheet_prompt,
    expand_screen_rule, locked_hashes, seed_prompt, style_block, veo_prompt,
)
from tests.test_render3_director import _treatment


def test_style_block_only_palette_line_changes():
    t = _treatment()
    b1 = style_block(t.color_script, 1)
    b3 = style_block(t.color_script, 3)
    assert "#5B6770" in b1 and "#C9A66B" in b3
    # constants identical across acts
    for token in ("50mm", "f/2.0 shallow", "soft practical", "subtle film grain",
                  "vertical 9:16", "No text overlays"):
        assert token in b1 and token in b3


def test_character_block_identity_anchor_and_lock_sentence():
    t = _treatment()
    cb1 = character_block(t.character_sheet, 1)
    cb3 = character_block(t.character_sheet, 3)
    assert cb1.startswith("PROTAGONIST — the SAME person")
    assert cb1.endswith("identity in any way.")            # the lock sentence LAST
    assert "beige cardigan" in cb1 and "navy blazer" in cb3   # only the outfit slot changes
    assert "beauty mark above right eyebrow" in cb1
    # act 2 wears the act-1 outfit (max 2 outfits per R7)
    assert character_block(t.character_sheet, 2) == cb1


def test_locked_hashes_catch_any_drift():
    t = _treatment()
    hashes = locked_hashes(t)
    assert len(hashes) == 6
    cb = character_block(t.character_sheet, 1)
    assert_locked(cb, hashes["character_act1"], "character_act1")   # byte-identical -> ok
    with pytest.raises(AssertionError):
        assert_locked(cb + " ", hashes["character_act1"], "character_act1")  # 1 byte -> raise


def test_seed_prompt_structure_and_screen_rules():
    t = _treatment()
    hashes = locked_hashes(t)
    p = seed_prompt(t, t.shots[0], n_shots=6, hashes=hashes)
    # order: identity anchor first, scene mid, negatives last
    assert p.index("PROTAGONIST") < p.index("SCENE 1/6") < p.index("NEGATIVE:")
    assert "faces away from camera" in p                    # ANGLED_AWAY expanded
    assert GLOBAL_NEGATIVE in p
    # REAL_CONTENT fallback names mundane content
    t.shots[1].screen_rule = "REAL_CONTENT: dark terminal window, white monospace text"
    p2 = seed_prompt(t, t.shots[1], n_shots=6, hashes=hashes)
    assert "dark terminal window" in p2 and "never glowing" in p2
    # screenshot-attached form overrides
    p3 = seed_prompt(t, t.shots[1], n_shots=6, screenshot_index=3, hashes=hashes)
    assert "EXACTLY the attached screenshot (image 3)" in p3
    # location conditioning line
    p4 = seed_prompt(t, t.shots[2], n_shots=6, location_photo_attached=True, hashes=hashes)
    assert "attached real location (image 2)" in p4


def test_expand_screen_rule_table():
    assert expand_screen_rule("NONE") == "No screens visible in frame."
    assert "bokeh" in expand_screen_rule("OUT_OF_FOCUS")
    assert "faces away" in expand_screen_rule("ANGLED_AWAY")
    assert "5 labeled nodes" in expand_screen_rule("REAL_CONTENT: topology with 5 labeled nodes")
    assert expand_screen_rule("") == "No screens visible in frame."
    # ":detail" flavor on safe tokens is kept — but the safety clause ALWAYS rides along
    r = expand_screen_rule("OUT_OF_FOCUS:A monitor in the background.")
    assert r.startswith("A monitor in the background. ") and "no readable" in r
    r2 = expand_screen_rule("ANGLED_AWAY: a sleek monitor on her desk")
    assert "a sleek monitor" in r2 and "not visible" in r2


def test_character_block_is_gender_aware():
    from reel.render3.director import CharacterSheet
    man = CharacterSheet(name="Karim", age=28, presenting_gender="man", skin_tone="olive",
                         face="oval", distinctive_features=["a scar on his brow", "sharp jaw"],
                         eyes="brown", hijab=None, outfit_act1="a blue shirt",
                         outfit_act3="a navy blazer", constant_accessory="a silver watch")
    cb = character_block(man, 1)
    assert "Egyptian man" in cb and "his face" in cb and "his hair" in cb
    assert "woman" not in cb and "her " not in cb
    csp = character_sheet_prompt(man)
    assert "Egyptian man" in csp and "His hair" in csp
    # default (no gender) preserves the original woman phrasing
    t = _treatment()
    assert "Egyptian woman" in character_block(t.character_sheet, 1)


def test_seed_and_veo_prompts_are_gender_aware():
    # C1 regression: a male protagonist must never surface "her"/"the woman" in the
    # per-shot seed prompt OR the Veo animate prompt (both were hardcoded female).
    from reel.render3.director import CharacterSheet
    from reel.render3.prompts import seed_prompt
    man = CharacterSheet(name="Karim", age=28, presenting_gender="man", skin_tone="olive",
                         face="oval", distinctive_features=["a scar on his brow", "sharp jaw"],
                         eyes="brown", hijab=None, outfit_act1="a blue shirt",
                         outfit_act3="a navy blazer", constant_accessory="a silver watch")
    tm = _treatment().model_copy(update={"character_sheet": man})
    sp = seed_prompt(tm, tm.shots[0], n_shots=len(tm.shots))
    assert "his face" in sp and "her " not in sp and "woman" not in sp
    vp = veo_prompt(tm, tm.shots[0])
    assert "the man's face" in vp and "his outfit" in vp
    assert "her " not in vp and "woman" not in vp


def test_character_sheet_and_veo_prompts():
    t = _treatment()
    cs = character_sheet_prompt(t.character_sheet)
    assert "three views side by side" in cs and "identity reference" in cs
    assert "Salma" in cs and "chocolate-brown" in cs
    v = veo_prompt(t, t.shots[0])
    assert "Animate this exact frame" in v and "Identity and world lock" in v
    assert "ambient room tone only" in v and "no dialogue" in v
    assert "End the shot on: hand reaches right" in v
    # the last shot has no match cut -> no end clause
    v_last = veo_prompt(t, t.shots[-1])
    assert "End the shot on" not in v_last
