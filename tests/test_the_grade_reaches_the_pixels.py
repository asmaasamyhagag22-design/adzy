# -*- coding: utf-8 -*-
"""`reel/grade.py` was complete, tested, and imported by nothing.

Its own docstring recorded the measurement that motivated it — "`grep -rn "lut|curves|colorbalance|
eq=" reel/` found no grading at all" — and then it shipped without a single production caller, so
the grep stayed true. Clips were muxed exactly as each generation returned them.

MEASURED 2026-08-01 across 48 rendered reels on disk, per SHOT (cuts detected by scene score, the
end-card excluded because it is a deliberate flat plate): median luma spread between shots of the
same film = **90 of 255**, median sigma_luma = **35**. A third of the dynamic range, between one
shot and the next, inside one advertisement.

The old tests proved `grade_filter()` returns a good string. These prove the string reaches ffmpeg,
lands on the footage and not the captions, is skipped for the product insert, and changes pixels.
"""
from __future__ import annotations

import inspect
from pathlib import Path

import pytest

CLINICAL = {"name": "Nano Care", "category": "pharmacy",
            "description": "clinical, lab-tested, dermatologist approved supplements"}
ORGANIC = {"name": "Abu Auf", "category": "grocery",
           "description": "natural organic nuts and dried fruit, warm family recipes"}


class _Rec:
    name = "veo"

    def generate(self, prompt, *, out_path, duration_s, width, height,
                 palette=None, reference_image=None):
        from reel.ffmpeg_tools import run_ffmpeg
        run_ffmpeg(["-f", "lavfi",
                    "-i", f"testsrc=size={width}x{height}:rate=30:duration={max(4, duration_s)}",
                    "-pix_fmt", "yuv420p", str(out_path)])
        return Path(out_path)


def _render(tmp_path, monkeypatch, *, grade, insert_urls=None):
    """Render two scenes and return (result, every ffmpeg command issued)."""
    from PIL import Image

    import reel.compositor as comp
    from reel.schemas import ReelScene, Storyboard

    cmds: list[str] = []
    real = comp.run_ffmpeg

    def _spy(args, **kw):
        cmds.append(" ".join(str(a) for a in args))
        return real(args, **kw)

    def _seqs(storyboard, *, width, height, fps, out_dir, entrance_s=1.2, include_logo=True):
        out = []
        for i in range(len(storyboard.scenes)):
            d = Path(out_dir) / f"seq{i}"
            d.mkdir(parents=True, exist_ok=True)
            Image.new("RGBA", (width, height), (0, 0, 0, 0)).save(d / "f0000.png")
            out.append(str(d / "f%04d.png"))
        return out

    monkeypatch.setattr(comp, "run_ffmpeg", _spy)
    monkeypatch.setattr(comp, "render_text_sequences", _seqs)
    sb = Storyboard(business_name="B", palette_hex=["#111111"], total_duration_s=8.0,
                    scenes=[ReelScene(kind="gallery", duration_s=4.0, visual_prompt="S0"),
                            ReelScene(kind="gallery", duration_s=4.0, visual_prompt="S1",
                                      insert_product_urls=insert_urls or [])])
    res = comp.render_reel(sb, provider=_Rec(), out_path=tmp_path / "o.mp4", scale=0.25,
                           include_logo=False, grade=grade)
    return res, cmds


def _scene_cmds(cmds):
    return [c for c in cmds if "filter_complex" in c and "overlay=" in c]


# =====================================================================
# it reaches ffmpeg at all
# =====================================================================

def test_the_grade_lands_in_the_filter_chain(tmp_path, monkeypatch):
    from reel.grade import grade_filter
    g = grade_filter(profile=CLINICAL)
    _res, cmds = _render(tmp_path, monkeypatch, grade=g)
    scene = _scene_cmds(cmds)
    assert scene, "no per-scene filter chain was issued"
    assert all("eq=contrast=" in c and "colorbalance=" in c for c in scene)


def test_no_grade_means_no_filter_rather_than_a_neutral_one(tmp_path, monkeypatch):
    """A neutral grade still costs a filter pass and still says "we graded this" in the log."""
    _res, cmds = _render(tmp_path, monkeypatch, grade="")
    assert not any("eq=contrast=" in c for c in _scene_cmds(cmds))


def test_the_grade_is_applied_to_the_footage_not_the_captions(tmp_path, monkeypatch):
    """Captions carry the brand's REGISTERED colours. The poster pipeline caps its product
    harmonisation at 6% per channel to protect exactly those; shifting them here would undo it."""
    from reel.grade import grade_filter
    _res, cmds = _render(tmp_path, monkeypatch, grade=grade_filter(profile=CLINICAL))
    for c in _scene_cmds(cmds):
        fc = c.split("-filter_complex ", 1)[1]
        at_grade, at_text = fc.find("eq=contrast="), fc.find("[1:v]")
        assert at_grade > 0 and at_text > 0
        assert at_grade < at_text, "the grade is applied at or after the caption layer"


def test_the_product_insert_is_not_graded(tmp_path, monkeypatch):
    """It is the brand's real photograph, already light-matched to its plate under tight caps.
    Grading it moves a registered colour a second time, with no cap at all."""
    from PIL import Image, ImageDraw

    from reel.grade import grade_filter
    shot = tmp_path / "p.png"
    # A REAL packshot size (2026-08-06). This was 300x400, which no product in the corpus
    # resembles — measured trimmed heights run 664-1615px — and `insert_shot` now has a size floor
    # after a 120x120 decorative icon nearly shipped as a hero product shot. Below the floor no
    # insert is built at all, the beat generates instead, and this test's subject disappears.
    im = Image.new("RGB", (760, 1000), (255, 255, 255))
    ImageDraw.Draw(im).rectangle([250, 150, 510, 850], fill=(20, 80, 160))
    im.save(shot)

    _res, cmds = _render(tmp_path, monkeypatch, grade=grade_filter(profile=CLINICAL),
                         insert_urls=[str(shot)])
    scene = _scene_cmds(cmds)
    assert len(scene) == 2
    assert "eq=contrast=" in scene[0], "the generated scene lost its grade"
    assert "eq=contrast=" not in scene[1], "the product insert was graded"


# =====================================================================
# it changes pixels, and differently per brand
# =====================================================================

def test_the_grade_actually_changes_the_picture(tmp_path, monkeypatch):
    """The whole point of the file this replaces: a filter string that is built and never applied
    looks identical to one that is applied."""
    import subprocess
    import tempfile

    from PIL import Image, ImageStat

    from reel.ffmpeg_tools import ffmpeg_exe
    from reel.grade import grade_filter

    def _median(video):
        with tempfile.TemporaryDirectory() as td:
            f = Path(td) / "f.png"
            subprocess.run([ffmpeg_exe(), "-hide_banner", "-y", "-ss", "1.0", "-i", str(video),
                            "-frames:v", "1", str(f)], capture_output=True)
            return ImageStat.Stat(Image.open(f).convert("RGB")).median

    plain, _c = _render(tmp_path / "a", monkeypatch, grade="")
    warm, _c = _render(tmp_path / "b", monkeypatch, grade=grade_filter(profile=ORGANIC))
    m0, m1 = _median(plain.reel_path), _median(warm.reel_path)
    assert m0 != m1, "the grade was in the command and changed nothing"


def test_two_brands_get_two_different_grades():
    from reel.grade import grade_filter
    assert grade_filter(profile=CLINICAL) != grade_filter(profile=ORGANIC)


def test_a_cool_brand_is_not_pushed_golden():
    """The failure `grade.py` exists to prevent: one house style over every brand."""
    from reel.grade import grade_for_brand
    assert grade_for_brand(profile=CLINICAL)["temperature"] < 0
    assert grade_for_brand(profile=ORGANIC)["temperature"] > 0


# =====================================================================
# the wiring, and the resolver it must agree with
# =====================================================================

def test_the_grade_is_resolved_beside_the_look_from_the_same_evidence():
    """`grade.py`: "if the two ever disagreed, a film would be shot one way and graded another."
    So both come off the same `_dna` and the same `profile`, in the same block, once per run."""
    import reel.creative as c
    src = inspect.getsource(c.render_creative_reel)
    at_look, at_grade = src.find("look_lock(brand_dna=_dna"), src.find("grade_filter(brand_dna=_dna")
    assert at_look > 0 and at_grade > at_look
    assert at_grade - at_look < 1200, "the look and the grade drifted apart in the source"


def test_the_grade_reaches_render_reel():
    """SCANS THE WHOLE CALL, not a fixed number of characters after it.

    This read `src[at:at + 900]`, and on 2026-08-06 the call grew past 900 characters when
    `identity_refs=` was added with its reasoning above it — so the test failed while the grade was
    still being passed one line further down. A window measured in characters is a guess about
    formatting; the call's own closing paren is not.
    """
    import reel.creative as c
    src = inspect.getsource(c.render_creative_reel)
    at = src.find("result = render_reel(")
    assert at > 0
    end = src.find("\n    return", at)
    call = src[at:end if end > at else len(src)]
    assert "grade=" in call, "the grade is computed and never passed"


def test_the_compositor_takes_a_grade_and_defaults_to_none():
    import reel.compositor as comp
    p = inspect.signature(comp.render_reel).parameters["grade"]
    assert p.default == "", "a default grade would change every existing caller's output"


@pytest.mark.parametrize("prof", [CLINICAL, ORGANIC])
def test_the_grade_is_deterministic(prof):
    from reel.grade import grade_filter
    assert grade_filter(profile=prof) == grade_filter(profile=prof)
