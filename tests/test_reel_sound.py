"""Render-level sound design (reel/sound.py + reel/creative.py wiring) — overhaul Phase C.

The creative reel was SILENT but for the VO. The plan now authors a sound arc (mood) + per-scene
sfx; the render synthesizes a mood-tuned bed + those cues. Hermetic: run_ffmpeg is captured, no audio
is actually encoded.
"""
from __future__ import annotations

from pathlib import Path

import pytest

import reel.sound as snd


def _graph(monkeypatch, tmp_path, *args, **kw) -> str:
    # P-92: write under tmp_path, NEVER a bare "x.m4a" — the run_ffmpeg mock doesn't create the
    # file, so synth_sound_bed falls to _wave_impact_fallback which used to leak `x.wav` into the
    # repo root (a 529KB untracked artifact). tmp_path is auto-cleaned.
    cap: dict = {}
    monkeypatch.setattr(snd, "run_ffmpeg", lambda a: cap.__setitem__("args", a))
    snd.synth_sound_bed(tmp_path / "x.m4a", *args, **kw)
    return " ".join(cap["args"])


def test_mood_tunes_the_pad(monkeypatch, tmp_path):
    tense = _graph(monkeypatch, tmp_path, 12.0, mood="tense and cluttered")
    assert "98.00" in tense and "lowpass=f=360" in tense           # dark, sparse
    confident = _graph(monkeypatch, tmp_path, 12.0, mood="confident resolve")
    assert "196.00" in confident and "lowpass=f=780" in confident  # bright, full


def test_sfx_beats_place_named_cues_at_their_times(monkeypatch, tmp_path):
    g = _graph(monkeypatch, tmp_path, 12.0, sfx_beats=[(0.0, "impact"), (6.0, "chime"), (9.0, "whoosh")])
    assert "sine=frequency=62" in g                                # impact boom
    assert "880" in g and "1318.51" in g                           # chime blip
    assert "anoisesrc" in g                                        # whoosh sweep
    assert "adelay=6000|6000" in g                                 # chime at t=6.0s
    assert "adelay=8700|8700" in g                                 # whoosh fires 0.3s BEFORE its cut (9.0)


def test_sfx_beats_out_of_range_are_skipped(monkeypatch, tmp_path):
    # impact=False removes the always-on opening boom so we isolate the (skipped) out-of-range cues
    g = _graph(monkeypatch, tmp_path, 5.0, impact=False, sfx_beats=[(99.0, "chime"), (-1.0, "impact")])
    assert "880" not in g and "anoisesrc" not in g and "adelay" not in g


def test_positional_call_is_back_compatible(monkeypatch, tmp_path):
    g = _graph(monkeypatch, tmp_path, 6.0, [2.0, 4.0])             # old signature: no mood/sfx
    assert "110.00" in g and "amix" in g                          # default pad, valid graph


def test_render_creative_does_not_synthesize_a_bed():
    """The film carries the ElevenLabs voice alone — the synth bed is OFF.

    OWNER, 2026-08-02: «امسح صوت veo لانه بشع… واظبط صوت 11لاب». Measured before changing
    anything: Veo's audio never reaches the file — the per-scene normalise maps `[v]` only and the
    mux takes `0:v` from a concat with no audio track. So the machine sound was OURS:
    `synth_sound_bed`, an ffmpeg whoosh/pulse/chime bed. She named the wrong suspect; the sound
    was real.

    A silent bed is not a missing feature. A synthesised whoosh on every cut is the loudest tell
    that nobody scored the film. `synth_sound_bed` itself is kept — a real track can be handed in
    via `music_path`, and this only stops the pipeline from inventing one.
    """
    import inspect

    import reel.creative as cr
    src = inspect.getsource(cr)
    assert "_BED_OFF = True" in src
    assert "if music_path is None and not _BED_OFF:" in src, (
        "the pipeline still synthesizes a bed of its own"
    )


def test_a_real_music_track_is_still_muxed():
    """Turning the SYNTH off must not turn AUDIO off: a caller-supplied track still plays."""
    import inspect

    import reel.compositor as comp
    src = inspect.getsource(comp.render_reel)
    assert "has_music" in src and "music_path" in src


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
