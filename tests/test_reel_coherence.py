"""Reel coherence gate (reel/coherence.py) — the code-enforced creative contracts (Phase B).

Hermetic: crafted CreativeReel plans + injected callers. Mirrors tests/test_reel_plan_eval.py and
tests/test_render3_g2.py (verdict recomputed in code).
"""
from __future__ import annotations

from reel.coherence import (
    CriticVerdict, coherence_lint, critic_review, evaluate_reel_coherence, plan_reel_gated,
)
from reel.creative_director import CreativeReel, CreativeScene


def _scene(**kw):
    kw.setdefault("image_index", 0)
    kw.setdefault("veo_prompt", "a person holds and uses the product, dynamic hand-held shot")
    kw.setdefault("duration_s", 3.0)
    return CreativeScene(**kw)


def _clean_plan():
    # Passes BOTH the coherence gate AND the folded-in legacy plan_eval (names the brand + the
    # Business Digital Store offering, distinct photo per scene, readable captions).
    return CreativeReel(
        concept="from clutter to control", hook="one app", cta="Order on the app",
        dialect="Egyptian colloquial", sound_design="tense -> warm -> confident",
        brand_unique_claim="built-in billing from your telecom",
        # creative strategy (2026-07-20 contract): a declared POV, an ordered arc the scenes walk,
        # and a motif written into the scene prompts. See tests/test_reel_creative_strategy.py.
        creative_pov="the quiet relief of one bill replacing a drawer of invoices",
        visual_motif="scattered invoices collapsing into one glowing screen",
        narrative_arc=["problem", "discovery", "proof"],
        scenes=[
            _scene(image_index=0, veo_prompt="macro of scattered paper invoices on a desk",
                   on_screen_text="Vodafone Business", caption_role="name", hero_product=True,
                   sfx="impact", sound_mood="tense", arc_beat="problem"),
            _scene(image_index=1, veo_prompt="the invoices slide into one glowing screen in a hand",
                   voiceover="the Business Digital Store", on_screen_text="one bill",
                   caption_role="benefit", sound_mood="warm", arc_beat="discovery"),
            _scene(image_index=2, veo_prompt="she sets the phone down, the screen calm",
                   voiceover="order on the app", on_screen_text="Order on the app",
                   caption_role="cta", hero_product=True, sound_mood="confident", arc_beat="proof"),
        ])


_PROFILE = {"name": {"value": "Vodafone"}, "category": {"value": "telecom"},
            "offerings": [{"name": "Business Digital Store"}]}


def test_clean_plan_passes():
    r = coherence_lint(_clean_plan(), profile=_PROFILE)
    assert r.ok and r.score == 100, r.issues


def test_lint_catches_each_defect_class():
    # 1 no hero product
    p = _clean_plan(); [setattr(s, "hero_product", False) for s in p.scenes]
    assert any("hero-product" in i for i in coherence_lint(p, profile=_PROFILE).issues)
    # 2 abstract/blur subject
    p = _clean_plan(); p.scenes[1].veo_prompt = "abstract glowing UI with blurred charts"
    assert any("abstract" in i for i in coherence_lint(p, profile=_PROFILE).issues)
    # 3 cliché hook
    p = _clean_plan(); p.scenes[0].veo_prompt = "a stressed man at a laptop looking worried"
    assert any("cliché" in i for i in coherence_lint(p, profile=_PROFILE).issues)
    # 4 CTA split (final caption != reel.cta)
    p = _clean_plan(); p.scenes[-1].on_screen_text = "learn more"
    assert any("CTA split" in i for i in coherence_lint(p, profile=_PROFILE).issues)
    # 5 dead air (no voice/sfx/sound_mood and no bed)
    p = _clean_plan(); p.sound_design = ""; p.music_mood = ""
    p.scenes[1].voiceover = ""; p.scenes[1].sfx = ""; p.scenes[1].sound_mood = ""
    assert any("dead air" in i for i in coherence_lint(p, profile=_PROFILE).issues)
    # 6 caption verbatim-repeats the VO (non-CTA scene)
    p = _clean_plan(); p.scenes[1].on_screen_text = p.scenes[1].voiceover
    assert any("verbatim" in i for i in coherence_lint(p, profile=_PROFILE).issues)
    # 7 no brand-unique claim
    p = _clean_plan(); p.brand_unique_claim = ""
    assert any("brand_unique_claim" in i for i in coherence_lint(p, profile=_PROFILE).issues)
    # 8 intra-shot cuts
    p = _clean_plan(); p.scenes[1].veo_prompt = "quick cuts of the office, then he smiles"
    assert any("multiple shots" in i for i in coherence_lint(p, profile=_PROFILE).issues)


def test_cta_scene_caption_may_match_its_voiceover():
    # the CTA scene is exempt from the verbatim rule — one action line said AND shown is correct.
    r = coherence_lint(_clean_plan(), profile=_PROFILE)
    assert r.ok, r.issues


def _critic_caller(verdict: CriticVerdict):
    def caller(system, user, model, group_name="", images=None):
        return verdict, None
    return caller


def test_critic_verdict_is_recomputed_in_code():
    # a model claiming PASS while product_shown=False is recomputed to FAIL...
    lying = CriticVerdict(hook_stops_scroll=True, works_muted=True, product_shown=False,
                          dialect_consistent=True, one_continuous_voice=True, cta_unified=True,
                          brand_specific=True, caption_strategy_ok=True, verdict="PASS")
    v = critic_review(_clean_plan(), profile=_PROFILE, caller=_critic_caller(lying))
    assert v.verdict == "FAIL"
    # ...and all-true axes are recomputed to PASS even if the model said FAIL.
    honest = CriticVerdict(hook_stops_scroll=True, works_muted=True, product_shown=True,
                           dialect_consistent=True, one_continuous_voice=True, cta_unified=True,
                           brand_specific=True, caption_strategy_ok=True, verdict="FAIL")
    assert critic_review(_clean_plan(), profile=_PROFILE, caller=_critic_caller(honest)).verdict == "PASS"


def test_gate_degrades_without_a_critic_caller():
    r = evaluate_reel_coherence(_clean_plan(), profile=_PROFILE, critic_caller=None)
    assert r.critic_ran is False and r.ok


def test_critic_failure_adds_offenders_to_the_report():
    v = CriticVerdict(hook_stops_scroll=False, works_muted=True, product_shown=True,
                      dialect_consistent=True, one_continuous_voice=True, cta_unified=True,
                      brand_specific=True, caption_strategy_ok=True,
                      offenders=["hook_stops_scroll: scene 1 — open on the product macro"])
    r = evaluate_reel_coherence(_clean_plan(), profile=_PROFILE, critic_caller=_critic_caller(v))
    assert not r.ok and any("hook_stops_scroll" in i for i in r.issues)


def test_retry_feeds_cumulative_fix_note_and_keeps_best(monkeypatch):
    import reel.coherence as co
    calls = {"n": 0, "fix_notes": []}

    def fake_design(profile, photos, **kw):
        calls["n"] += 1
        calls["fix_notes"].append(kw.get("fix_note"))
        if calls["n"] == 1:
            bad = _clean_plan(); bad.brand_unique_claim = ""      # one defect on the first round
            return bad
        return _clean_plan()                                     # fixed on the retry

    monkeypatch.setattr(co, "design_creative_reel", None, raising=False)
    monkeypatch.setattr("reel.creative_director.design_creative_reel", fake_design)
    reel, rep = plan_reel_gated(_PROFILE, ["u"], critic_caller=None, max_rounds=2)
    assert rep.ok and reel.brand_unique_claim                   # returned the fixed plan
    assert calls["n"] == 2 and calls["fix_notes"][1] and "brand_unique_claim" in calls["fix_notes"][1]


def test_gate_never_infinite_loops_returns_best_with_issues(monkeypatch):
    def always_bad(profile, photos, **kw):
        p = _clean_plan(); p.brand_unique_claim = ""; return p

    monkeypatch.setattr("reel.creative_director.design_creative_reel", always_bad)
    reel, rep = plan_reel_gated(_PROFILE, ["u"], critic_caller=None, max_rounds=2)
    assert reel is not None and not rep.ok and rep.issues       # best-effort plan + surfaced issues


def test_gate_returns_none_when_director_cannot_plan(monkeypatch):
    monkeypatch.setattr("reel.creative_director.design_creative_reel", lambda *a, **k: None)
    reel, rep = plan_reel_gated(_PROFILE, ["u"], critic_caller=None)
    assert reel is None and not rep.ok
