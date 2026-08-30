# -*- coding: utf-8 -*-
"""Every shot pulled toward ONE anchor — and the reasons it refuses to guess.

MEASURED BEFORE (2026-08-01), across 48 rendered reels on disk, per SHOT with cuts detected by
scene score and the end-card excluded: median luma spread between shots of the same film = **90 of
255**, median sigma_luma = **35**. A third of the dynamic range between one shot and the next,
inside one advertisement.

MEASURED AFTER, on a film built to be mismatched (bright warm kitchen / dark cool gym / flat studio
/ warm close-up): sigma_luma **-28%**, sigma_cast **-36%**, luma range **-32%**. A half-correction
by design — `PULL = 0.5` — because a full one makes every shot identical, which is a flat, not a
film.

The brand grade does NOT do this and was never meant to: it applies the SAME numbers to every shot,
so a warm grade over a bright kitchen and a dark gym gives a warm bright kitchen and a warm dark
gym. Convergence needs a per-shot correction.
"""
from __future__ import annotations

import statistics

import pytest

from reel.shot_match import (
    MAX_CHANNEL_SHIFT, MAX_LUMA_SHIFT, PULL, match_filter, measure,
)


def _flat(path, rgb, size=(160, 284)):
    """Flat colour with light structure, so the median is meaningful."""
    from PIL import Image
    im = Image.new("RGB", size)
    px = im.load()
    for y in range(size[1]):
        for x in range(size[0]):
            n = ((x * 7 + y * 13) % 23) - 11
            px[x, y] = tuple(max(0, min(255, c + n)) for c in rgb)
    im.save(path)
    return str(path)


# =====================================================================
# it converges the shots
# =====================================================================

def test_the_spread_between_shots_actually_falls(tmp_path):
    """The claim, measured end to end through ffmpeg rather than asserted about a formula."""
    from PIL import Image, ImageStat

    from reel.ffmpeg_tools import run_ffmpeg

    shots = [(168, 150, 126), (74, 84, 104), (128, 128, 128), (150, 132, 112)]
    frames = [_flat(tmp_path / f"s{i}.png", rgb) for i, rgb in enumerate(shots)]

    def _med(p):
        return list(ImageStat.Stat(Image.open(p).convert("RGB")).median)

    def _sigma(meds):
        return statistics.pstdev([sum(m) / 3.0 for m in meds])

    before = [_med(f) for f in frames]
    after = []
    for i, f in enumerate(frames):
        flt = match_filter(measure(frames[0], f))
        if not flt:
            after.append(_med(f))
            continue
        out = tmp_path / f"g{i}.png"
        run_ffmpeg(["-i", str(f), "-vf", flt, str(out)])
        after.append(_med(out))

    assert _sigma(after) < _sigma(before) * 0.85, (
        f"sigma_luma {_sigma(before):.2f} -> {_sigma(after):.2f}: the shots did not converge")


def test_it_corrects_by_half_not_all_the_way(tmp_path):
    """A full correction makes every shot identical. That is a flat, not a film — the director's
    intent has to survive the match."""
    assert PULL == pytest.approx(0.5)
    m = measure(_flat(tmp_path / "a.png", (140, 140, 140)),
                _flat(tmp_path / "b.png", (100, 100, 100)))
    assert m.checked
    full = 140 / 100
    assert 1.0 < m.luma_gain < full, "the correction is not a partial pull"


def test_the_anchor_shot_is_left_alone(tmp_path):
    f = _flat(tmp_path / "a.png", (130, 120, 110))
    assert match_filter(measure(f, f)) == ""


# =====================================================================
# it refuses to guess — and refusing is not the same as "no correction"
# =====================================================================

@pytest.mark.parametrize("anchor,shot,why", [
    ((120, 120, 120), (3, 3, 3), "crushed"),
    ((120, 120, 120), (252, 252, 252), "blown"),
    ((200, 200, 200), (40, 40, 40), "deliberate contrast"),
])
def test_an_unreliable_measurement_is_not_a_correction(tmp_path, anchor, shot, why):
    m = measure(_flat(tmp_path / "a.png", anchor), _flat(tmp_path / "b.png", shot))
    assert m.checked is False, why
    assert m.reason
    assert match_filter(m) == ""


def test_a_deliberate_hard_contrast_is_left_intact(tmp_path):
    """A director who wrote a bright exterior cutting to a dark interior meant it. Flattening that
    is not matching the film, it is deleting a decision."""
    m = measure(_flat(tmp_path / "a.png", (210, 210, 210)), _flat(tmp_path / "b.png", (45, 45, 45)))
    assert m.checked is False and "contrast" in m.reason


def test_a_missing_frame_says_so(tmp_path):
    assert measure(None, _flat(tmp_path / "b.png", (120, 120, 120))).checked is False
    assert measure(tmp_path / "nope.png", tmp_path / "gone.png").checked is False
    bad = tmp_path / "bad.png"
    bad.write_bytes(b"not an image")
    assert measure(bad, bad).checked is False


def test_unmeasurable_and_no_change_needed_are_different_states(tmp_path):
    """"Nothing to do" and "we could not tell" must not collapse into one answer — that collapse
    is the failure class this repo keeps finding."""
    same = _flat(tmp_path / "a.png", (120, 120, 120))
    nothing_to_do = measure(same, same)
    could_not_tell = measure(None, same)
    assert nothing_to_do.checked is True and nothing_to_do.is_identity
    assert could_not_tell.checked is False
    assert match_filter(nothing_to_do) == match_filter(could_not_tell) == ""


# =====================================================================
# the caps clamp, they do not abandon
# =====================================================================

def test_a_big_gap_is_partly_closed_rather_than_ignored(tmp_path):
    """The deliberate divergence from `poster/product_composite._harmonize`, which returns the
    image untouched past its caps because a REGISTERED BRAND COLOUR is at stake. Generated footage
    has no such colour, and the needed correction is biggest exactly when it matters most."""
    m = measure(_flat(tmp_path / "a.png", (150, 150, 150)), _flat(tmp_path / "b.png", (90, 90, 90)))
    assert m.checked is True
    assert m.capped is True
    assert m.luma_gain == pytest.approx(1.0 + MAX_LUMA_SHIFT, abs=1e-6)
    assert match_filter(m) != "", "a capped correction was abandoned instead of applied"


def test_no_single_channel_can_run_away(tmp_path):
    m = measure(_flat(tmp_path / "a.png", (120, 120, 120)),
                _flat(tmp_path / "b.png", (60, 110, 190)))
    assert m.checked
    for g in m.gains:
        assert abs(g / m.luma_gain - 1.0) <= MAX_CHANNEL_SHIFT + 1e-6


def test_the_exposure_is_divided_out_before_the_cast_is_read(tmp_path):
    """Otherwise the channel term is a second copy of the brightness difference, and a shot that is
    merely darker reads as colour-shifted."""
    m = measure(_flat(tmp_path / "a.png", (120, 120, 120)), _flat(tmp_path / "b.png", (80, 80, 80)))
    assert m.checked
    assert len(set(round(g, 4) for g in m.gains)) == 1, "a neutral shot picked up a colour cast"


# =====================================================================
# how it reaches ffmpeg
# =====================================================================

def test_the_correction_is_expressed_as_gamma(tmp_path):
    """Gain is multiplicative; `eq=brightness` is additive, and mapping a ratio onto colorbalance's
    units would be a guess. Gamma is exact and cannot clip black or white."""
    m = measure(_flat(tmp_path / "a.png", (150, 140, 130)), _flat(tmp_path / "b.png", (95, 100, 118)))
    f = match_filter(m)
    assert f.startswith("eq=gamma=")
    assert "gamma_r=" in f and "gamma_g=" in f and "gamma_b=" in f
    assert "brightness=" not in f and "colorbalance" not in f


def test_a_no_op_emits_no_filter_at_all(tmp_path):
    """The ffmpeg command string is then itself the evidence the feature fired — the direct test
    for this repo's "produced, never read" class."""
    f = _flat(tmp_path / "a.png", (120, 120, 120))
    assert match_filter(measure(f, f)) == ""


def test_the_filter_is_valid_ffmpeg(tmp_path):
    from reel.ffmpeg_tools import run_ffmpeg
    m = measure(_flat(tmp_path / "a.png", (160, 145, 125)), _flat(tmp_path / "b.png", (80, 90, 110)))
    out = tmp_path / "o.png"
    run_ffmpeg(["-i", _flat(tmp_path / "c.png", (80, 90, 110)), "-vf", match_filter(m), str(out)])
    assert out.is_file() and out.stat().st_size > 64


# =====================================================================
# how it is wired
# =====================================================================

def test_the_match_is_against_an_anchor_never_the_previous_shot():
    """A neighbour chain compounds: shot N ships corrected, but its tail — N+1's reference —
    reflects a state N no longer occupies, and the film walks one cut at a time."""
    import inspect

    import reel.compositor as comp
    src = inspect.getsource(comp.render_reel)
    at = src.find("_m = measure(")
    assert at > 0
    assert "measure(anchor_frame, _head_png)" in src[at:at + 120]
    assert "measure(prev_tail" not in src


def test_the_anchor_is_set_once():
    import inspect

    import reel.compositor as comp
    src = inspect.getsource(comp.render_reel)
    assert src.count("anchor_frame = _head_png") == 1
    assert "if anchor_frame is None:" in src


def test_the_match_runs_on_ungraded_frames():
    """Both frames come from `raw`, before the fc chain. The brand grade is identical for every
    scene, so it cannot change which shots agree; matching in graded space would only make the
    match fight the grade."""
    import inspect

    import reel.compositor as comp
    src = inspect.getsource(comp.render_reel)
    at_match, at_fc = src.find("_m = measure("), src.find("fc = (")
    assert 0 < at_match < at_fc
    assert "head_frame(raw," in src


def test_the_insert_is_not_matched():
    import inspect

    import reel.compositor as comp
    src = inspect.getsource(comp.render_reel)
    at = src.find('_match = ""')
    assert at > 0
    assert "if not insert:" in src[at:at + 80]


def test_most_shots_hitting_the_cap_is_reported():
    """A shot needing more than the cap was LIT differently, and no colour pass repairs that. If
    most of them are, the look lock did not reach the generator and this grade is hiding a
    generation problem rather than finishing a film."""
    import inspect

    import reel.compositor as comp
    src = inspect.getsource(comp.render_reel)
    assert "capped_shots" in src
    assert "were LIT" in src


def test_head_frame_produces_a_real_picture(tmp_path):
    """Not merely "it did not raise" — that is exactly what hid `tail_frame` being dead since the
    day it was written."""
    from PIL import Image, ImageStat

    from reel.continuity import head_frame
    from reel.ffmpeg_tools import run_ffmpeg
    clip = tmp_path / "c.mp4"
    run_ffmpeg(["-f", "lavfi", "-i", "testsrc=size=180x320:rate=30:duration=2",
                "-pix_fmt", "yuv420p", str(clip)])
    out = head_frame(clip, tmp_path / "h.png")
    assert out, "head_frame produced nothing from a valid clip"
    im = Image.open(out).convert("RGB")
    assert im.size == (180, 320)
    med = sum(ImageStat.Stat(im).median) / 3.0
    assert 2 < med < 253, f"the frame is blank (median {med})"


def test_head_frame_fails_quietly_on_rubbish(tmp_path):
    from reel.continuity import head_frame
    assert head_frame(tmp_path / "nope.mp4", tmp_path / "a.png") is None
    junk = tmp_path / "j.mp4"
    junk.write_bytes(b"x")
    assert head_frame(junk, tmp_path / "b.png") is None
