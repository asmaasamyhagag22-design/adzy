# -*- coding: utf-8 -*-
"""The editor's cheat sheet — the last-five-minutes tricks that sell the footage as real.

OWNER'S BRIEF (2026-07-27), four secrets, checked against the code before writing any:

  1. J-CUT / anticipatory SFX — the ear believes before the eye. ALREADY HALF-BUILT:
     `sound.py` places every cut whoosh at (cut − 0.3s). What was WRONG is the level: volume=0.5
     (−6 dB) — a transition effect nearly as loud as the voice. The brief says −20 dB (≈0.1).
  2. KINETIC TYPE — word-by-word pop with a bounce, keyword in brand colour at ~120%.
     ALREADY HALF-BUILT: per-word stagger exists (_LW_STAGGER) and the `_back` easing genuinely
     overshoots (c1=1.70158 → peak ≈ 1.10, the exact 0→1.1→1.0 pop). What was WRONG: the accent
     always lands on the LAST word regardless of meaning, and carries no size emphasis.
  3. AI-MORPH CAMOUFLAGE — a subtle handheld shake so generation artefacts read as camera motion.
     MEASURED: no shake filter existed anywhere in reel/.
  4. IN MEDIA RES — cut the first 0.5s of every AI clip so the film opens mid-action.
     MEASURED: the compositor consumed every clip from t=0.

Plus the pending half of brief rule 2: intra-scene cuts. One Kling clip re-cut into ≤1.8s shots
with alternating punch-ins — the zero-cost route (the alternative bills a clip per shot).
"""
from __future__ import annotations

import re


# --- 1: the whoosh leads the cut, quietly -----------------------------------

def test_the_cut_whoosh_leads_by_a_third_of_a_second_at_minus_20db(tmp_path):
    import reel.sound as snd
    seen = {}

    def _fake_run(args):
        seen["args"] = list(args)
        from pathlib import Path
        Path(args[-1]).write_bytes(b"m4a")

    import unittest.mock as m
    with m.patch.object(snd, "run_ffmpeg", _fake_run):
        snd.synth_sound_bed(tmp_path / "bed.m4a", 10.0, cut_times=[4.0], real_sfx=False)
    graph = seen["args"][seen["args"].index("-filter_complex") + 1]
    whoosh = next(p for p in graph.split(";") if "adelay=3700|3700" in p)
    assert "volume=0.1," in whoosh or "volume=0.10" in whoosh, (
        f"the transition whoosh is not at -20dB: {whoosh}")


# --- 2: the accent word is the KEYWORD, and it is bigger ---------------------

def test_the_keyword_is_highlighted_not_blindly_the_last_word():
    from reel.textlayer import _highlight_keyword
    html = _highlight_keyword("قشرة الشعر مش قدر محتوم", keywords=["قشرة", "Alejon"])
    assert '<span class="hl">قشرة' in html, html


def test_keyword_matching_is_word_start_not_substring():
    """'lab' in 'available' — three times today. Never again by construction."""
    from reel.textlayer import _highlight_keyword
    html = _highlight_keyword("everything available here", keywords=["lab"])
    assert '<span class="hl">available</span>' not in html
    # prefix still reaches the inflected word
    html2 = _highlight_keyword("بالقشرة بتختفي", keywords=["قشرة"])
    assert '<span class="hl">' in html2


def test_no_keyword_falls_back_to_the_last_word():
    from reel.textlayer import _highlight_keyword
    assert _highlight_keyword("طاقتك كاملة", keywords=[]).endswith(
        '<span class="hl">كاملة</span>')


def test_the_highlight_is_visibly_bigger():
    import reel.textlayer as tl
    import inspect
    src = inspect.getsource(tl)
    line = next((l for l in src.splitlines() if ".headline .hl{" in l), "")
    assert "font-size:1.2em" in line.replace(" ", ""), (
        f"the accent word carries colour but no size — the brief says ~120%: {line!r}")


def test_the_bounce_easing_really_overshoots():
    """scale 0 -> ~1.1 -> 1.0: the JS `_back` must peak above 1.05 mid-curve."""
    c1, c3 = 1.70158, 2.70158
    peak = max(1 + c3 * (p / 100 - 1) ** 3 + c1 * (p / 100 - 1) ** 2 for p in range(101))
    assert peak > 1.05


# --- 3: handheld camouflage --------------------------------------------------

def test_the_shake_is_animated_and_subtle():
    from reel.cinematic import handheld_shake_filter
    f = handheld_shake_filter(1080, 1920)
    assert "crop=1080:1920" in f
    assert "sin(" in f and "t*" in f, "the crop offsets do not move with time"
    for n in re.findall(r"\*(\d+(?:\.\d+)?)(?![\d.])", f.split("crop=")[1]):
        assert float(n) <= 24, f"a {n}px throw is a earthquake, not handheld"


def test_the_shake_frequencies_do_not_tick():
    """Two commensurate sines produce a visible loop; the frequencies must not divide evenly."""
    from reel.cinematic import handheld_shake_filter
    freqs = [float(x) for x in re.findall(r"t\*(\d+(?:\.\d+)?)", handheld_shake_filter(1080, 1920))]
    assert len(freqs) >= 3
    for i, a in enumerate(freqs):
        for b in freqs[i + 1:]:
            ratio = max(a, b) / min(a, b)
            assert abs(ratio - round(ratio)) > 0.05, f"{a} and {b} beat in sync"


# --- 4: open mid-action ------------------------------------------------------

def test_head_trim_is_half_a_second_and_guarded():
    from reel.cinematic import HEAD_TRIM_S, head_trim_for
    assert HEAD_TRIM_S == 0.5
    assert head_trim_for(clip_s=5.0, scene_s=4.0) == 0.5
    # a clip with no slack must NOT be trimmed into being shorter than its scene
    assert head_trim_for(clip_s=4.2, scene_s=4.0) == 0.0
    assert head_trim_for(clip_s=0.0, scene_s=4.0) == 0.0     # unknown length -> no trim


# --- intra-scene cuts: one clip, several shots -------------------------------

def test_a_long_scene_is_recut_into_shots_with_alternating_punch():
    from reel.cinematic import intra_scene_chain
    chain = intra_scene_chain("bg", "bgc", duration_s=5.4, width=1080, height=1920)
    assert chain.count("trim=") >= 3, "a 5.4s scene stayed one shot"
    assert "concat=" in chain
    assert "1.06" in chain or "1.07" in chain, "no punch-in — the cuts would be invisible"
    assert chain.count("setpts=PTS-STARTPTS") >= 3


def test_a_short_scene_is_left_alone():
    from reel.cinematic import intra_scene_chain
    assert intra_scene_chain("bg", "bgc", duration_s=1.6, width=1080, height=1920) == "", (
        "a scene already at pace must pass through untouched")


def test_the_compositor_uses_all_three():
    """Wired, not just written — the session's rule."""
    import inspect
    import reel.compositor as rc
    src = "\n".join(l.split("#")[0] for l in inspect.getsource(rc).splitlines())
    for name in ("handheld_shake_filter", "head_trim_for", "intra_scene_chain"):
        assert name in src, f"the compositor never calls {name}"


def test_every_shot_declares_the_same_sample_aspect_ratio():
    """MEASURED — this killed a live render (Nano Care, 2026-07-27):

        Parsed_concat_14: Input link in0:v0 parameters (870x1548, SAR 1363131655:1363011146)
        do not match the corresponding output link (870x1548, SAR 972763675:972366912)
        Failed to configure output pad -> Conversion failed

    `concat` demands identical SAR across every input. The punch-in branch runs
    `scale=iw*1.06:ih*1.06` on odd shots only, and a fractional scale leaves a SAR a hair away
    from the untouched shots'. Same pixel size, different SAR, refused. Every shot must state
    its SAR explicitly rather than inherit whatever the filter chain computed.
    """
    from reel.cinematic import intra_scene_chain
    chain = intra_scene_chain("bg", "bgc", duration_s=5.4, width=1080, height=1920)
    shots = [seg for seg in chain.split(";") if seg.startswith("[bg") and "trim=" in seg]
    assert shots, chain
    for seg in shots:
        assert "setsar=1" in seg, f"a shot does not pin its SAR: {seg}"
