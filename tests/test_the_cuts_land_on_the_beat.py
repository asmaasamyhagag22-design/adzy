# -*- coding: utf-8 -*-
"""The film has a tempo now — and we did not have to license one to get it.

This pipeline had NO notion of tempo. The repo's only BPM code (`reel/motion.py::_grid_durations`)
has no production caller, and `reel/sound.py`'s docstring claimed "the cuts beat-sync to its bpm"
about a compositor that had never heard of a beat. Cuts landed wherever the copy happened to end.

The usual fix is to buy a track and detect its tempo — a vendor, a licence question for a
client-sold reel, and an octave error waiting to happen. We don't need it: when no `--music` is
given, `synth_sound_bed` SYNTHESIZES the bed we ship. We are the composer, so the tempo is a
decision.

MEASURED: the bed is rendered at an authored BPM, decoded back to PCM, and an independent onset +
autocorrelation detector reads **exactly** the authored tempo at 75, 100 and 120. That proves the
pulse is physically in the audio — not that a filter string was built, which is this repo's
recurring way of being wrong.
"""
from __future__ import annotations

import inspect

import pytest

from reel.tempo import _BPM, BeatGrid, beat_grid_for_brand, look_family, quantise

CLINICAL = {"name": "Nano Care", "category": "pharmacy",
            "description": "clinical, lab-tested, dermatologist approved supplements"}
ORGANIC = {"name": "Abu Auf", "category": "grocery",
           "description": "natural organic nuts and dried fruit, warm family recipes"}


# =====================================================================
# the grid itself
# =====================================================================

@pytest.mark.parametrize("family,bpm", sorted(_BPM.items()))
def test_a_beat_is_a_whole_number_of_frames(family, bpm):
    """This is what makes "on the beat" and "on the frame" the SAME lattice. Any other tempo puts
    cuts between frames and leaves a residual nobody can remove."""
    frames = 30.0 * 60.0 / bpm
    assert abs(frames - round(frames)) < 1e-9, f"{family} at {bpm} BPM = {frames} frames per beat"


def test_the_tempo_is_derived_from_the_same_resolver_as_the_look_and_the_grade():
    """Three consumers, one resolution. If they disagreed, a film would be shot one way, graded
    another and cut to a third."""
    from reel.grade import grade_for_brand
    for prof in (CLINICAL, ORGANIC):
        assert look_family(profile=prof) == grade_for_brand(profile=prof)["family"]


def test_two_brands_get_two_tempos():
    a, b = beat_grid_for_brand(profile=CLINICAL), beat_grid_for_brand(profile=ORGANIC)
    assert a.bpm != b.bpm
    assert a.frame_exact and b.frame_exact


def test_a_luxury_brand_is_slower_than_a_punchy_one():
    assert _BPM["luxury"] < _BPM["warm"] < _BPM["clinical"] < _BPM["vibrant"]


def test_no_country_or_language_decides_the_tempo():
    """D-8. The tempo is a property of what the brand IS."""
    import ast
    import reel.tempo as t
    tree = ast.parse(inspect.getsource(t))
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.ClassDef)) and ast.get_docstring(node):
            node.body = node.body[1:]
    code = ast.unparse(tree).lower()
    for banned in ("country", "egypt", "arabic", "lang", "category"):
        assert banned not in code, banned


def test_a_brand_with_no_evidence_still_gets_a_real_grid():
    g = beat_grid_for_brand(profile={})
    assert g.bpm > 0 and g.beat_s > 0 and g.frame_exact


def test_an_unusual_fps_is_reported_rather_than_rounded_away():
    g = beat_grid_for_brand(profile=ORGANIC, fps=25)
    if not g.frame_exact:
        assert "not frame-exact" in g.reason


# =====================================================================
# quantisation: the floors win, and the error cannot accumulate
# =====================================================================

def _starts(durations):
    out, acc = [0.0], 0.0
    for d in durations:
        acc += d
        out.append(acc)
    return out


def test_every_cut_lands_on_a_beat():
    g = beat_grid_for_brand(profile=ORGANIC)
    q = quantise([4.0, 5.0, 6.0, 5.5], [4.0, 4.0, 4.0, 1.4], g.beat_s)
    for s in _starts(q):
        k = s / g.beat_s
        assert abs(k - round(k)) < 1e-9, f"cut at {s:.4f}s is {abs(k - round(k)) * g.beat_s:.4f}s off"


def test_the_error_does_not_accumulate_over_a_long_film():
    """Rounding N independent lengths compounds — the identical defect `reel/pacing.py` was just
    fixed for. Snapping cumulative STARTS makes it impossible."""
    g = beat_grid_for_brand(profile=CLINICAL)
    q = quantise([4.3] * 12, [4.0] * 12, g.beat_s)
    last = _starts(q)[-1] / g.beat_s
    assert abs(last - round(last)) < 1e-9


@pytest.mark.parametrize("floor", [4.0, 5.5, 7.25])
def test_a_floor_is_never_broken(floor):
    """The floors are legibility contracts the owner rejected a reel over twice. The grid may only
    round UP against them."""
    g = beat_grid_for_brand(profile=ORGANIC)
    q = quantise([4.0, 4.0, 4.0], [floor] * 3, g.beat_s)
    assert all(d >= floor - 1e-9 for d in q), q


def test_the_grid_only_lengthens_a_scene_it_cannot_fit():
    """Snapping DOWN would clip a caption mid-read to buy a rhythm nobody asked for."""
    g = beat_grid_for_brand(profile=ORGANIC)
    want = [4.05, 4.05, 4.05]
    q = quantise(want, want, g.beat_s)
    assert all(a >= b - 1e-9 for a, b in zip(q, want))


def test_no_scene_is_quantised_to_nothing():
    g = beat_grid_for_brand(profile=ORGANIC)
    assert all(d > 0 for d in quantise([0.1, 0.1], [0.0, 0.0], g.beat_s))


def test_no_grid_means_the_durations_are_returned_untouched():
    """Never a guessed tempo. An unquantised cut over music reads as a normal edit; a cut 200ms
    off a strong beat reads as a mistake."""
    assert quantise([4.0, 5.0], [4.0, 4.0], 0.0) == [4.0, 5.0]
    assert quantise([], [], 0.6) == []


def test_the_lengthening_is_bounded_by_one_beat_per_cut():
    g = beat_grid_for_brand(profile=ORGANIC)
    want = [4.1, 5.2, 6.3, 5.4]
    q = quantise(want, [4.0] * 4, g.beat_s)
    assert sum(q) - sum(want) < g.beat_s * len(want) + 1e-9


# =====================================================================
# the pulse is really in the audio
# =====================================================================

def _detect_bpm(path):
    """An independent reading of the rendered file — onset envelope + autocorrelation."""
    import subprocess

    import numpy as np

    from reel.ffmpeg_tools import ffmpeg_exe
    sr, hop = 8000, 80
    r = subprocess.run([ffmpeg_exe(), "-hide_banner", "-v", "error", "-i", str(path),
                        "-ac", "1", "-ar", str(sr), "-f", "s16le", "-"], capture_output=True)
    x = np.frombuffer(r.stdout, "<i2").astype(np.float32) / 32768.0
    if x.size < sr * 4:
        return None
    env = np.array([np.abs(x[i:i + hop]).mean() for i in range(0, len(x) - hop, hop)])
    env = np.maximum(0.0, env - np.convolve(env, np.ones(30) / 30, mode="same"))
    env -= env.mean()
    ac = np.correlate(env, env, "full")[len(env) - 1:]
    fps_env = sr / hop
    lo, hi = int(fps_env * 60 / 180), int(fps_env * 60 / 60)
    return 60.0 * fps_env / (lo + int(np.argmax(ac[lo:hi])))


@pytest.mark.parametrize("bpm", [75.0, 100.0, 120.0])
def test_the_authored_tempo_is_readable_back_out_of_the_bed(tmp_path, bpm):
    """The antidote to "produced, never read": render it, decode it, measure it."""
    from reel.sound import synth_sound_bed
    bed = synth_sound_bed(tmp_path / "b.m4a", 12.0, cut_times=[4.0, 8.0], mood="warm",
                          bpm=bpm, real_sfx=False)
    assert bed, "no bed was produced"
    got = _detect_bpm(bed)
    assert got is not None
    assert abs(got - bpm) < 1.0, f"authored {bpm}, measured {got:.1f}"


def test_no_bpm_reproduces_the_old_bed(tmp_path, monkeypatch):
    """`bpm=None` must be the previous behaviour exactly — the change has to be reversible under
    demo pressure.

    Asserted on the ffmpeg INVOCATION, not on the file: two renders with identical parameters
    already differ in their bytes (the AAC encoder and the container stamp are not deterministic),
    so a byte comparison would fail for reasons that have nothing to do with this change. The
    command is the thing this feature actually alters.
    """
    import reel.sound as s
    seen: list[list[str]] = []
    real = s.run_ffmpeg

    def _spy(args, **kw):
        seen.append([str(a) for a in args])
        return real(args, **kw)

    monkeypatch.setattr(s, "run_ffmpeg", _spy)
    s.synth_sound_bed(tmp_path / "a.m4a", 8.0, cut_times=[4.0], mood="warm", real_sfx=False)
    s.synth_sound_bed(tmp_path / "b.m4a", 8.0, cut_times=[4.0], mood="warm", real_sfx=False,
                      bpm=None)
    assert len(seen) == 2
    strip = lambda a: [x for x in a if not x.endswith(".m4a")]  # noqa: E731 — the out path differs
    assert strip(seen[0]) == strip(seen[1])
    graph = seen[0][seen[0].index("-filter_complex") + 1]
    assert "[puls]" not in graph and "[bar]" not in graph


def test_a_bpm_adds_the_pulse_and_nothing_else(tmp_path, monkeypatch):
    import reel.sound as s
    seen: list[list[str]] = []
    real = s.run_ffmpeg

    def _spy(args, **kw):
        seen.append([str(a) for a in args])
        return real(args, **kw)

    monkeypatch.setattr(s, "run_ffmpeg", _spy)
    s.synth_sound_bed(tmp_path / "a.m4a", 8.0, cut_times=[4.0], mood="warm", real_sfx=False)
    s.synth_sound_bed(tmp_path / "b.m4a", 8.0, cut_times=[4.0], mood="warm", real_sfx=False,
                      bpm=100.0)
    # the GRAPH only — pytest's tmp_path embeds this test's own name, which contains "pulse"
    def _graph(args):
        return args[args.index("-filter_complex") + 1]

    plain, pulsed = _graph(seen[0]), _graph(seen[1])
    assert "[puls]" not in plain and "[bar]" not in plain
    assert "[puls]" in pulsed and "[bar]" in pulsed
    assert "mod(t" in " ".join(seen[1])


def test_the_pulse_is_one_input_not_one_per_beat():
    """A 20s reel at 100 BPM is 33 beats; 33 extra lavfi inputs would widen an amix that is
    already pad + impact + cuts + sfx, and push the Windows command line toward its limit."""
    import ast
    import reel.sound as s
    tree = ast.parse(inspect.getsource(s.synth_sound_bed))
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.Module)) and ast.get_docstring(node):
            node.body = node.body[1:]
    code = ast.unparse(tree)
    assert "mod(t" in code, "the pulse is not a periodic expression"
    assert code.count("[puls]") <= 2 and code.count("[bar]") <= 2


# =====================================================================
# it is the LAST thing that touches a duration
# =====================================================================

def test_the_grid_is_applied_after_the_dead_tail_fit():
    """The fit multiplies every scene by a scalar, and a scaled beat multiple is not a beat
    multiple. Quantising before it would be silently undone."""
    import reel.creative as c
    src = inspect.getsource(c.render_creative_reel)
    at_fit, at_grid = src.find("narration_fit("), src.find("fit_to_runtime(_before, _floors")
    assert at_fit > 0 and at_grid > at_fit


def test_nothing_rescales_a_duration_after_the_grid():
    import reel.creative as c
    src = inspect.getsource(c.render_creative_reel)
    tail = src[src.find("fit_to_runtime(_before, _floors"):]
    assert "duration_s * " not in tail
    assert "* scale" not in tail


def test_the_bed_is_given_the_same_tempo_the_cuts_were_snapped_to():
    import reel.creative as c
    src = inspect.getsource(c.render_creative_reel)
    at = src.find("synth_sound_bed(")
    assert at > 0
    assert "bpm=(_beat.bpm if _beat else None)" in src[at:at + 500]


def test_a_failure_to_build_the_grid_says_so_and_ships_unquantised():
    import reel.creative as c
    src = inspect.getsource(c.render_creative_reel)
    assert "cuts NOT quantised" in src


def test_the_grid_does_not_undo_the_dead_tail_fit():
    """CAUGHT BY A TEST, 2026-08-01: the first version used the video model's 4-second minimum as
    a CUT floor. That minimum governs what we BUY — `video_provider.py` ceils to 4/6/8 — and the
    separation is the whole point of buying coverage. Coupling them pushed a film correctly
    shortened to 8.8s to match its narration back out to 12.6s, i.e. four seconds of silence at
    the end. The floors here are legibility only."""
    import ast

    import reel.creative as c
    tree = ast.parse(inspect.getsource(c.render_creative_reel))
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.Module)) and ast.get_docstring(node):
            node.body = node.body[1:]
    code = ast.unparse(tree)
    at = code.find("_floors.append")
    assert at > 0
    window = code[at:at + 400]
    assert "4.0" not in window, "the Veo purchase minimum is being used as a cut floor again"


def test_a_short_narrated_scene_keeps_its_length():
    """The dead-tail fit's output must survive quantisation — up to one beat, never four seconds."""
    g = beat_grid_for_brand(profile=CLINICAL)
    q = quantise([2.9, 2.9, 2.9], [1.0, 1.0, 1.0], g.beat_s)
    assert sum(q) < 3 * 2.9 + 3 * g.beat_s
    assert max(q) < 4.0


def test_the_grid_is_deterministic():
    a = beat_grid_for_brand(profile=CLINICAL)
    b = beat_grid_for_brand(profile=CLINICAL)
    assert (a.bpm, a.beat_s, a.family) == (b.bpm, b.beat_s, b.family)
    assert isinstance(a, BeatGrid)
