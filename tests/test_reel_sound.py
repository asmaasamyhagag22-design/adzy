"""Render-level sound design (reel/sound.py + reel/creative.py wiring) — overhaul Phase C.

The creative reel was SILENT but for the VO. The plan now authors a sound arc (mood) + per-scene
sfx; the render synthesizes a mood-tuned bed + those cues. Hermetic: run_ffmpeg is captured, no audio
is actually encoded.
"""
from __future__ import annotations

from pathlib import Path

import reel.sound as snd


def _graph(monkeypatch, *args, **kw) -> str:
    cap: dict = {}
    monkeypatch.setattr(snd, "run_ffmpeg", lambda a: cap.__setitem__("args", a))
    snd.synth_sound_bed(Path("x.m4a"), *args, **kw)
    return " ".join(cap["args"])


def test_mood_tunes_the_pad(monkeypatch):
    tense = _graph(monkeypatch, 12.0, mood="tense and cluttered")
    assert "98.00" in tense and "lowpass=f=360" in tense           # dark, sparse
    confident = _graph(monkeypatch, 12.0, mood="confident resolve")
    assert "196.00" in confident and "lowpass=f=780" in confident  # bright, full


def test_sfx_beats_place_named_cues_at_their_times(monkeypatch):
    g = _graph(monkeypatch, 12.0, sfx_beats=[(0.0, "impact"), (6.0, "chime"), (9.0, "whoosh")])
    assert "sine=frequency=62" in g                                # impact boom
    assert "880" in g and "1318.51" in g                           # chime blip
    assert "anoisesrc" in g                                        # whoosh sweep
    assert "adelay=6000|6000" in g                                 # chime at t=6.0s
    assert "adelay=8700|8700" in g                                 # whoosh fires 0.3s BEFORE its cut (9.0)


def test_sfx_beats_out_of_range_are_skipped(monkeypatch):
    # impact=False removes the always-on opening boom so we isolate the (skipped) out-of-range cues
    g = _graph(monkeypatch, 5.0, impact=False, sfx_beats=[(99.0, "chime"), (-1.0, "impact")])
    assert "880" not in g and "anoisesrc" not in g and "adelay" not in g


def test_positional_call_is_back_compatible(monkeypatch):
    g = _graph(monkeypatch, 6.0, [2.0, 4.0])                       # old signature: no mood/sfx
    assert "110.00" in g and "amix" in g                          # default pad, valid graph


def test_render_creative_synthesizes_a_bed_when_no_music(monkeypatch, tmp_path):
    import reel.creative as rc
    from poster.schemas import PosterBrief
    from reel.creative_director import CreativeReel, CreativeScene

    captured: dict = {}
    monkeypatch.setattr(rc, "render_reel", lambda sb, **kw: captured.update(kw) or {"ok": True})

    def _fake_bed(out, total, cut_times=None, *, mood="", sfx_beats=None, impact=True):
        captured["bed"] = {"total": total, "mood": mood, "sfx_beats": sfx_beats}
        Path(out).write_bytes(b"BED")
        return Path(out)
    monkeypatch.setattr("reel.sound.synth_sound_bed", _fake_bed)

    plan = CreativeReel(
        concept="c", hook="h", cta="x", language="ar", sound_design="tense -> confident",
        images=["u0", "u1"],
        scenes=[CreativeScene(image_index=0, veo_prompt="A", voiceover="", duration_s=3.0, sfx="impact"),
                CreativeScene(image_index=1, veo_prompt="B", voiceover="", duration_s=4.0, sfx="chime")])
    rc.render_creative_reel({}, PosterBrief(business_name="NTI", headline="NTI"), [],
                            provider=None, out_path=tmp_path / "r.mp4",
                            plan_override=plan.model_dump(), with_voiceover=False)
    # a bed was synthesized and handed to the compositor as music
    assert captured.get("music_path") and Path(captured["music_path"]).name.endswith(".bed.m4a")
    bed = captured["bed"]
    assert bed["mood"] == "tense -> confident"
    assert bed["total"] == 7.0                                    # == storyboard duration (no dead-air tail)
    assert bed["sfx_beats"] == [(0.0, "impact"), (3.0, "chime")]  # cues at cumulative scene starts


def test_supplied_music_is_not_overridden_by_the_synth(monkeypatch, tmp_path):
    import reel.creative as rc
    from poster.schemas import PosterBrief
    from reel.creative_director import CreativeReel, CreativeScene
    captured: dict = {}
    monkeypatch.setattr(rc, "render_reel", lambda sb, **kw: captured.update(kw) or {"ok": True})
    monkeypatch.setattr("reel.sound.synth_sound_bed",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not synth when music given")))
    plan = CreativeReel(concept="c", hook="h", cta="x", language="ar", images=["u0"],
                        scenes=[CreativeScene(image_index=0, veo_prompt="A", duration_s=3.0)])
    rc.render_creative_reel({}, PosterBrief(business_name="NTI", headline="NTI"), [],
                            provider=None, out_path=tmp_path / "r.mp4", music_path="real_track.mp3",
                            plan_override=plan.model_dump(), with_voiceover=False)
    assert captured["music_path"] == "real_track.mp3"             # the real track wins
