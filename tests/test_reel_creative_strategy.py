# -*- coding: utf-8 -*-
"""Creative STRATEGY gate — the owner's ruling (2026-07-20): "Taste له مفيش ground truth...
مستحيل تختبره. لكن Strategy يمكن." So the gate never judges whether a reel is *good*; it checks
the STRUCTURE a director's work has and a shot-collector's doesn't, and every check below is
mechanically decidable:

  - creative_pov  — ONE declared feeling the viewer leaves with (present, specific, not a generic
                    marketing adjective).
  - narrative_arc — the STORY the POV can't carry (owner: "الـPOV يقول إحساس لكن لا يقول القصة"):
                    >=3 distinct ordered beats, scenes walk them in order, first/last scene sit on
                    the first/last beat.
  - visual_motif  — the recurring visual thread; it must ACTUALLY RECUR (its tokens appear in >=2
                    scenes' prompts), which is what separates a declared motif from a wish.

Hermetic (no model calls)."""
from __future__ import annotations

from reel.coherence import coherence_lint
from reel.creative_director import CreativeReel, CreativeScene

_PROFILE = {"name": {"value": "Vodafone"}, "category": {"value": "telecom"},
            "offerings": [{"name": "Business Digital Store"}]}

_ARC = ["problem", "discovery", "proof"]


def _scene(**kw):
    kw.setdefault("image_index", 0)
    kw.setdefault("veo_prompt", "a person holds and uses the product, dynamic hand-held shot")
    kw.setdefault("duration_s", 3.0)
    return CreativeScene(**kw)


def _plan(**over):
    """A plan that is clean on every PRE-EXISTING contract, so any failure below is strategy."""
    base = dict(
        concept="from clutter to control", hook="one app", cta="Order on the app",
        dialect="Egyptian colloquial", sound_design="tense -> warm -> confident",
        brand_unique_claim="built-in billing from your telecom",
        creative_pov="the quiet relief of one bill replacing a drawer of invoices",
        visual_motif="scattered paper invoices collapsing into a single glowing screen",
        narrative_arc=list(_ARC),
        scenes=[
            _scene(image_index=0, veo_prompt="macro of paper invoices scattered on a desk",
                   on_screen_text="Vodafone Business", caption_role="name", hero_product=True,
                   sfx="impact", sound_mood="tense", arc_beat="problem"),
            _scene(image_index=1, veo_prompt="the invoices slide into one screen in a hand",
                   voiceover="the Business Digital Store", on_screen_text="one bill",
                   caption_role="benefit", sound_mood="warm", arc_beat="discovery"),
            _scene(image_index=2, veo_prompt="the screen glows calm as she sets it down",
                   voiceover="order on the app", on_screen_text="Order on the app",
                   caption_role="cta", hero_product=True, sound_mood="confident",
                   arc_beat="proof"),
        ])
    base.update(over)
    return CreativeReel(**base)


def _issues(plan) -> str:
    return " | ".join(coherence_lint(plan, profile=_PROFILE).issues)


def test_a_strategically_structured_plan_passes():
    r = coherence_lint(_plan(), profile=_PROFILE)
    assert r.ok and r.score == 100, r.issues


def test_missing_pov_is_rejected():
    assert "creative_pov" in _issues(_plan(creative_pov=""))


def test_generic_pov_is_rejected_in_every_language():
    # "جودة عالية"/"high quality" is not a point of view — it is what every brand says.
    for generic in ("high quality", "جودة عالية", "professional and modern",
                    "premium experience", "trust and quality"):
        assert "creative_pov" in _issues(_plan(creative_pov=generic)), generic


def test_motif_must_actually_recur_in_the_scenes():
    # Declared but never written into any scene = a wish, not a motif.
    bad = _plan(visual_motif="a recurring circle of warm amber light")
    assert "visual_motif" in _issues(bad)
    # Present in only ONE scene is still not recurrence.
    p = _plan(visual_motif="amber light")
    p.scenes[0].veo_prompt = "macro of invoices under amber light"
    assert "visual_motif" in _issues(p)
    # Two scenes carry it -> it recurs.
    p.scenes[2].veo_prompt = "the screen glows in the same amber light as she sets it down"
    assert "visual_motif" not in _issues(p)


def test_arc_needs_at_least_three_distinct_beats():
    assert "narrative_arc" in _issues(_plan(narrative_arc=[]))
    assert "narrative_arc" in _issues(_plan(narrative_arc=["problem", "proof"]))
    dup = _plan(narrative_arc=["problem", "problem", "proof"])
    assert "narrative_arc" in _issues(dup)


def test_every_scene_must_sit_on_a_declared_beat():
    p = _plan()
    p.scenes[1].arc_beat = ""
    assert "arc_beat" in _issues(p)
    p2 = _plan()
    p2.scenes[1].arc_beat = "epiphany"                 # not in the declared arc
    assert "arc_beat" in _issues(p2)


def test_scenes_must_walk_the_arc_in_order():
    p = _plan()
    p.scenes[0].arc_beat, p.scenes[1].arc_beat = "proof", "problem"   # story runs backwards
    assert "order" in _issues(p)
    # Opening off the first beat / closing off the last beat both break the arc.
    p2 = _plan()
    p2.scenes[0].arc_beat = "discovery"
    assert "narrative_arc" in _issues(p2) or "order" in _issues(p2)


def test_two_scenes_may_share_one_beat():
    # Non-decreasing, not strictly increasing: a beat can span two shots.
    p = _plan()
    p.scenes[1].arc_beat = "problem"
    p.scenes[1].veo_prompt = "more paper invoices pile up next to the single glowing screen"
    r = coherence_lint(p, profile=_PROFILE)
    assert r.ok, r.issues


def test_a_newly_authored_plan_may_not_skip_strategy_by_leaving_it_blank():
    # The backward-compat skip below must not become a loophole: when the gate is AUTHORING a plan
    # (require_strategy=True, what plan_reel_gated passes), omitting all three fields is a failure,
    # not a pass. Otherwise a lazy plan escapes the contract by saying nothing at all.
    blank = _plan(creative_pov="", visual_motif="", narrative_arc=[])
    for s in blank.scenes:
        s.arc_beat = ""
    assert coherence_lint(blank, profile=_PROFILE).ok                      # legacy call: advisory
    r = coherence_lint(blank, profile=_PROFILE, require_strategy=True)
    assert not r.ok and any("creative_pov" in i for i in r.issues)


def test_authoring_loop_requires_strategy(monkeypatch):
    from reel.coherence import plan_reel_gated
    blank = _plan(creative_pov="", visual_motif="", narrative_arc=[])
    for s in blank.scenes:
        s.arc_beat = ""
    monkeypatch.setattr("reel.creative_director.design_creative_reel",
                        lambda *a, **k: blank.model_copy(deep=True))
    _reel, rep = plan_reel_gated(_PROFILE, ["u"], critic_caller=None, max_rounds=1)
    assert not rep.ok and any("creative_pov" in i for i in rep.issues)


def test_old_plans_without_strategy_fields_still_load():
    # Backward compatibility: an owner-APPROVED plan authored before this contract must still
    # construct and lint (advisory at that call site) rather than raising.
    old = CreativeReel(concept="c", hook="h", cta="Order now", scenes=[_scene()])
    assert old.creative_pov == "" and old.narrative_arc == []
    coherence_lint(old, profile=_PROFILE)          # must not raise
