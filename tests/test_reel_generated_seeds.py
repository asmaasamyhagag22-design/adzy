# -*- coding: utf-8 -*-
"""FULLY-GENERATED reel scenes (owner creative ruling 2026-07-12) — hermetic.

Pins the REPURPOSING contract: real photos exit the DISPLAY path (generated seed is what
Veo animates) but expand as conditioning (REAL PLACE prompt block + attached photo) and as
the motion-QA judging reference (place_ref_url). Fail-closed: a seed that fails the gate
twice falls back to the REAL photo. Scope boundary: reels only — poster evidence surfaces
untouched (pinned by the poster suites)."""
from __future__ import annotations

from pathlib import Path

from poster.schemas import PosterBrief
from reel.creative import build_creative_storyboard
from reel.creative_director import CreativeReel, CreativeScene
from reel.seed_gen import build_seed_prompt, generate_scene_seeds


def _plan() -> CreativeReel:
    return CreativeReel(
        concept="c", hook="h", cta="قدم دلوقتي", language="ar",
        images=["https://x/real0.jpg", "https://x/real1.jpg"],
        scenes=[CreativeScene(image_index=0, veo_prompt="lab scene", voiceover="س",
                              duration_s=4.0),
                CreativeScene(image_index=1, veo_prompt="campus scene", voiceover="ص",
                              duration_s=4.0)])


def test_seed_prompt_carries_conditioning_and_bans():
    p = build_seed_prompt("a student coding in the lab", brand_name="NTI",
                          dna_lines="modern tech campus", locale_line="Set in Egypt.",
                          n_refs=1)
    assert "REAL PLACE" in p and "unmistakably lives THERE" in p
    assert "Do NOT paste" in p                       # fresh frame, not a photo copy
    assert "no text, letters, numbers, logos" in p   # the poster-grade no-text contract
    assert "Set in Egypt." in p and "modern tech campus" in p
    assert "9:16" in p and "a student coding in the lab" in p
    assert "REAL PLACE" not in build_seed_prompt("x", n_refs=0)   # honest without refs


def test_storyboard_maps_generated_seed_and_real_judging_ref():
    reel = _plan()
    brief = PosterBrief(business_name="NTI", headline="NTI")
    seeds = [("outputs/reels/_genseeds/seed_a.png", "https://x/real0.jpg"),
             (None, "https://x/real1.jpg")]          # scene 2: generation failed twice
    sb = build_creative_storyboard(reel, brief, seeds=seeds)
    s0, s1 = sb.scenes
    # scene 1: the GENERATED still is displayed; the REAL photo becomes the judge's reference
    assert s0.seed_image_url.endswith("seed_a.png")
    assert s0.place_ref_url == "https://x/real0.jpg"
    # scene 2 fail-closed: the REAL photo seeds (legacy), no self-judging reference
    assert s1.seed_image_url == "https://x/real1.jpg" and s1.place_ref_url is None
    # legacy mode (seeds=None) unchanged
    sb_old = build_creative_storyboard(reel, brief)
    assert sb_old.scenes[0].seed_image_url == "https://x/real0.jpg"
    assert sb_old.scenes[0].place_ref_url is None


def test_seedless_scenes_drop_and_dead_tail_fits(monkeypatch, tmp_path):
    # Calibration #2 (owner): a raw photo on display read as "قطع خالص" -> a seedless scene
    # DROPS (>=3 guard); and the video total tracks the surviving speech (dead-tail fit).
    import reel.creative as rc
    import reel.plan_eval as pe
    from reel.plan_eval import ReelPlanVerdict
    monkeypatch.setattr(pe, "evaluate_reel_plan",
                        lambda *a, **k: ReelPlanVerdict(ok=True, score=90))
    captured: dict = {}
    monkeypatch.setattr(rc, "synth_voiceover",
                        lambda lines, durs, out, **kw: captured.update(vo=list(lines),
                                                                       durs=list(durs)) or None)
    monkeypatch.setattr(rc, "render_reel",
                        lambda sb, **kw: captured.update(sb=sb) or {"ok": True})
    monkeypatch.setenv("REEL_SEED_MODE", "generated")
    seeds = [("g1.png", "https://x/r0.jpg"), (None, "https://x/r1.jpg"),
             ("g2.png", "https://x/r0.jpg"), ("g3.png", "https://x/r1.jpg")]
    monkeypatch.setattr("reel.seed_gen.generate_scene_seeds",
                        lambda *a, **k: list(seeds))

    plan = CreativeReel(
        concept="c", hook="h", cta="قدم", language="ar",
        images=["https://x/r0.jpg", "https://x/r1.jpg"],
        scenes=[CreativeScene(image_index=0, veo_prompt=f"S{i}",
                              voiceover="كلمة " * 4, duration_s=6.0) for i in range(4)])
    rc.render_creative_reel({}, PosterBrief(business_name="NTI", headline="NTI"), [],
                            provider=None, out_path=tmp_path / "r.mp4",
                            plan_override=plan.model_dump(), with_voiceover=True)
    sb = captured["sb"]
    assert len(sb.scenes) == 3                                # the seedless scene DROPPED
    assert all(s.seed_image_url and s.seed_image_url.endswith(".png") for s in sb.scenes)
    # dead-tail fit: 3 scenes x 4 words -> speech ~ 6.75s -> video ~ 8.75s, not 3x6=18s
    assert sb.total_duration_s <= 9.5
    assert all(s.duration_s >= 2.0 for s in sb.scenes)        # per-scene floor holds
    assert len(captured["vo"]) == 3                           # VO lines follow the drop


def test_tts_brief_carries_latin_name_pronunciation():
    from reel.voiceover import _instructions_for
    brief = _instructions_for(["برنامج HireReady من NTI يؤهلك"], [], tone="")
    assert "PRONUNCIATION" in brief and "never letter-by-letter" in brief


def test_generate_scene_seeds_gate_retry_and_fallback(monkeypatch, tmp_path):
    import reel.seed_gen as sg

    calls = {"gen": 0, "prompts": []}

    def _fake_gen(prompt, out_path, **kw):
        calls["gen"] += 1
        calls["prompts"].append(prompt)
        Path(out_path).write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 16)
        return Path(out_path)
    monkeypatch.setattr("poster.oneshot.generate_oneshot_poster", _fake_gen)
    monkeypatch.setattr(sg, "_ref_bytes", lambda url: (b"refbytes", "image/jpeg"))

    from reel.scene_qa import SeedVerdict
    verdicts = iter([SeedVerdict(overall_pass=True, checked=True),      # scene 1: pass
                     SeedVerdict(overall_pass=False, checked=True,      # scene 2: fail
                                 reason="warped"),
                     SeedVerdict(overall_pass=False, checked=True,      # retry: fail
                                 reason="warped again"),
                     SeedVerdict(overall_pass=False, checked=True,      # plain 3rd: fail
                                 reason="still warped")])
    monkeypatch.setattr("reel.scene_qa.check_seed_frame",
                        lambda p, caller=None, brand_hint="", log=None, **kw: next(verdicts))

    out = generate_scene_seeds(_plan(), ["https://x/real0.jpg", "https://x/real1.jpg"],
                               profile={"name": {"value": "NTI"}}, out_dir=tmp_path,
                               caller=object())
    assert len(out) == 2
    assert out[0][0] and out[0][1] == "https://x/real0.jpg"     # generated + judged vs real
    assert out[1][0] is None and out[1][1] == "https://x/real1.jpg"   # fail-closed fallback
    assert calls["gen"] == 4                # 1 + (corrective retry + plain 3rd) for scene 2
    assert "Natural anatomy" in calls["prompts"][-2]            # corrective retry suffix
    assert "REAL PLACE" not in calls["prompts"][-1]             # 3rd attempt drops conditioning


def test_an_UNJUDGED_seed_never_animates(monkeypatch, tmp_path):
    """P6 #7 — the seed gate guards a PAID Veo render, so NOT_CHECKED must not read as a pass.

    The old code was `if (not v.checked) or v.overall_pass:` — no vision caller, an unreadable
    PNG, or ONE Gemini timeout (scene_qa returns checked=False on all three) meant the seed was
    accepted and animated at full render cost, while seed_gen's own docstring promised
    "Fail-closed". This test fails if that hole ever returns.
    """
    import reel.seed_gen as sg
    from reel.scene_qa import SeedVerdict

    def _fake_gen(prompt, out_path, **kw):
        Path(out_path).write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 16)
        return Path(out_path)
    monkeypatch.setattr("poster.oneshot.generate_oneshot_poster", _fake_gen)
    monkeypatch.setattr(sg, "_ref_bytes", lambda url: (b"refbytes", "image/jpeg"))
    # the gate could not run — the exact shape scene_qa returns on no-caller / unreadable / timeout
    monkeypatch.setattr("reel.scene_qa.check_seed_frame",
                        lambda p, caller=None, brand_hint="", log=None, **kw:
                        SeedVerdict(checked=False, reason="seed gate call failed (TimeoutError)"))

    out = generate_scene_seeds(_plan(), ["https://x/real0.jpg", "https://x/real1.jpg"],
                               profile={"name": {"value": "NTI"}}, out_dir=tmp_path,
                               caller=object())
    # every scene falls back to the real photo; NOT ONE unjudged seed is handed to Veo
    assert [s for s, _ in out] == [None, None]
    assert [r for _, r in out] == ["https://x/real0.jpg", "https://x/real1.jpg"]


def test_the_seed_lane_is_chosen_EXPLICITLY_not_inherited(monkeypatch, tmp_path):
    """C2 — seeds must NAME their model, never inherit the poster's.

    seed_gen called generate_oneshot_poster passing NO model=, so seeds silently rode the POSTER's
    lane: .env sets ONESHOT_IMAGE_MODEL=gemini-3-pro-image-preview (a measured ruling about the
    POSTER's Arabic text), oneshot.py binds it into DEFAULT_ONESHOT_MODEL, and that is a def-time
    default arg. A poster decision therefore governed reel seeds by accident — and a future .env
    edit would have moved seeds again, silently. Now it is an explicit constant.

    NOTE the value is PRO, and that is a MEASURED conclusion, not the original hypothesis — see
    test_the_pro_seed_lane_is_justified_by_measurement below.
    """
    import reel.seed_gen as sg
    from reel.scene_qa import SeedVerdict

    seen: dict = {}

    def _fake_gen(prompt, out_path, **kw):
        seen["model"] = kw.get("model")
        Path(out_path).write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 16)
        return Path(out_path)

    monkeypatch.setattr("poster.oneshot.generate_oneshot_poster", _fake_gen)
    monkeypatch.setattr(sg, "_ref_bytes", lambda url: None)
    monkeypatch.setattr("reel.scene_qa.check_seed_frame",
                        lambda p, caller=None, brand_hint="", log=None, **kw:
                        SeedVerdict(overall_pass=True, checked=True))

    generate_scene_seeds(_plan(), ["https://x/real0.jpg", "https://x/real1.jpg"],
                         profile={"name": {"value": "NTI"}}, out_dir=tmp_path, caller=object())

    assert seen["model"] == sg._SEED_IMAGE_MODEL, "seeds are not naming their own model"
    assert seen["model"] is not None, "seeds fell back to inheriting the poster's default"


def test_the_POSTERS_env_cannot_move_the_seed_lane(monkeypatch, tmp_path):
    """Env-sharing caused this, so the fix must not depend on env AT ALL. Whatever the poster's
    ruling is in the environment, seeds render on the model THEY name."""
    import reel.seed_gen as sg
    from reel.scene_qa import SeedVerdict

    monkeypatch.setenv("ONESHOT_IMAGE_MODEL", "some-other-model-entirely")
    seen: dict = {}

    def _fake_gen(prompt, out_path, **kw):
        seen["model"] = kw.get("model")
        Path(out_path).write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 16)
        return Path(out_path)

    monkeypatch.setattr("poster.oneshot.generate_oneshot_poster", _fake_gen)
    monkeypatch.setattr(sg, "_ref_bytes", lambda url: None)
    monkeypatch.setattr("reel.scene_qa.check_seed_frame",
                        lambda p, caller=None, brand_hint="", log=None, **kw:
                        SeedVerdict(overall_pass=True, checked=True))

    generate_scene_seeds(_plan(), ["https://x/real0.jpg"], profile={"name": {"value": "NTI"}},
                         out_dir=tmp_path, caller=object())
    assert seen["model"] == sg._SEED_IMAGE_MODEL
    assert seen["model"] != "some-other-model-entirely", "the poster's env moved the seed lane"


def test_the_pro_seed_lane_is_justified_by_measurement_not_inheritance():
    """The premium is DEFENDED, not assumed — and the obvious optimisation was tested and refused.

    Hypothesis (mine and the owner's): the pro lane exists to RENDER legible text; build_seed_prompt
    BANS text; therefore seeds pay 3x ($0.12 vs $0.04) for a capability their own prompt forbids.
    A same-prompt A/B (outputs/defense/seed_ab/, ~$0.16) asked the project's OWN seed gate:
        PRO  -> overall_pass=True,  score 9, no_text=True
        NANO -> overall_pass=False, score 4, no_text=False
               "The computer screen displays readable code, violating the no-text rule."
    The premium buys INSTRUCTION-FOLLOWING (including "do not render text"), not text rendering.
    With the gate in the loop nano retries 3x = $0.12 — one clean pro seed's price — and can still
    yield NO seed. Same cost, worse outcome.

    This test pins the CONCLUSION so nobody re-runs the same clever idea without re-measuring.
    """
    import vertex_spend as vs

    import reel.seed_gen as sg
    assert sg._SEED_IMAGE_MODEL == "gemini-3-pro-image-preview"
    assert vs.est_image_cost_usd(1, sg._SEED_IMAGE_MODEL) == 0.12   # the premium, eyes open
    # and the ban the hypothesis was built on really is in the prompt (it was never the issue)
    assert "no text, letters, numbers, logos" in sg.build_seed_prompt("x", n_refs=0)
