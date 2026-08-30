# -*- coding: utf-8 -*-
"""Five structural properties that separate a scenario from a spec sheet.

A creative-director teardown of two real plans (2026-07-28) found something uncomfortable: every
check the coherence gate ran was SATISFIED, and the reels still read as automated sequences. The
gaps were not taste — each one is a property you can read straight off the plan, and each was
present in output that had already passed.

MEASURED ON THE REAL PLANS (`outputs/nanocareegypt_com_reel_plan.json`, `outputs/nti_sci_eg_reel_plan.json`):

    scene lengths     2.5 · 3.5 · 3.0 · 3.0 · 2.5 · 3.0      every shot within half a second
                      2.0 · 3.0 · 3.0 · 3.0 · 3.0 · 3.0      of every other one
    final scene       caption and voiceover the IDENTICAL string
    captions          5 of 6, and 6 of 6 — the image never speaks alone
    scene 6 brief     "a clean, static hero shot" — which the motion contract forbids at render

TWO CORRECTIONS, recorded because the reasoning was wrong first and the record should say so:
the same teardown claimed the narrative arc was missing from production and that the gate skipped
itself. Both were wrong. Those plans predate the strategy fields (plans 2026-07-12 and 07-20;
fields added 07-24), and `plan_reel_gated` already passes `require_strategy=True`, so a freshly
authored plan must carry POV, arc and motif. The conditional is deliberate: plans approved before
the contract existed lint advisory rather than being condemned retroactively.
"""
from __future__ import annotations

import types


def _scene(**kw):
    s = types.SimpleNamespace(on_screen_text="", voiceover="", veo_prompt="a woman walks",
                              duration_s=3.0, caption_role="", hero_product=False,
                              product_overlay="", sfx="", arc_beat="")
    for k, v in kw.items():
        setattr(s, k, v)
    return s


def _run(scenes, arc=()):
    """Just the craft checks, so one property is tested at a time."""
    from reel.coherence import _craft_checks
    issues: list[str] = []
    _craft_checks(scenes, [s.on_screen_text.strip() for s in scenes],
                  [s.voiceover.strip() for s in scenes],
                  [s.veo_prompt for s in scenes],
                  [s.caption_role.strip().lower() for s in scenes], list(arc),
                  lambda m, c: issues.append(m))
    return issues


def _varied(n=6):
    """A plan that passes every craft check, so a test failure names ONE cause."""
    lens = [2.0, 4.0, 3.0, 5.0, 2.5, 4.5][:n]
    return [_scene(duration_s=lens[i], on_screen_text=("" if i == 2 else f"caption {i}"),
                   voiceover=f"line {i}") for i in range(n)]


def test_the_baseline_plan_is_clean():
    assert _run(_varied(), arc=["she is tired by 4pm", "she finds it", "she is back"]) == []


# =====================================================================
# one message per moment
# =====================================================================

def test_a_caption_that_repeats_the_voiceover_is_a_burnt_channel():
    s = _varied()
    s[1].on_screen_text = s[1].voiceover = "اطلبي نانوكير النهاردة"
    issues = _run(s)
    assert any("repeats the voiceover" in i for i in issues), issues


def test_a_caption_that_says_something_else_is_fine():
    s = _varied()
    s[1].on_screen_text, s[1].voiceover = "في ثواني", "بودرة بتدوب من غير حبوب"
    assert not any("repeats" in i for i in _run(s))


def test_whitespace_and_case_do_not_hide_a_repeat():
    s = _varied()
    s[1].voiceover = "Order Nano Care today"
    s[1].on_screen_text = "  order   nano care TODAY "
    assert any("repeats the voiceover" in i for i in _run(s))


# =====================================================================
# room to breathe
# =====================================================================

def test_captioning_every_scene_is_flagged():
    s = _varied()
    for i, sc in enumerate(s):
        sc.on_screen_text = f"caption {i}"
    assert any("left for the image alone" in i for i in _run(s))


def test_one_silent_beat_is_enough():
    assert not any("image alone" in i for i in _run(_varied()))


def test_a_very_short_reel_is_not_asked_to_stay_silent():
    """Three scenes with a caption each is a legitimate short cut, not a wall of text."""
    s = [_scene(duration_s=d, on_screen_text="c", voiceover=f"v{i}")
         for i, d in enumerate((2.0, 4.0, 3.0))]
    assert not any("image alone" in i for i in _run(s))


# =====================================================================
# the first beat is the problem
# =====================================================================

def test_an_arc_that_opens_on_the_solution_is_flagged():
    issues = _run(_varied(), arc=["the powder dissolves instantly",
                                  "she drinks it", "she feels great"])
    assert any("opens on the solution" in i for i in issues), issues


def test_an_arc_that_opens_on_the_obstacle_passes():
    for opener in ("she is exhausted by the afternoon", "الحبوب بتوجع معدتها",
                   "why does iron always hurt", "before: the 4pm crash"):
        assert not any("opens on the solution" in i
                       for i in _run(_varied(), arc=[opener, "b", "c"])), opener


def test_no_arc_is_not_this_checks_business():
    """Its absence is already a harder failure elsewhere; flagging it twice buries the real one."""
    assert not any("opens on the solution" in i for i in _run(_varied(), arc=[]))


# =====================================================================
# rhythm
# =====================================================================

def test_a_metronomic_reel_is_flagged():
    """The two real plans, verbatim."""
    for lens in ([2.5, 3.5, 3.0, 3.0, 2.5, 3.0], [2.0, 3.0, 3.0, 3.0, 3.0, 3.0]):
        s = [_scene(duration_s=d, on_screen_text=("" if i == 0 else "c"), voiceover=f"v{i}")
             for i, d in enumerate(lens)]
        assert any("metronome" in i for i in _run(s)), lens


def test_a_varied_reel_passes():
    assert not any("metronome" in i for i in _run(_varied()))


def test_rhythm_is_not_judged_on_a_three_shot_cut():
    s = [_scene(duration_s=d, voiceover="v") for d in (3.0, 3.0, 3.0)]
    assert not any("metronome" in i for i in _run(s))


# =====================================================================
# the brief may not fight the renderer
# =====================================================================

def test_a_static_shot_in_the_brief_is_flagged():
    """MEASURED: a real plan's final scene read "a clean, static hero shot" while the motion
    contract forbids exactly that at render time."""
    s = _varied()
    s[5].veo_prompt = "A clean, static hero shot of the box on a white counter."
    assert any("static/locked-off" in i for i in _run(s))


def test_the_other_ways_of_asking_for_a_frozen_frame_are_caught():
    for phrasing in ("locked-off frame on the shelf", "a still shot of the product",
                     "tripod shot of the counter", "the model stands motionless"):
        s = _varied()
        s[3].veo_prompt = phrasing
        assert any("static/locked-off" in i for i in _run(s)), phrasing


def test_ordinary_motion_is_not_mistaken_for_a_static_brief():
    for phrasing in ("she stands at the counter and pours", "a still life comes alive as she enters",
                     "the camera tracks past a static-free bright kitchen"):
        s = _varied()
        s[3].veo_prompt = phrasing
        assert not any("static/locked-off" in i for i in _run(s)), phrasing


# =====================================================================
# wired into the gate, not just written
# =====================================================================

def test_the_lint_actually_runs_the_craft_checks():
    import inspect

    import reel.coherence as c
    src = inspect.getsource(c.coherence_lint)
    assert "_craft_checks(" in src, "the craft checks are defined and never called"


def test_the_arc_reaches_the_craft_checks():
    """Check C is dead code unless the reel's own arc is passed through."""
    import inspect

    import reel.coherence as c
    src = inspect.getsource(c.coherence_lint)
    at = src.find("_craft_checks(")
    assert "narrative_arc" in src[at:at + 300], (
        "the craft checks are called without the arc, so the first-beat rule can never fire")


# =====================================================================
# the patterns themselves
# =====================================================================

def test_no_pattern_carries_a_literal_control_character():
    """A self-inflicted bug worth a permanent guard. Writing these through a shell heredoc turned
    every `\b` word boundary into a real BACKSPACE (0x08), so both regexes required a control
    character before their first word and could never match anything. Every check silently passed
    — the worst possible failure for a gate: alive, running, and blind."""
    import reel.coherence as c
    for name in [n for n in dir(c) if n.endswith("_RE")]:
        pat = getattr(c, name).pattern
        for bad in range(0x20):
            if bad in (0x09, 0x0a, 0x0d):
                continue
            assert chr(bad) not in pat, f"{name} contains control char 0x{bad:02x}"


def test_the_tension_words_are_not_english_only():
    """D-8. The arc is authored in the brand's own language; an English-only list would flag every
    Arabic plan as opening on the solution, and a check that fires on everything is unusable."""
    import reel.coherence as c
    assert c._TENSION_RE.search("الحبوب بتوجع معدتها")
    assert c._TENSION_RE.search("تعبانة طول اليوم")


def test_the_static_check_tolerates_words_between_adjective_and_noun():
    """The phrase measured in production was "a clean, static HERO shot" — a pattern requiring
    "static shot" adjacent would have missed the exact brief it was written for."""
    import reel.coherence as c
    assert c._STATIC_RE.search("A clean, static hero shot of the box")
    assert c._STATIC_RE.search("a locked-off wide frame")


def test_the_cta_may_repeat_itself():
    """The one moment repetition earns its place: the viewer hears "order today" and reads it in
    the same instant. This rule predates the burnt-channel check, which was wrong to ignore it —
    it fired on seven existing tests before the exemption was added."""
    s = _varied()
    s[-1].caption_role = "cta"
    s[-1].on_screen_text = s[-1].voiceover = "اطلبي نانوكير النهاردة"
    assert not any("repeats the voiceover" in i for i in _run(s))


def test_the_final_scene_is_exempt_even_without_the_role():
    """A director that writes the closing beat without tagging caption_role still gets the
    exemption — the position is the signal, not only the label."""
    s = _varied()
    s[-1].on_screen_text = s[-1].voiceover = "order today"
    assert not any("repeats the voiceover" in i for i in _run(s))


def test_a_mid_reel_repeat_is_still_a_burnt_channel():
    s = _varied()
    s[2].caption_role = "cta"          # mislabelled mid-reel: position still decides
    s[1].on_screen_text = s[1].voiceover = "same words twice"
    assert any("repeats the voiceover" in i for i in _run(s))
