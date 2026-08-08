# -*- coding: utf-8 -*-
"""Owner round 2026-07-12 ('الريل البشع') — four measured root fixes, hermetic.

1. HITL SANCTITY: an EXECUTE-approved plan is FINAL — the pre-render eval may log, but a
   weak score must NEVER swap in a regenerated plan the user never saw.
2. LISTENABLE PACING: the VIDEO stretches to fit the words (~2.2 wps + a breath); the
   audio atempo squeeze is capped at 1.12x (1.35x was unintelligible).
3. EMOTIONAL ARC: every scene's voiceover_delivery reaches the narrator — the old code
   kept only the FIRST one, so a 'frustrated' hook directed the whole film.
4. FEATURED SEEDING: 'image_index ALWAYS 0' only when a real product photo anchors the
   pool; a photo-less offering (NTI course) draws on DIFFERENT real brand photos.
"""
from __future__ import annotations

from poster.schemas import PosterBrief
from reel.creative import build_creative_storyboard, render_creative_reel
from reel.creative_director import CreativeReel, CreativeScene, _system_prompt
from reel.plan_eval import ReelPlanVerdict
from reel.voiceover import _fit_filter, _instructions_for


def _plan(**kw) -> CreativeReel:
    base = dict(
        concept="c", hook="h", cta="قدم دلوقتي", language="ar",
        images=["https://x/0.jpg", "https://x/1.jpg"],
        scenes=[CreativeScene(image_index=0, veo_prompt="APPROVED SHOT ONE",
                              voiceover="بتجمع شهادات ومش لاقي فرصة؟",
                              voiceover_delivery="Relatable and slightly frustrated.",
                              on_screen_text="مش لاقي فرصة؟", duration_s=2.5),
                CreativeScene(image_index=1, veo_prompt="APPROVED SHOT TWO",
                              voiceover="في المعهد بنحول معرفتك لخبرة حقيقية مع أكاديمية المواهب المصرية",
                              voiceover_delivery="Confident and inviting.",
                              on_screen_text="خبرة حقيقية", duration_s=4.0)],
    )
    base.update(kw)
    return CreativeReel(**base)


def test_hitl_approved_plan_is_never_replaced_by_the_eval(monkeypatch, tmp_path):
    # The exact betrayal the owner caught: plan_eval scored her approved plan weak and the
    # renderer silently regenerated a different plan. Now: advisory log only.
    import reel.creative as rc
    import reel.plan_eval as pe

    monkeypatch.setattr(pe, "evaluate_reel_plan",
                        lambda *a, **k: ReelPlanVerdict(ok=False, score=0, issues=["weak"]))

    def _never(*a, **k):
        raise AssertionError("design_creative_reel must NOT run on an approved plan")
    monkeypatch.setattr(rc, "design_creative_reel", _never)

    captured: dict = {}

    def _fake_render(storyboard, **kw):
        captured["sb"] = storyboard
        return {"ok": True}
    monkeypatch.setattr(rc, "render_reel", _fake_render)

    plan = _plan()
    result, creative = render_creative_reel(
        {}, PosterBrief(business_name="NTI", headline="NTI"), [],
        provider=None, out_path=tmp_path / "r.mp4",
        plan_override=plan.model_dump(), with_voiceover=False)
    assert result == {"ok": True}
    # THE APPROVED CREATIVE IS UNTOUCHED AND LEADS. Nothing was swapped, nothing reordered,
    # nothing rewritten — which is the betrayal this test was written to catch.
    prompts = [s.visual_prompt for s in captured["sb"].scenes]
    assert [p.split("\n")[0] for p in prompts] == ["APPROVED SHOT ONE", "APPROVED SHOT TWO"]
    assert creative.scenes[0].voiceover == "بتجمع شهادات ومش لاقي فرصة؟"

    # UPDATED 2026-08-01. This used to assert byte-equality, and byte-equality made the LOOK LOCK
    # impossible to carry — an approved plan rendered with no shared lighting/grade sentence, so
    # every shot of an owner-approved film was lit differently and no post pass could repair it
    # (`brand/look.py`: the shadows are already in the pixels).
    #
    # "Verbatim" protects CREATIVE choices: what happens in the shot, how it moves, what is said.
    # It has never meant "no technical constraint may be appended" — `compositor.py` already adds
    # its no-readable-text ban to every prompt, approved or not, for the same reason. The look is
    # that species. So the law is now stated as: the approved text leads and survives intact, and
    # the ONLY thing that may follow it is the look clause.
    from reel.creative import _LOOK_MARK
    for original, rendered in zip(["APPROVED SHOT ONE", "APPROVED SHOT TWO"], prompts):
        extra = rendered[len(original):]
        assert not extra or extra.lstrip().startswith(_LOOK_MARK), \
            f"an approved prompt gained something that is not the look lock: {extra[:80]!r}"


def test_non_approved_render_routes_through_the_coherence_gate(monkeypatch, tmp_path):
    # With no APPROVED plan, the render authors through the coherence gate (which enforces the
    # creative contracts + auto-corrects), not the raw director — so a fresh render is gated too.
    import reel.creative as rc
    import reel.coherence as co
    from reel.coherence import CoherenceReport
    called: dict = {}

    def _fake_gate(profile, photos, **kw):
        called["yes"] = True
        return _plan(), CoherenceReport(ok=True, score=100)
    monkeypatch.setattr(co, "plan_reel_gated", _fake_gate)
    monkeypatch.setattr(rc, "render_reel", lambda sb, **kw: {"ok": True})

    result, creative = render_creative_reel(
        {}, PosterBrief(business_name="NTI", headline="NTI"), [],
        provider=None, out_path=tmp_path / "r.mp4", with_voiceover=False)   # no plan_override
    assert called.get("yes") is True and result == {"ok": True}
    assert creative.scenes[0].veo_prompt == "APPROVED SHOT ONE"             # the gated plan is used


def test_grounding_gate_blanks_unsourced_vo_even_on_approved_plans(monkeypatch, tmp_path):
    # The owner's pre-EXECUTE check found the creative path AUDIT-ONLY (reel/grounding.py said
    # so itself). Now BLOCKING: an unsourced hard claim in the spoken VO is BLANKED — on HITL
    # plans too (blanking is a veto, not a plan swap). Grounded/emotive lines pass untouched.
    import reel.creative as rc
    import reel.plan_eval as pe
    monkeypatch.setattr(pe, "evaluate_reel_plan",
                        lambda *a, **k: ReelPlanVerdict(ok=True, score=90))
    captured: dict = {}

    def _fake_synth(lines, durs, out, **kw):
        captured["vo"] = list(lines)
        return None
    monkeypatch.setattr(rc, "synth_voiceover", _fake_synth)
    monkeypatch.setattr(rc, "render_reel", lambda sb, **kw: {"ok": True})

    plan = _plan()
    # scene 1 gains a fabricated award (empty profile -> nothing can source it)
    plan.scenes[0].voiceover = "حاصلين على جايزة أفضل معهد في مصر 2026"
    render_creative_reel({}, PosterBrief(business_name="NTI", headline="NTI"), [],
                         provider=None, out_path=tmp_path / "r.mp4",
                         plan_override=plan.model_dump(), with_voiceover=True)
    assert captured["vo"][0] == ""                                  # fabricated award BLANKED
    assert captured["vo"][1] == plan.scenes[1].voiceover            # clean line untouched


def test_narrated_scene_stretches_to_a_listenable_pace():
    # Her S2: 13 words squeezed into 4.0s (3.2 wps). The video must stretch to ~words/2.2.
    reel = _plan()
    sb = build_creative_storyboard(reel, PosterBrief(business_name="NTI", headline="NTI"))
    words = len(reel.scenes[1].voiceover.split())
    assert sb.scenes[1].duration_s >= round(0.3 + words / 2.2, 2)
    assert sb.scenes[1].duration_s <= 8.0


def test_captions_off_by_default_reel_is_pure_footage(monkeypatch):
    # Owner reversal (2026-07-12): "الغي الكلام اللي ع الريل خالص خليه صور" — no kinetic
    # captions by default; the branded end-card (compositor) is separate and stays.
    monkeypatch.delenv("REEL_CAPTIONS", raising=False)
    reel = _plan()
    sb = build_creative_storyboard(reel, PosterBrief(business_name="NTI", headline="NTI"))
    assert all(not (s.headline or s.cta_text) for s in sb.scenes)
    assert all(s.kind == "gallery" for s in sb.scenes)
    # the VO listenable-pacing stretch still applies without captions
    words = len(reel.scenes[1].voiceover.split())
    assert sb.scenes[1].duration_s >= round(0.3 + words / 2.2, 2)
    # and the flag preserves the old routing
    sb_on = build_creative_storyboard(reel, PosterBrief(business_name="NTI", headline="NTI"),
                                      captions=True)
    assert any(s.headline or s.cta_text for s in sb_on.scenes)


def test_audio_squeeze_capped_at_1_12x():
    # A read 33% too long must NOT be sped 1.33x (unintelligible) — 1.12x max.
    af = _fit_filter(raw_dur=25.0, total=19.0)
    assert "atempo=1.120" in af
    # a read that fits gets no speed-up at all
    assert "atempo" not in _fit_filter(raw_dur=10.0, total=19.0)


def test_full_emotional_arc_reaches_the_narrator():
    arc = ["Relatable and slightly frustrated", "Confident and inviting", "Proud and conclusive"]
    brief = _instructions_for(["س1", "س2", "س3"], arc, tone="")
    for d in arc:
        assert d in brief                       # EVERY direction, not just the first
    assert "ARC" in brief
    assert "never rush" in brief                # the anti-rush pace line, all moods
    # backward compat: a plain string still works
    assert "just this one" in _instructions_for(["x"], "just this one", tone="")


def test_motion_qa_conjunction_is_computed_in_code(monkeypatch):
    # Owner directive: the model never self-passes — a verdict that says overall_pass=True
    # while flagging ad_grade=False (or any criterion) FAILS in code.
    import reel.scene_qa as sq
    monkeypatch.setattr(sq, "_extract_frames", lambda clip, n=3: [b"f1", b"f2", b"f3"])

    class _Optimist:
        def __call__(self, system, user, model, group_name="", images=None):
            return model(product_faithful=True, product_persists=True, action_plausible=True,
                         ad_grade=False, overall_pass=True, reason="model says fine"), None

    v = sq.check_scene("c.mp4", caller=_Optimist())
    assert v.checked and v.ad_grade is False
    assert v.overall_pass is False                     # code conjunction wins

    class _SettingDrift:
        def __call__(self, system, user, model, group_name="", images=None):
            assert "setting_faithful" in system        # the additive criteria reached the judge
            assert "no_junk_generated_text" in system
            return model(product_faithful=True, product_persists=True, action_plausible=True,
                         setting_faithful=False, overall_pass=True, reason="old ruin"), None

    v2 = sq.check_scene("c.mp4", caller=_SettingDrift())
    assert v2.overall_pass is False and v2.setting_faithful is False


def test_seed_gate_conjunction_threshold_and_degrade(tmp_path):
    # Stage A (owner directive): judge a generated still BEFORE Veo — code computes the
    # conjunction + score>=6 threshold; no caller degrades permissive (checked=False).
    from reel.scene_qa import check_seed_frame
    img = tmp_path / "seed.png"
    img.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 32)

    class _Judge:
        def __init__(self, **verdict):
            self.verdict = verdict

        def __call__(self, system, user, model, group_name="", images=None):
            assert group_name == "reel_seed_gate" and images
            return model(**self.verdict), None

    good = check_seed_frame(img, caller=_Judge(anatomy_ok=True, lighting_real=True,
                                               composition_clean=True, ad_grade=True,
                                               score=8, reason="clean"))
    assert good.checked and good.overall_pass
    # a single failed criterion fails in code
    bad = check_seed_frame(img, caller=_Judge(anatomy_ok=False, lighting_real=True,
                                              composition_clean=True, ad_grade=True,
                                              score=9, reason="six fingers"))
    assert bad.checked and not bad.overall_pass
    # a weak score fails even when every boolean passes
    weak = check_seed_frame(img, caller=_Judge(anatomy_ok=True, lighting_real=True,
                                               composition_clean=True, ad_grade=True,
                                               score=5, reason="mediocre"))
    assert weak.checked and not weak.overall_pass
    # degrade contract (TRI-STATE, P6): no caller -> NOT_CHECKED, which is NOT a pass. The seed
    # gate guards a paid Veo render, so an unjudged seed must never animate (seed_gen.py).
    skipped = check_seed_frame(img, caller=None)
    assert not skipped.checked and skipped.overall_pass is None


def test_scene_qa_runs_for_service_brands_with_seed_reference(monkeypatch, tmp_path):
    # The gap the owner caught live: no featured product -> QA never ran. Now qa_caller runs
    # for every creative reel and the scene's OWN seed becomes the judge's reference.
    from pathlib import Path

    import reel.compositor as comp

    calls = {"qa": 0, "refs": []}

    class _Judge:
        def __call__(self, system, user, model, group_name="", images=None):
            calls["qa"] += 1
            calls["refs"].append(len(images or []))
            return model(product_faithful=True, product_persists=True, action_plausible=True,
                         overall_pass=True, reason="clean"), None

    class _Prov:
        name = "fake"

        def generate(self, prompt, **kw):
            p = tmp_path / f"clip{calls['qa']}.mp4"
            p.write_bytes(b"\x00")
            return str(p)

    import reel.scene_qa as sq
    monkeypatch.setattr(sq, "_extract_frames", lambda clip, n=3: [b"f1", b"f2"])
    monkeypatch.setattr(comp, "render_text_sequences",
                        lambda sb, *a, **k: [str(tmp_path / "tx_%02d.png")] * len(sb.scenes))
    monkeypatch.setattr(comp, "run_ffmpeg", lambda args, **k: Path(str(args[-1])).write_bytes(b"\x00"))
    monkeypatch.setattr("reel.video_provider._load_reference_image",
                        lambda url: [(b"seedbytes", "image/jpeg")] if url else None)

    from reel.schemas import ReelScene, Storyboard
    sb = Storyboard(business_name="NTI", primary_dir="rtl", total_duration_s=4.0,
                    scenes=[ReelScene(kind="gallery", duration_s=4.0, visual_prompt="p",
                                      seed_image_url="https://x/real.jpg")],
                    palette_hex=["#111111"], content_images=["https://x/real.jpg"])
    comp.render_reel(sb, provider=_Prov(), out_path=tmp_path / "r.mp4",
                     qa_caller=_Judge())                 # NO product hint, NO product ref
    assert calls["qa"] == 1                              # QA ran without a featured product
    assert calls["refs"][0] >= 3                         # seed reference + 2 frames reached it


def test_director_carries_scenario_screen_guard_and_tts_rules():
    # Owner round 2 (2026-07-12): "مش سيناريو" -> ONE CONTINUOUS SCENARIO; Veo junk pseudo-text
    # on screens -> SCREENS & MARKS guard (incl. no third-party logos); "الكلام أوفر/بيقولوه
    # غلط" -> <=8 words, silent scenes encouraged, numbers as words (TTS-friendly).
    p = _system_prompt(6, "ar", "generic")
    assert "ONE CONTINUOUS SCENARIO" in p and "SAME protagonist" in p
    assert "SCREENS & MARKS" in p and "NO readable text" in p
    assert "third-party marks" in p                    # the swoosh class, banned at the source
    assert "NEVER more than 8 words" in p and "silent" in p
    assert "TTS-FRIENDLY" in p and "أربع شهور" in p


def test_featured_offering_without_photo_uses_varied_photos():
    anchored = _system_prompt(6, "ar", "generic", featured="X", product_photo_anchored=True)
    assert "ALWAYS 0" in anchored               # real product photo -> same-item shots
    unanchored = _system_prompt(6, "ar", "generic", featured="Fiber Course",
                                product_photo_anchored=False)
    assert "ALWAYS 0" not in unanchored         # photo-less course: never one photo x6
    assert "FEATURED OFFERING" in unanchored and "DIFFERENT" in unanchored
    # and the director now carries the hard pace budget
    assert "PACE BUDGET" in unanchored and "2 words per second" in unanchored
