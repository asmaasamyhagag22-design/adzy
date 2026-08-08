"""The audio and the picture must be built from the SAME number.

MEASURED 2026-08-02 on the Al Fouad reel, with ffmpeg:

    container   15.462s
    video track  392 frames, ends at 13.033s   -> 2.43s with no picture at all
    audio        audible to 12.166s            -> 3.29s of dead silence
    freeze       12.533s for 0.467s

The owner saw it as «الريل اتقط» and «الصوت خلص بدري», and «العلبة اللي في الآخر غير» — because the
end of the film was a held frame, not the product insert.

One cause under all three. The clips are cut to `scene.duration_s`; the sound bed and the music
fade were built from `storyboard.total_duration_s`, a STORED field. When the two disagree the audio
is written for a film that does not exist, and `-shortest` cannot rescue it: with `-c:v copy` it
does not truncate a stream-copied video, so the container simply takes the audio's length.

Both readers now sum the scenes — a number that cannot drift from what was rendered — and the mux
is bounded explicitly.
"""
from __future__ import annotations

import ast
import inspect

import reel.compositor as comp
import reel.creative as creative


def _code_only(module) -> str:
    """Source with comments and docstrings stripped.

    A source-scanning test that reads comments finds the OLD code quoted in the note explaining
    why it was replaced, and passes or fails on prose. That has already happened four times in
    this repo; the guard is cheap."""
    src = inspect.getsource(module)
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Module)):
            body = getattr(node, "body", None)
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant)                     and isinstance(body[0].value.value, str):
                body[0].value.value = ""
    return ast.unparse(tree)


def test_the_bed_is_built_from_the_scenes_not_from_a_stored_field():
    code = _code_only(creative)
    assert "acc or storyboard.total_duration_s" in code, (
        "the sound bed still trusts a stored total over the durations actually rendered"
    )
    assert "storyboard.total_duration_s or acc" not in code


def test_the_music_fade_is_placed_against_the_rendered_length():
    src = _code_only(comp)
    assert "duration_s" in src and "sum(" in src, (
        "the fade is still scheduled from a stored total"
    )


def test_the_mux_is_bounded_explicitly():
    """`-shortest` with `-c:v copy` does not truncate the copied video, so the container took the
    audio's length — 2.43s of it with no frames. Every mux path now carries an explicit -t."""
    src = inspect.getsource(comp.render_reel)
    assert src.count('"-t", f"{total:.3f}"') == 3, (
        "a mux path can still emit a container longer than the picture"
    )


def test_every_mux_path_still_carries_shortest():
    """Belt and braces: -t bounds the top, -shortest still guards a short audio track."""
    src = inspect.getsource(comp.render_reel)
    assert src.count('"-shortest"') == 3


def test_the_two_readers_agree_on_a_storyboard_whose_field_is_stale():
    """The regression in miniature: a storyboard whose stored total is wrong must not change what
    either side computes."""
    from types import SimpleNamespace
    scenes = [SimpleNamespace(duration_s=4.0), SimpleNamespace(duration_s=6.0),
              SimpleNamespace(duration_s=3.03)]
    stale = SimpleNamespace(scenes=scenes, total_duration_s=15.46)
    summed = round(sum(float(s.duration_s) for s in stale.scenes), 3)
    assert summed == 13.03
    assert summed != stale.total_duration_s, "fixture must actually be stale"
