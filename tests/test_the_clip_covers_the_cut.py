# -*- coding: utf-8 -*-
"""Buy coverage, cut what the edit wants — and the measurement that corrected the diagnosis.

I claimed a reel "runs short" when a scene asks for 5 seconds and the video model, which only sells
4/6/8, sold it 4. MEASURED on `[4.0, 5.0, 6.0, 5.5]` with a stub that snaps exactly like the real
provider: **the claim was wrong.** Every scene renders to within one frame of its requested length —
the compositor's `-t` and the padded text overlay see to that — so the film is not short and nothing
downstream drifts.

What IS wrong is smaller and was completely silent:

  * the under-bought scene's last second contains **11% identical consecutive frames** against
    **0%** in every scene whose clip covered it — a frozen tail;
  * `intra_scene_chain`'s final shot gets **0.67s of footage instead of 1.67s** — 40% of the length
    the pace was designed around;
  * and nothing printed anything, because the OUTPUT length was right.

Ceiling the purchase takes the freeze to 0%. It costs money — a 4,5,6,5 reel buys 22s instead of 20
— and that is the honest trade: what the nearest-neighbour snap saved, it spent on a frozen tail.

The rounding half of this file is the same defect class one layer down: `[round(d/parts,3)]*parts`
rounds one number and repeats the error, so a 5-second scene produced shot windows summing to 5.001
and asked ffmpeg for a frame past the end of the clip.
"""
from __future__ import annotations

import ast
import inspect
import re

import pytest

from reel.cinematic import intra_scene_chain
from reel.pacing import MAX_SHOT_S, split_for_pace
from reel.video_provider import _VEO_DURATIONS


def _snap(want: float) -> int:
    """The provider's arithmetic, extracted so the table below is readable."""
    return min([v for v in _VEO_DURATIONS if v >= want] or [max(_VEO_DURATIONS)])


def _code(obj) -> str:
    """Source with comments and docstrings REMOVED.

    Every source-scanning guard in this file explains, in prose, the exact expression it forbids —
    so a naive substring search finds the ban notice and fails. This repo has already paid for that
    once (`test_the_insert_never_calls_the_video_provider`, 2026-08-01). Read the code.
    """
    src = inspect.getsource(obj)
    src = "\n".join(line for line in src.splitlines())
    tree = ast.parse(inspect.cleandoc(src) if src.lstrip() != src else src)
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) \
                and ast.get_docstring(node):
            node.body = node.body[1:]
    return ast.unparse(tree)


# =====================================================================
# the purchase covers the cut
# =====================================================================

@pytest.mark.parametrize("want,bought", [
    (1.4, 4), (2.0, 4), (4.0, 4), (4.5, 6), (5.0, 6), (5.5, 6), (6.0, 6), (7.0, 8), (8.0, 8),
])
def test_the_clip_bought_is_never_shorter_than_the_scene(want, bought):
    assert _snap(want) == bought
    assert _snap(want) >= want


def test_the_provider_actually_ceils():
    """The arithmetic is inline in `generate`, so the guard reads the source. Nearest-neighbour is
    the exact expression that shipped the frozen tail; it must not come back."""
    import reel.video_provider as vp
    code = _code(vp.VeoProvider.generate)
    assert "if v >= duration_s" in code, "the purchase no longer ceils"
    assert "abs(v - duration_s)" not in code, "nearest-neighbour snapping is back"


def test_a_scene_longer_than_the_longest_clip_is_honest_about_it():
    """8s is the most the model sells. Asking for 9 gets 8 — the caller cannot buy coverage, and
    pretending otherwise would be the silent under-run again."""
    assert _snap(9.0) == max(_VEO_DURATIONS)
    assert _snap(9.0) < 9.0


def test_the_bill_moves_and_the_test_says_by_how_much():
    """Ceil is not free. Stating the delta here means nobody discovers it on an invoice."""
    reel = [4.0, 5.0, 6.0, 5.5]
    nearest = sum(min(_VEO_DURATIONS, key=lambda v: abs(v - w)) for w in reel)
    ceil = sum(_snap(w) for w in reel)
    assert nearest == 20 and ceil == 22
    assert ceil / nearest == pytest.approx(1.10, abs=0.01)


# =====================================================================
# a short clip is said out loud
# =====================================================================

def test_a_clip_shorter_than_its_scene_is_reported():
    """The output length was always right, which is precisely why this went unnoticed. The render
    must say the tail is frozen even though the duration checks out."""
    src = inspect.getsource(__import__("reel.compositor", fromlist=["x"]))
    assert "frozen frame" in src
    at = src.find("frozen frame")
    window = src[max(0, at - 600):at + 400]
    assert "_len < float(d)" in window, "the deficit is not actually measured"
    assert "frozen tail" in window, "the deficit never reaches the audit trail"


def test_the_short_clip_note_is_not_a_pass(tmp_path, monkeypatch):
    """Tri-state: it is an observation, not a verdict. `overall_pass: None` keeps "nobody judged
    this" from reading as "this is fine"."""
    src = inspect.getsource(__import__("reel.compositor", fromlist=["x"]))
    at = src.find('"reason": f"clip {_len:.2f}s')
    assert at > 0
    assert '"overall_pass": None' in src[max(0, at - 300):at]


# =====================================================================
# the rounding cannot walk past the end of the clip
# =====================================================================

@pytest.mark.parametrize("d", [1.5, 2.0, 3.0, 4.0, 5.0, 5.5, 6.0, 7.0, 8.0])
def test_the_shots_sum_to_exactly_the_scene(d):
    """`[round(d/parts,3)] * parts` rounded once and repeated the error — 5.0 came back as 5.001."""
    shots = split_for_pace([d])
    assert sum(shots) == pytest.approx(d, abs=1e-9)


@pytest.mark.parametrize("d", [4.0, 5.0, 5.5, 6.0, 8.0])
def test_no_trim_window_asks_for_footage_that_does_not_exist(d):
    chain = intra_scene_chain("bg", "bgc", duration_s=d, width=1080, height=1920)
    ends = [float(x) for x in re.findall(r"trim=[\d.]+:([\d.]+)", chain)]
    assert ends, f"no windows emitted for {d}"
    assert max(ends) <= d + 1e-9, f"a window ends at {max(ends)} on a {d}s scene"
    assert ends[-1] == pytest.approx(d, abs=1e-9), "the last shot does not reach the scene's end"


def _windows(chain: str) -> list[tuple[float, float]]:
    return [(float(a), float(b)) for a, b in re.findall(r"trim=([\d.]+):([\d.]+)", chain)]


def test_with_no_measured_coverage_the_windows_stay_inside_the_scene():
    """`clip_s=0` means nobody measured the clip. "We did not measure" must never become "we cut
    anyway", so the windows stay contiguous inside the scene — the pre-2026-08-06 behaviour,
    kept deliberately as the honest degrade."""
    pairs = _windows(intra_scene_chain("bg", "bgc", duration_s=5.5, width=1080, height=1920))
    for (_a, end), (start, _b) in zip(pairs, pairs[1:]):
        assert end == pytest.approx(start, abs=1e-9)


def test_a_cut_removes_time_when_the_purchase_covers_it():
    """THE OTHER HALF OF WHY THE OLD CUTS WERE INVISIBLE.

    The windows were contiguous — `trim=0:1.8` then `trim=1.8:3.6` — so the action ran straight
    through the join. Nothing was cut out, therefore nothing was cut; concatenating them back
    reproduces the original take with a zoom toggling on top.

    The surplus to jump over is already paid for: the model sells 4/6/8-second clips, so a
    2.97-second scene is billed at 4 and `-t` was throwing the rest away.
    """
    d, clip = 2.97, 4.0
    chain = intra_scene_chain("bg", "bgc", duration_s=d, width=1080, height=1920, clip_s=clip)
    pairs = _windows(chain)
    assert len(pairs) >= 2
    jumps = [start - end for (_a, end), (start, _b) in zip(pairs, pairs[1:])]
    assert all(j > 0.05 for j in jumps), f"the action runs through the cut: {jumps}"
    # the film's LENGTH is untouched — pacing is an edit decision, not a licence to shorten it
    assert sum(b - a for a, b in pairs) == pytest.approx(d, abs=2e-3)
    assert max(b for _a, b in pairs) <= clip + 1e-9


def test_no_shot_exceeds_the_pace_ceiling():
    for d in (4.0, 5.0, 6.0, 7.0, 8.0):
        assert max(split_for_pace([d])) <= MAX_SHOT_S + 1e-6


def test_the_chain_no_longer_accumulates():
    """Accumulating shot lengths re-introduces the drift the boundary rounding just removed."""
    code = _code(intra_scene_chain)
    assert "t += s" not in code
    assert "edges" in code


# =====================================================================
# the floor is guaranteed, not requested
# =====================================================================

def test_the_worked_example_agrees_with_the_rule():
    """The JSON skeleton is the last thing the model reads and the thing it copies. It showed a 3
    while the rule three hundred lines above said four is the floor."""
    from reel.creative_director import _system_prompt
    p = _system_prompt(4, "ar", "service")
    assert '"duration_s":5' in p
    assert '"duration_s":3' not in p


def test_a_director_who_ignores_the_floor_is_clamped_anyway(monkeypatch):
    """Tested at the seam where the model's answer becomes our data — a fake caller that answers
    2 seconds, which is what the model may still do however the brief is worded."""
    import reel.creative_director as cd
    from business_profile.llm.caller import Usage
    from reel.creative_director import CreativeScene, _ReelResponse, design_creative_reel

    monkeypatch.setattr(cd, "_image_parts",
                        lambda urls, *, max_images, max_side=640: ([(b"IMG", "image/jpeg")], list(urls)[:1]))

    def caller(system, user, response_model, group_name="", images=None):
        return (_ReelResponse(concept="c", language="en", scenes=[
            CreativeScene(image_index=0, veo_prompt="a", duration_s=2.0),
            CreativeScene(image_index=0, veo_prompt="b", duration_s=5.5),
            CreativeScene(image_index=0, veo_prompt="c", duration_s=30.0),
        ]), Usage(model="gemini-2.5-pro"))

    reel = design_creative_reel({"name": "B", "category": "x"}, ["https://x/a.jpg"], caller=caller)
    assert reel is not None
    assert [s.duration_s for s in reel.scenes] == [4.0, 5.5, 8.0]


# =====================================================================
# the QA judge sees the whole clip
# =====================================================================

def test_the_judge_samples_the_clip_it_was_given():
    """`frac * 4.0` sampled 0.4/2.0/3.6s of a 6-second clip and called them start/middle/end — so
    the last third, where a product vanishes, was never shown. Ceil makes 6s and 8s ordinary."""
    import reel.scene_qa as sq
    code = _code(sq._extract_frames)
    assert "frac * 4.0" not in code
    assert "frac * span" in code
    assert "_probe_duration" in code


def test_an_unmeasurable_clip_falls_back_to_the_stated_nominal():
    """A fallback that is written down is a decision; one that is inlined is an assumption."""
    import reel.scene_qa as sq
    assert "span = 4.0" in _code(sq._extract_frames)
