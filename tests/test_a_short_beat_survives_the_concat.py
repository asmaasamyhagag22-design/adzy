# -*- coding: utf-8 -*-
"""A short scene must still be in the film — measured on the file, with real ffmpeg.

MEASURED 2026-08-06 through `compositor.render_reel` with the stub provider, so the only variable
in the experiment is the filter graph:

    plan [4.0, 4.0, 4.0]        -> 11.93s   fine
    plan [4.0, 1.7]             ->  3.99s   THE 1.7-SECOND SCENE IS SIMPLY GONE
    plan [1.7, 4.0]             -> 366.78s  a 5.7-second plan, six minutes of file
    plan [4.0, 4.0, 4.0, 1.7]   -> 11.93s   the last beat missing

THE CAUSE. A scene long enough to be re-cut goes through `cinematic.intra_scene_chain`, which ends
in `concat` — and `concat` emits its OWN timebase. A scene already at pace skips the chain and
keeps the timebase set earlier in the graph. Both encode to the same codec, the same pixel format
and the same size, so the concat DEMUXER accepts them for stream copy and then writes nonsense,
because the timestamps in the two files do not mean the same thing.

WHY IT MATTERS MORE THAN IT LOOKS. The scene this destroys in production is the last beat of a
15-second reel: the PRODUCT INSERT (`reel/insert_shot.py`, ~1.4-1.7s), which is short by design and
therefore never chained. The shot before the logo — the one the whole ad resolves onto — was the
one shot that could vanish.

The fix normalises timebase, frame rate and SAR AFTER the overlay, so both branches leave the graph
identical. This test renders real files with real ffmpeg because that is the only place the defect
exists: every unit test of the filter STRING passed throughout.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from reel.compositor import render_reel
from reel.schemas import ReelScene, Storyboard
from reel.text_overlay import _probe_duration
from reel.video_provider import StubVideoProvider


def _fake_text_layer(monkeypatch, tmp_path):
    """Real ffmpeg, fake Chromium: the defect is in the graph, not in the type-setting, and a
    browser session per scene would make this test far too slow to keep."""
    import reel.compositor as comp
    from PIL import Image

    def _seqs(storyboard, *, width, height, fps, out_dir, include_logo=True):
        out = []
        for i in range(len(storyboard.scenes)):
            d = Path(out_dir) / f"seq{i}"
            d.mkdir(parents=True, exist_ok=True)
            Image.new("RGBA", (width, height), (0, 0, 0, 0)).save(d / "f0000.png")
            out.append(str(d / "f%04d.png"))
        return out

    monkeypatch.setattr(comp, "render_text_sequences", _seqs)


def _render(tmp_path, plan):
    sb = Storyboard(
        business_name="NTI", primary_dir="ltr", total_duration_s=round(sum(plan), 3),
        palette_hex=["#246cb4", "#2484cc", "#203144"], primary_color="#246cb4",
        scenes=[ReelScene(kind="gallery", duration_s=d, visual_prompt=f"shot {i}",
                          source_field="creative") for i, d in enumerate(plan)],
    )
    out = tmp_path / ("r" + "_".join(str(x) for x in plan) + ".mp4")
    render_reel(sb, provider=StubVideoProvider(), out_path=out, scale=0.25, include_logo=False)
    return float(_probe_duration(out) or 0.0)


@pytest.mark.parametrize("plan", [
    [4.0, 1.7],            # delivered 3.99s — the short beat was dropped
    [1.7, 4.0],            # delivered 366.78s — the timeline was destroyed
    [4.0, 4.0, 1.7],       # the shape a real 15-second reel ships: shots, then the insert
])
def test_a_film_mixing_long_and_short_scenes_is_the_length_it_was_cut_to(tmp_path, monkeypatch,
                                                                        plan):
    monkeypatch.setattr("reel.continuity.tail_frame", lambda *a, **k: None)
    _fake_text_layer(monkeypatch, tmp_path)
    got = _render(tmp_path, plan)
    # THE SWEEPS COST THEIR OWN LENGTH, and that is the whole point of them. Since 2026-08-03 the
    # shots are joined by a white light sweep instead of a hard cut (`compositor._crossfade`) —
    # measured against the reference spot the owner supplied, whose ten seconds contain ZERO
    # detectable cuts. `xfade` OVERLAPS the two shots it joins, so an N-clip film is (N-1) sweeps
    # shorter than the sum of its scenes. The compositor subtracts exactly this from the audio
    # length too; if the two ever disagree the container runs past the last frame, which is the
    # defect this test exists to catch.
    from reel.compositor import _SWEEP_S
    expected = sum(plan) - max(0, len(plan) - 1) * _SWEEP_S
    assert got == pytest.approx(expected, abs=0.12), (
        f"a {sum(plan)}s plan with {len(plan) - 1} sweep(s) should deliver {expected:.2f}s, "
        f"got {got:.2f}s — a scene was dropped or the timeline broke")


def test_every_scene_leaves_the_graph_with_the_same_timebase_and_sar():
    """The fix, stated where it can be checked: whichever branch a scene took, the LAST filters
    before the encoder are the same, so the concat demuxer is comparing like with like."""
    import inspect

    import reel.compositor as comp
    src = inspect.getsource(comp.render_reel)
    at = src.find("overlay=x=0:y=0:format=auto")
    assert at > 0
    tail = src[at:at + 260]
    for needed in ("settb=AVTB", "setsar=1", "fps={fps}"):
        assert needed in tail, f"the graph no longer normalises {needed} after the overlay"
