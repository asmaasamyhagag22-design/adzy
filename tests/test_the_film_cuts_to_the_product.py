# -*- coding: utf-8 -*-
"""The one shot an agency never asks a video model to make — so we stopped asking.

Three live runs said the same thing in the gate's own words: "the text on the product packaging
morphs into garbled junk across the frames". `reel/creative.py::_product_asset` already stopped
PASTING a pack into moving footage for the mirror-image reason ("a STATIC IMAGE CRUDELY OVERLAID").
Between those two findings sits the shot every commercial actually uses: the product alone, held,
lit, cut to. Nothing generated, nothing to hallucinate, nothing to reject.

These tests render one for real — ffmpeg and PIL are both bundled, so this is offline and costs
nothing — and pin the three things that make it an INSERT and not the Ken Burns fallback the owner
rejected: it is locked off, it is short, and it never stands in for a shot that failed.
"""
from __future__ import annotations

import inspect
import io
import subprocess

import pytest

from reel.ffmpeg_tools import ffmpeg_exe, run_ffmpeg


def _room(path, w=270, h=480):
    """A 'set': a lit wall with a counter, the kind of frame a previous clip would end on."""
    from PIL import Image, ImageDraw
    im = Image.new("RGB", (w, h))
    d = ImageDraw.Draw(im)
    for y in range(h):
        d.line([(0, y), (w, y)], fill=(200 - y // 12, 170 - y // 14, 140 - y // 16))
    d.rectangle([w // 8, int(h * 0.62), w - w // 8, int(h * 0.73)], fill=(120, 90, 60))
    im.save(path)
    return str(path)


def _packshot(path, w=760, h=1000):
    """A studio packshot: a coloured carton on a clean white ground, which is what keys.

    THE SIZE IS PART OF THE FIXTURE (2026-08-06). This was 300x400, which no real packshot in the
    corpus resembles — measured trimmed heights across Al Fouad and Nano Care run 664-1615px, and
    the insert draws the pack at 883px tall. A 400px source is a 2.2x blow-up of the one shot whose
    whole job is that the viewer can READ the name.

    It matters because `_keyed_products` now has a size floor, added after NTI — a brand with no
    packshot at all — offered a **120x120 decorative chain icon** that keyed perfectly, was
    aspect-1.00, and would have been composited as the hero product shot before the logo. A
    fixture smaller than every real product would have made that gate untestable here.
    """
    from PIL import Image, ImageDraw
    im = Image.new("RGB", (w, h), (255, 255, 255))
    d = ImageDraw.Draw(im)
    d.rectangle([w // 3, h // 6, 2 * w // 3, 5 * h // 6], fill=(20, 80, 160))
    d.rectangle([w // 3 + 10, h // 2 - 40, 2 * w // 3 - 10, h // 2 + 20], fill=(255, 255, 255))
    im.save(path)
    return str(path)


def _duration(path) -> float:
    out = subprocess.run([ffmpeg_exe(), "-hide_banner", "-i", str(path)],
                         capture_output=True, text=True).stderr or ""
    for line in out.splitlines():
        if "Duration:" in line:
            hh, mm, ss = line.split("Duration:")[1].split(",")[0].strip().split(":")
            return int(hh) * 3600 + int(mm) * 60 + float(ss)
    return 0.0


# =====================================================================
# it renders, and it renders the real label
# =====================================================================

def test_the_insert_is_a_real_clip_of_the_real_photograph(tmp_path):
    from reel.insert_shot import INSERT_S, insert_clip
    clip = insert_clip(_room(tmp_path / "bg.png"), [_packshot(tmp_path / "p.png")],
                       out_path=tmp_path / "insert.mp4", duration_s=INSERT_S,
                       width=270, height=480)
    assert clip, "no insert was produced from a keyable packshot"
    assert _duration(clip) == pytest.approx(INSERT_S, abs=0.15)


def test_the_product_is_centred_and_large_enough_to_read(tmp_path):
    """An insert is the shot where the viewer reads the name. The poster's own ceiling is 34% of
    the height because a poster's copy needs somewhere to live; an insert has no copy."""
    from PIL import Image

    from reel.insert_shot import _HEIGHT_FRAC, build_insert_frame
    assert _HEIGHT_FRAC > 0.34
    f = build_insert_frame(_room(tmp_path / "bg.png"), [_packshot(tmp_path / "p.png")],
                           out_path=tmp_path / "f.png", width=270, height=480)
    assert f
    im = Image.open(f).convert("RGB")
    w, _h = im.size
    # the pack is the only saturated blue in the frame; its column span must straddle the centre
    xs = [x for x in range(w) for y in (int(im.height * 0.45),)
          if im.getpixel((x, y))[2] > im.getpixel((x, y))[0] + 40]
    assert xs, "the product is not in the frame"
    assert abs((min(xs) + max(xs)) / 2 - w / 2) < w * 0.06, "the product is not centred"


def test_the_background_is_the_previous_shots_world_defocused(tmp_path):
    """A pack on a grey card is a catalogue photo. A pack in the room the film is already in is a
    cut — which is the entire reason the tail frame is the background."""
    from PIL import Image, ImageStat

    from reel.insert_shot import build_insert_frame
    room = _room(tmp_path / "bg.png")
    f = build_insert_frame(room, [_packshot(tmp_path / "p.png")],
                           out_path=tmp_path / "f.png", width=270, height=480)
    strip = lambda p: ImageStat.Stat(  # noqa: E731 — the counter edge, above the product
        Image.open(p).convert("L").resize((270, 480)).crop((0, 290, 40, 360))).stddev[0]
    assert strip(f) < strip(room), "the background was not defocused"


# =====================================================================
# fail-closed: no product means no beat, never an empty one
# =====================================================================

def test_no_candidate_means_no_insert(tmp_path):
    """A defocused empty room held for a second and a half is a beat that says nothing while
    looking deliberate — the "absence read as success" shape this repo keeps paying for."""
    from reel.insert_shot import insert_clip
    assert insert_clip(_room(tmp_path / "bg.png"), [], out_path=tmp_path / "x.mp4",
                       duration_s=1.4, width=270, height=480) is None


def test_an_unreadable_background_does_not_raise(tmp_path):
    from reel.insert_shot import build_insert_frame
    bad = tmp_path / "bad.png"
    bad.write_bytes(b"not an image")
    assert build_insert_frame(bad, [_packshot(tmp_path / "p.png")], out_path=tmp_path / "f.png",
                              width=270, height=480) is None


def test_a_logo_is_not_a_product():
    """MEASURED on a real poster: a transparent site logo keys beautifully and was composited at
    44% of the canvas. Ranking has to happen before keying, and it does."""
    from reel.insert_shot import product_candidates
    got = product_candidates({"visual": {"content_images": [
        "https://x/assets/Logo_colors.png", "https://x/img/serum-bottle.jpg"]}})
    assert got[0].endswith("serum-bottle.jpg")


def test_a_web_banner_keys_perfectly_and_is_still_not_a_product(tmp_path):
    """MEASURED (Al Fouad, 2026-08-01): three of its four best candidates keyed cleanly and
    composited into a beautiful insert of a homepage strip — "Solutions For Every Concern", a
    "Shop By Concern" button, six products in a row. A banner sits on flat white exactly like a
    packshot does. The SHAPE is what tells them apart: 3.8:1, 18.1:1 and 2.0:1 against a bottle's
    0.4 and a carton's 0.8."""
    from PIL import Image, ImageDraw

    from reel.insert_shot import _keyed_products
    banner = tmp_path / "banner.png"
    im = Image.new("RGB", (1200, 240), (255, 255, 255))
    ImageDraw.Draw(im).rectangle([40, 40, 1160, 200], fill=(190, 30, 45))
    im.save(banner)
    assert _keyed_products([str(banner)]) == []
    assert _keyed_products([_packshot(tmp_path / "p.png")]), "a real packshot was refused too"


def test_the_shape_test_needs_no_text_detector():
    """A proportion says the same thing about a banner in Arabic, Thai or Portuguese — D-8. An
    OCR gate here would need a language, a model and a per-render call to say less."""
    import ast

    import reel.insert_shot as m
    tree = ast.parse(inspect.getsource(m))
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.ClassDef)) and ast.get_docstring(node):
            node.body = node.body[1:]
    code = ast.unparse(tree).lower()
    for forbidden in ("ocr", "pytesseract", "caller", "arabic"):
        assert forbidden not in code, forbidden


def test_the_gate_is_deliberately_stricter_than_every_real_product():
    """The trade is stated, not stumbled into: a genuinely wide product (a bar on its side) is
    refused along with the banners. Losing one insert costs a beat; shipping a webpage in the
    middle of the film costs the reel."""
    from reel.insert_shot import _MAX_ASPECT, _MIN_ASPECT
    assert _MIN_ASPECT <= 0.4          # a tall bottle
    assert 1.0 < _MAX_ASPECT < 2.0     # a jar passes, the 2.0:1 "special offer" strip does not


def test_nothing_product_shaped_means_no_insert_at_all(tmp_path):
    from PIL import Image, ImageDraw

    from reel.insert_shot import build_insert_frame
    banner = tmp_path / "banner.png"
    im = Image.new("RGB", (1200, 240), (255, 255, 255))
    ImageDraw.Draw(im).rectangle([40, 40, 1160, 200], fill=(190, 30, 45))
    im.save(banner)
    assert build_insert_frame(_room(tmp_path / "bg.png"), [str(banner)],
                              out_path=tmp_path / "f.png", width=270, height=480) is None


def test_a_brand_with_no_photos_gets_no_insert():
    from reel.insert_shot import product_candidates
    assert product_candidates({"visual": {"content_images": []}}) == []
    assert product_candidates(None) == []


def test_the_catalogue_outranks_a_photo_of_the_shop():
    """`profile["catalog"]` comes from the brand's own PRODUCT PAGES, so its images are pictures
    of one item for sale — usually shot on white, which is what keys. `content_images` is the
    interior, the staff and the storefront."""
    from reel.insert_shot import product_candidates
    got = product_candidates({
        "catalog": [{"name": "Serum", "image_url": "https://x/p/serum.jpg"}],
        "visual": {"content_images": ["https://x/img/our-store-front.jpg"]}})
    assert got[0] == "https://x/p/serum.jpg"


def test_a_brand_with_no_catalogue_still_gets_its_photos():
    """A service brand has no product pages; the crawl's photos are all it has."""
    from reel.insert_shot import product_candidates
    got = product_candidates({"visual": {"content_images": ["https://x/img/clinic-room.jpg"]}})
    assert got == ["https://x/img/clinic-room.jpg"]


# =====================================================================
# it is an insert, not the fallback
# =====================================================================

def test_the_insert_is_locked_off():
    """The handheld shake and the intra-scene re-cut exist to rescue GENERATED footage. On a real
    packshot the shake reads as a badly pasted sticker and the re-cut destroys the only shot in
    the film whose job is to hold."""
    src = inspect.getsource(__import__("reel.compositor", fromlist=["x"]))
    assert 'handheld_shake_filter(W, H) if not insert else "null"' in src
    assert '_intra = ("" if insert else' in src


class _Rec:
    """A provider that records every scene it was asked to generate."""

    def __init__(self, name="veo"):
        self.name = name
        self.seen: list = []

    def generate(self, prompt, *, out_path, duration_s, width, height,
                 palette=None, reference_image=None):
        self.seen.append(prompt)
        from pathlib import Path as _P
        _P(out_path).write_bytes(b"\x00")
        return _P(out_path)


def _board(insert_urls, room=None):
    from reel.schemas import ReelScene, Storyboard
    return Storyboard(business_name="B", palette_hex=["#111111"], total_duration_s=6.0,
                      scenes=[ReelScene(kind="gallery", duration_s=4.0, visual_prompt="S0"),
                              ReelScene(kind="gallery", duration_s=1.4, visual_prompt="S1",
                                        seed_image_url=room,
                                        insert_product_urls=insert_urls)])


def _render(tmp_path, monkeypatch, insert_urls, primary=None):
    """Render through the real compositor with the text layer and ffmpeg stubbed."""
    from pathlib import Path as _P

    import reel.compositor as comp

    def _fake_text_seqs(storyboard, *, width, height, fps, out_dir, entrance_s=1.2,
                        include_logo=True):
        out = []
        for i in range(len(storyboard.scenes)):
            d = _P(out_dir) / f"seq{i}"
            d.mkdir(parents=True, exist_ok=True)
            out.append(str(d / "f%04d.png"))
        return out

    monkeypatch.setattr(comp, "render_text_sequences", _fake_text_seqs)
    monkeypatch.setattr(comp, "run_ffmpeg", lambda args, **kw: _P(args[-1]).write_bytes(b"\x00"))
    primary = primary or _Rec()
    # The insert's background is normally the PREVIOUS clip's last frame. Here the previous clip
    # is a one-byte stub, so the scene's own seed stands in — the same fallback a live run uses
    # when the shot before it never rendered.
    res = comp.render_reel(_board(insert_urls, room=_room(tmp_path / "room.png")),
                           provider=primary, out_path=tmp_path / "o.mp4", scale=0.25)
    return res, primary


def test_the_insert_scene_never_reaches_the_video_provider(tmp_path, monkeypatch):
    """The whole saving: that beat is not generated, so it is not billed and cannot be rejected."""
    res, primary = _render(tmp_path, monkeypatch, [_packshot(tmp_path / "p.png")])
    assert len(primary.seen) == 1, "the insert scene was sent to the video model"
    assert primary.seen[0].startswith("S0")
    assert res.fallback_used is False, "a planned insert was reported as a degraded render"


def test_a_scene_whose_insert_cannot_be_built_still_generates(tmp_path, monkeypatch):
    """No packshot means no beat to cut to — the scene must fall through to the model, not ship
    as a hole."""
    _res, primary = _render(tmp_path, monkeypatch, ["https://x/nothing-here.png"])
    assert len(primary.seen) == 2
    assert primary.seen[1].startswith("S1")


def test_an_insert_says_it_was_not_judged_rather_than_passing_silently(tmp_path, monkeypatch):
    res, _p = _render(tmp_path, monkeypatch, [_packshot(tmp_path / "p.png")])
    row = [r for r in res.scene_qa if r.get("scene") == 1]
    assert row, "the insert left no trace on the audit trail"
    assert row[0]["checked"] is False and row[0]["overall_pass"] is None
    assert "not generated" in row[0]["reason"]


def test_the_insert_never_calls_the_video_provider():
    """Code only — the module's docstring names Veo repeatedly, because explaining WHY this beat
    exists means naming the thing it replaces."""
    import ast

    import reel.insert_shot as m
    tree = ast.parse(inspect.getsource(m))
    for node in ast.walk(tree):                       # drop every docstring before reading
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.ClassDef)) and ast.get_docstring(node):
            node.body = node.body[1:]
    code = ast.unparse(tree).lower()
    # Only the video model this beat REPLACES is listed. The other provider this repo removed is
    # covered by `test_the_video_stack_is_veo_only`, which forbids naming it anywhere outside one
    # file — and naming it here to assert its absence is exactly what that guard caught.
    for forbidden in ("veo", "provider.generate", "generate_videos"):
        assert forbidden not in code, forbidden


# =====================================================================
# where it lands in the film
# =====================================================================

def test_the_film_ends_on_the_product_when_there_is_one():
    src = inspect.getsource(__import__("reel.creative", fromlist=["x"]))
    # The last beat is still a product beat — and since 2026-08-02 (Axis 1) so is ANY scene
    # the director marked `hero_product`: no generated shot may show the product at all.
    # THE RULE, read off the code that enforces it rather than off one spelling of it. Axis 1
    # settled this after three live runs: the product beat is the LAST beat, always, and a
    # mid-film `hero_product` mark is dropped rather than honoured — a diffusion model cannot
    # hold a registered label, and every attempt cost $2.40 and failed its own gate.
    assert "_hero_beat = (n - 1) if n > 1" in src, (
        "the product beat is no longer pinned to the last scene")
    assert "_is_product_beat = (idx == _hero_beat)" in src, (
        "a scene other than the hero beat can be composited as a product shot again")
    assert 'getattr(sc, "hero_product", False)' in src, (
        "the director's own product marks are no longer read at all")
    # THE INSERT COMES FROM THE CATALOGUE SELECTOR, and since 2026-08-08 it is also given the
    # product link the owner pasted (`hero_product_url`) — so this checks the CALL, not one exact
    # spelling of its arguments. The literal it used to assert broke the moment the hero link was
    # threaded through, which is a test measuring the typing rather than the behaviour.
    at = src.find("_insert = product_candidates(")
    assert at > 0, "the film no longer resolves its insert through the catalogue selector"
    assert "hero_url" in src[at:at + 160], (
        "the insert is chosen without the pasted product link — the ad can feature the wrong item")


def test_the_insert_buys_its_own_scene_back():
    """It costs no Veo seconds and is not bound by the 4-second minimum, so a 15-second film is
    three generated shots PLUS it — not three shots and nothing to cut to."""
    from reel.__main__ import _scenes_for
    assert _scenes_for(15.0, 10) == 3
    assert _scenes_for(15.0, 10, has_insert=True) == 4


def test_an_explicit_smaller_request_still_wins():
    from reel.__main__ import _scenes_for
    assert _scenes_for(15.0, 3, has_insert=True) == 3


def test_fifteen_seconds_is_the_default_length():
    """OWNER: «الريل الطبيعي 15 ثانية». 28 is not a length any platform sells."""
    src = inspect.getsource(__import__("reel.__main__", fromlist=["x"]))
    assert '"--max-seconds", type=float, default=15.0' in src


def test_the_director_is_asked_for_shots_the_runtime_can_cut():
    """Asking for 2-second scenes when the model's floor is 4 means paying for 4 and trimming —
    and it breaks the arithmetic `_scenes_for` does on the other side."""
    from reel.creative_director import _system_prompt
    p = _system_prompt(4, "ar", "service")
    assert "FOUR SECONDS IS THE FLOOR" in p
    assert "duration_s: 4-6" in p


# =====================================================================
# the continuity this depends on, which had never once worked
# =====================================================================

def test_the_tail_frame_of_a_clip_is_a_real_frame(tmp_path):
    """MEASURED 2026-08-01: `tail_frame` passed ffmpeg's own path as an argument to ffmpeg, so
    every call raised, the except returned None, and `decide_seed` read that None as "no tail to
    chain". Continuity had never chained a scene since it was written — and nothing noticed,
    because the function promises to fail quietly."""
    from PIL import Image

    from reel.continuity import tail_frame
    clip = tmp_path / "c.mp4"
    run_ffmpeg(["-f", "lavfi", "-i", "testsrc=size=180x320:rate=10:duration=1",
                "-pix_fmt", "yuv420p", str(clip)])
    out = tail_frame(clip, tmp_path / "t.png")
    assert out, "tail_frame produced nothing from a valid clip"
    assert Image.open(out).size == (180, 320)


def test_a_missing_or_broken_clip_still_costs_nothing(tmp_path):
    from reel.continuity import tail_frame
    assert tail_frame(tmp_path / "nope.mp4", tmp_path / "a.png") is None
    junk = tmp_path / "junk.mp4"
    junk.write_bytes(b"x")
    assert tail_frame(junk, tmp_path / "b.png") is None


def test_the_binary_is_never_passed_to_itself_again():
    """The permanent guard for the shape of that bug: `run_ffmpeg` prepends the executable, so a
    caller that also passes it makes ffmpeg treat its own path as the output file."""
    import reel.continuity as c
    src = inspect.getsource(c)
    assert "ffmpeg_exe(), \"-y\"" not in src
    assert "run_ffmpeg([\n            \"-sseof\"" in src or '"-sseof", "-0.2"' in src


def test_every_run_ffmpeg_caller_lets_it_prepend_the_binary():
    """The same mistake anywhere else would be just as silent."""
    import pathlib
    import re
    bad = []
    for p in pathlib.Path("reel").glob("*.py"):
        src = p.read_text(encoding="utf-8")
        for m in re.finditer(r"run_ffmpeg\(\s*\[([^\]]{0,120})", src):
            head = m.group(1).strip()
            if "ffmpeg_exe" in head.split(",")[0]:
                bad.append(f"{p.name}: {head[:60]}")
    assert not bad, bad


# =====================================================================
# the shared compositor change the poster also runs on
# =====================================================================

def test_centering_needs_the_width_only_the_compositor_knows(tmp_path):
    """A caller cannot centre through a zone: the width is decided after the asset is keyed,
    trimmed and scaled, so a fixed `zx` puts the LEFT EDGE where the caller meant the middle."""
    from PIL import Image

    from poster.product_composite import composite_product
    bg = tmp_path / "bg.png"
    Image.new("RGB", (400, 400), (180, 180, 180)).save(bg)
    buf = io.BytesIO()
    Image.open(_packshot(tmp_path / "p.png")).save(buf, "PNG")

    left = composite_product(bg, buf.getvalue(), zone=(0.5, 0.3, 0.4),
                             out_path=tmp_path / "l.png")
    mid = composite_product(bg, buf.getvalue(), zone=(0.5, 0.3, 0.4), align="center",
                            out_path=tmp_path / "m.png")
    assert left and mid

    def _span(p):
        im = Image.open(p).convert("RGB")
        xs = [x for x in range(im.width)
              if im.getpixel((x, int(im.height * 0.45)))[2] > im.getpixel(
                  (x, int(im.height * 0.45)))[0] + 40]
        return (min(xs) + max(xs)) / 2 if xs else None

    assert _span(mid) == pytest.approx(200, abs=12)
    assert _span(left) != pytest.approx(200, abs=12)


def test_the_posters_default_is_unchanged():
    """`align` is additive: the poster reserves a rectangle and lays its copy around it, so its
    zone X must keep meaning exactly what it meant."""
    from poster.product_composite import composite_product
    assert inspect.signature(composite_product).parameters["align"].default == ""
