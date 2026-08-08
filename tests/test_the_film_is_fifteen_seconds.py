# -*- coding: utf-8 -*-
"""The delivered length is the length that was asked for — and the music survives the logo.

MEASURED 2026-08-06 on every reel this pipeline has shipped (ffmpeg, on the files themselves):

    file                duration   silence starts   dead tail
    alfouad_run3.mp4      15.30s        12.73s        2.57s
    nanocare_film.mp4     16.69s        10.40s        6.29s
    alfouad_v2_rev.mp4    11.00s         8.39s        2.61s
    web_reel_f3815e3b     9.40s          7.29s        2.11s

Two defects, both of them arithmetic rather than taste:

  * the end card was appended AFTER the budget was spent, so every film ran `budget + 2.1s`, and
    `tempo.quantise` may only round UP, so it grew again on the way;
  * the card was handed `anullsrc`, so the reel's audio cross-faded INTO SILENCE and the last two
    seconds of every ad — the frames carrying the brand — had no sound at all.

These tests fail if either comes back.
"""
from __future__ import annotations

import inspect

import pytest

from reel.runtime import (
    DEFAULT_RUNTIME_S, ENDCARD_S, ENDCARD_XFADE_S, FRAME_S,
    endcard_tail_s, fit_to_runtime, picture_budget_s,
)
from reel.tempo import beat_grid_for_brand


# =====================================================================
# the budget accounts for the close
# =====================================================================

def test_the_picture_and_the_card_add_up_to_the_named_runtime():
    assert picture_budget_s(15.0) + endcard_tail_s() == pytest.approx(15.0, abs=1e-9)
    assert picture_budget_s(DEFAULT_RUNTIME_S, with_endcard=False) == pytest.approx(
        DEFAULT_RUNTIME_S, abs=1e-9)


def test_the_card_does_not_eat_a_sixth_of_a_fifteen_second_ad():
    """2.6s of static logo on a 15s film is 17% of the run time, and it would be the longest shot
    in the film. An end frame holds long enough to read a name."""
    assert ENDCARD_S <= 2.0
    assert endcard_tail_s() == pytest.approx(ENDCARD_S - ENDCARD_XFADE_S, abs=1e-9)


def test_the_end_card_takes_its_length_from_the_runtime_module():
    """A second literal is how the budget and the card drifted apart in the first place."""
    import reel.endcard as ec
    sig = inspect.signature(ec.append_endcard_to_reel)
    assert sig.parameters["seconds"].default == ENDCARD_S


def test_the_scene_count_is_derived_from_the_picture_budget_not_the_whole_runtime():
    from reel.__main__ import _scenes_for
    src = inspect.getsource(_scenes_for)
    assert "picture_budget_s" in src, "the scene count still spends the end card's seconds"
    assert _scenes_for(15.0, 10, has_insert=True) >= 3


# =====================================================================
# the cuts land ON the number
# =====================================================================

@pytest.mark.parametrize("durations,floors", [
    ([4.0, 4.16, 4.0, 1.4], [2.2, 2.6, 2.0, 1.4]),      # the shipped alfouad shape
    ([5.0, 5.0, 5.0], [0.0, 0.0, 0.0]),                  # fully elastic
    ([4.0, 4.0, 4.0, 1.4], [0.0, 4.9, 0.0, 1.4]),        # one scene pinned by its narration
    ([8.0, 4.0], [1.0, 1.0]),                            # two long beats
    ([2.0, 2.0, 2.0, 2.0, 2.0], [0.0] * 5),              # more scenes than the budget wants
])
@pytest.mark.parametrize("family", ["luxury", "warm", "clinical", "vibrant"])
def test_the_film_lands_on_its_runtime(durations, floors, family):
    from reel.tempo import _BPM
    beat = 60.0 / _BPM[family]
    target = picture_budget_s(15.0)
    out, note = fit_to_runtime(durations, floors, beat, target)
    assert sum(out) == pytest.approx(target, abs=1e-3), (family, out, note)
    assert all(o > 0 for o in out), out
    for o, f in zip(out, floors):
        assert o >= f - 1e-6, f"a legibility floor was traded away for the runtime: {o} < {f}"


def test_a_line_too_long_for_the_format_is_reported_not_silently_overrun():
    """The floors are the two things the owner has rejected a reel over — a line needing time to
    be SAID and a caption time to be READ. When they exceed the format the answer is a writing
    decision, so the film says so instead of clipping a caption or quietly running long."""
    g = beat_grid_for_brand(profile={})
    out, note = fit_to_runtime([6.0, 6.0, 6.0], [6.0, 6.0, 6.0], g.beat_s, picture_budget_s(15.0))
    assert sum(out) == pytest.approx(18.0, abs=1e-6), "a floor was broken to make the runtime"
    assert note and "shorten" in note, note


def test_the_cuts_are_still_on_the_beat():
    g = beat_grid_for_brand(profile={})
    out, _n = fit_to_runtime([4.0, 4.5, 4.0, 1.4], [1.0, 1.0, 1.0, 1.4], g.beat_s,
                             picture_budget_s(15.0))
    for i in range(len(out) - 1):                    # the final boundary is pinned to the target
        start = sum(out[:i + 1])
        assert abs(round(start / g.beat_s) * g.beat_s - start) <= 2e-3, (i, start)


def test_no_target_leaves_the_film_exactly_as_it_was():
    out, note = fit_to_runtime([4.0, 5.0], [0.0, 0.0], 0.5, 0.0)
    assert out == [4.0, 5.0] and note


# =====================================================================
# the music lands on the logo
# =====================================================================

def _fake_endcard(monkeypatch, tmp_path, *, bed: bool):
    """Drive `append_endcard_to_reel` with every external step faked, and capture the ffmpeg
    argv it builds. Behaviour, not source: what the function ASKS FOR is what ships."""
    import reel.endcard as ec

    reel = tmp_path / "r.mp4"
    reel.write_bytes(b"\0" * 64)
    if bed:
        (tmp_path / "r.bed.m4a").write_bytes(b"\0" * 64)

    calls: list[list[str]] = []
    monkeypatch.setattr(ec, "_video_dims", lambda p: (1080, 1920))
    monkeypatch.setattr(ec, "render_endcard",
                        lambda *a, **k: (tmp_path / "card.png").write_bytes(b"x")
                        or (tmp_path / "card.png"))
    monkeypatch.setattr(ec, "run_ffmpeg", lambda args, **k: calls.append(list(args)))
    import reel.motion as rm
    monkeypatch.setattr(rm, "_clip_duration", lambda p: 20.0 if str(p).endswith(".m4a") else 13.7)
    monkeypatch.setattr(rm, "_has_audio", lambda p: True)
    monkeypatch.setattr(ec, "shutil", __import__("shutil"), raising=False)
    import shutil as _sh
    monkeypatch.setattr(_sh, "move", lambda a, b: None)
    ok = ec.append_endcard_to_reel({"name": "B"}, reel)
    return ok, calls


def test_the_card_gets_the_music_not_silence(monkeypatch, tmp_path):
    """The measured dead tail was not a mixing bug — the card's audio input was literally
    `anullsrc`. With a bed on disk the card must be fed the bed's TAIL instead."""
    _ok, calls = _fake_endcard(monkeypatch, tmp_path, bed=True)
    mux = [c for c in calls if any("xfade" in str(a) for a in c)]
    assert mux, calls
    argv = " ".join(mux[-1])
    assert "anullsrc" not in argv, "the end card is still silent"
    assert ".bed.m4a" in argv, "the sound bed never reaches the card"
    assert "acrossfade" in argv, "the join is not cross-faded"


def test_with_no_bed_the_card_is_honestly_silent(monkeypatch, tmp_path):
    """Inventing audio for a card is worse than a quiet one — the degrade stays the old path."""
    _ok, calls = _fake_endcard(monkeypatch, tmp_path, bed=False)
    argv = " ".join([" ".join(c) for c in calls])
    assert "anullsrc" in argv


def test_the_bed_is_written_long_enough_to_cover_the_card():
    src = inspect.getsource(__import__("reel.creative", fromlist=["x"]))
    at = src.find("synth_sound_bed(")
    assert at > 0
    window = src[max(0, at - 900):at + 400]
    assert "endcard_tail_s()" in window, "the bed still stops where the picture stops"


def test_the_music_fade_is_scheduled_at_the_end_of_the_film_not_the_picture():
    """Fading 1.5s before the PICTURE ends dips the track exactly as the card arrives."""
    src = inspect.getsource(__import__("reel.compositor", fromlist=["x"]))
    assert "music_tail_s" in src
    assert "total - 1.5" not in src, "the fade is still anchored to the picture's end"


# =====================================================================
# both doors ship the same film
# =====================================================================

@pytest.mark.parametrize("module,needle", [
    ("reel.__main__", "max_seconds=args.max_seconds"),
    ("api.routes.reel", "max_seconds=float("),
])
def test_the_runtime_reaches_the_renderer_from_every_door(module, needle):
    """`--max-seconds` and the API's `max_seconds` both existed and neither reached the function
    that decides how long the cuts are — the format governed the scene COUNT and nothing else."""
    src = inspect.getsource(__import__(module, fromlist=["x"]))
    assert needle in src, f"{module} still renders without telling it the runtime"


def test_the_renderer_accepts_a_runtime():
    from reel.creative import render_creative_reel
    assert "max_seconds" in inspect.signature(render_creative_reel).parameters
