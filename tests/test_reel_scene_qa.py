"""Reel SCENE QA gate (reel.scene_qa + compositor wiring) — hermetic, no ffmpeg/Veo/vision.

The owner caught Veo hallucinations a system prompt can't stop: the product is unfaithful,
it 'magically' VANISHES, and it presses a SEALED pump. The gate inspects the generated clip
and, on fail, regenerates once then falls back to the faithful real-photo KenBurns.
"""
from __future__ import annotations

from pathlib import Path

from reel.schemas import ReelScene, Storyboard


# --------------------------------------------------------------------------
# reel.scene_qa.check_scene — the verdict logic
# --------------------------------------------------------------------------

def test_check_scene_no_caller_is_not_checked_not_a_pass():
    # TRI-STATE (P6): without vision we did not check — and NOT_CHECKED must be unrepresentable
    # as a pass. overall_pass is None, which is falsy, so `if v.overall_pass` fails CLOSED.
    from reel.scene_qa import check_scene
    v = check_scene("clip.mp4", caller=None)
    assert v.checked is False and v.overall_pass is None
    assert not v.overall_pass                                # None is falsy -> callers fail closed


def test_check_scene_no_frames_makes_no_call(monkeypatch):
    import reel.scene_qa as sq
    monkeypatch.setattr(sq, "_extract_frames", lambda clip, n=3: [])
    calls = {"n": 0}

    class _C:
        def __call__(self, *a, **k):
            calls["n"] += 1
            return None
    v = sq.check_scene("clip.mp4", caller=_C())
    assert v.checked is False and calls["n"] == 0            # unreadable clip -> degrade, no call


def test_check_scene_flags_vanishing_product(monkeypatch):
    import reel.scene_qa as sq
    monkeypatch.setattr(sq, "_extract_frames", lambda clip, n=3: [b"f1", b"f2", b"f3"])

    seen = {}

    class _C:
        def __call__(self, system, user, model, group_name="", images=None):
            seen["images"] = images
            seen["system"] = system
            return model(product_faithful=False, product_persists=False,
                         action_plausible=True, overall_pass=False,
                         reason="the bottle vanishes by the last frame"), None
    v = sq.check_scene("clip.mp4", product_hint="Hair Growth Mist",
                       reference_image=b"REF", caller=_C())
    assert v.checked is True and v.overall_pass is False
    assert "vanish" in v.reason
    # the reference product leads, then the 3 clip frames
    assert seen["images"][0] == (b"REF", "image/jpeg")
    assert len(seen["images"]) == 4
    assert "Hair Growth Mist" in seen["system"] and "sealed" in seen["system"].lower()


def test_check_scene_passes_a_clean_clip(monkeypatch):
    import reel.scene_qa as sq
    monkeypatch.setattr(sq, "_extract_frames", lambda clip, n=3: [b"f1"])

    class _C:
        def __call__(self, system, user, model, group_name="", images=None):
            return model(product_faithful=True, product_persists=True,
                         action_plausible=True, overall_pass=True, reason="clean"), None
    v = sq.check_scene("clip.mp4", caller=_C())
    assert v.checked is True and v.overall_pass is True


def test_check_scene_caller_error_degrades(monkeypatch):
    import reel.scene_qa as sq
    monkeypatch.setattr(sq, "_extract_frames", lambda clip, n=3: [b"f1"])

    class _C:
        def __call__(self, *a, **k):
            raise RuntimeError("vision down")
    v = sq.check_scene("clip.mp4", caller=_C())
    # an infra failure means NOT_CHECKED, never "passed". Callers that choose to ship anyway must
    # say so explicitly (`v.overall_pass is not False`) rather than read a fabricated True.
    assert v.checked is False and v.overall_pass is None


# --------------------------------------------------------------------------
# compositor wiring — a bad scene is regenerated then faithfully fallen back
# --------------------------------------------------------------------------

class _Rec:
    def __init__(self, name):
        self.name = name
        self.seen: list[tuple[str, object]] = []

    def generate(self, prompt, *, out_path, duration_s, width, height,
                 palette=None, reference_image=None):
        self.seen.append((prompt, reference_image))
        Path(out_path).write_bytes(b"\x00")
        return Path(out_path)


def _stub_io(comp, monkeypatch):
    def _fake_text_seqs(storyboard, *, width, height, fps, out_dir, entrance_s=1.2,
                        include_logo=True):
        out = []
        for i in range(len(storyboard.scenes)):
            d = Path(out_dir) / f"seq{i}"
            d.mkdir(parents=True, exist_ok=True)
            out.append(str(d / "f%04d.png"))
        return out

    def _fake_ffmpeg(args, **kwargs):
        Path(args[-1]).write_bytes(b"\x00")
        return None

    monkeypatch.setattr(comp, "render_text_sequences", _fake_text_seqs)
    monkeypatch.setattr(comp, "run_ffmpeg", _fake_ffmpeg)


def _storyboard():
    scenes = [
        ReelScene(kind="gallery", duration_s=2.0, visual_prompt="S0", seed_image_url="https://x/p.jpg"),
        ReelScene(kind="gallery", duration_s=2.0, visual_prompt="S1", seed_image_url="https://x/p.jpg"),
    ]
    return Storyboard(business_name="B", scenes=scenes, palette_hex=["#111111"],
                      content_images=["https://x/p.jpg"], total_duration_s=4.0)


def test_qa_fail_regenerates_once_then_falls_back(tmp_path, monkeypatch):
    import reel.compositor as comp
    import reel.scene_qa as sq
    monkeypatch.setattr(sq, "_extract_frames", lambda clip, n=3: [b"f"])   # frames always present
    _stub_io(comp, monkeypatch)

    class _FailQA:
        def __init__(self):
            self.calls = 0

        def __call__(self, system, user, model, group_name="", images=None):
            self.calls += 1
            return model(product_faithful=False, product_persists=False,
                         action_plausible=False, overall_pass=False, reason="sealed pump"), None
    qa = _FailQA()
    primary = _Rec("veo")
    fb = _Rec("kenburns")
    res = comp.render_reel(_storyboard(), provider=primary, out_path=tmp_path / "o.mp4",
                           scale=0.25, fallback_provider=fb, qa_caller=qa,
                           qa_product_hint="Hair Growth", qa_reference_image=b"REF")
    # per scene: 1 initial + 1 retry on the primary, then the faithful KenBurns fallback
    assert len(primary.seen) == 4                       # 2 scenes x (initial + retry)
    # every prompt now carries the standing no-text clause (calibration fix 2026-07-12)
    assert {p.split(" Absolutely no readable text")[0] for p, _ in fb.seen} == {"S0", "S1"}
    assert all("no readable text" in p for p, _ in fb.seen)
    assert res.fallback_used is True
    assert qa.calls == 4                                # 2 scenes x (check + recheck)


def test_qa_pass_keeps_clip_no_retry_no_fallback(tmp_path, monkeypatch):
    import reel.compositor as comp
    import reel.scene_qa as sq
    monkeypatch.setattr(sq, "_extract_frames", lambda clip, n=3: [b"f"])
    _stub_io(comp, monkeypatch)

    class _PassQA:
        def __call__(self, system, user, model, group_name="", images=None):
            return model(product_faithful=True, product_persists=True,
                         action_plausible=True, overall_pass=True, reason="clean"), None
    primary = _Rec("veo")
    fb = _Rec("kenburns")
    res = comp.render_reel(_storyboard(), provider=primary, out_path=tmp_path / "o.mp4",
                           scale=0.25, fallback_provider=fb, qa_caller=_PassQA(),
                           qa_product_hint="Hair Growth", qa_reference_image=b"REF")
    assert len(primary.seen) == 2                       # one generate per scene, no retry
    assert fb.seen == []                                # nothing fell back
    assert res.fallback_used is False


def test_no_qa_caller_is_the_old_behaviour(tmp_path, monkeypatch):
    import reel.compositor as comp
    import reel.scene_qa as sq
    # if QA ran it would consult frames; assert it never does when qa_caller is None
    called = {"frames": 0}
    monkeypatch.setattr(sq, "_extract_frames", lambda clip, n=3: called.__setitem__("frames", called["frames"] + 1) or [b"f"])
    _stub_io(comp, monkeypatch)
    primary = _Rec("veo")
    res = comp.render_reel(_storyboard(), provider=primary, out_path=tmp_path / "o.mp4",
                           scale=0.25)                  # no qa_caller
    assert len(primary.seen) == 2 and called["frames"] == 0 and res.fallback_used is False
