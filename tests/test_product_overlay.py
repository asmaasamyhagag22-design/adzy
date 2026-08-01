"""Real product-on-screen compositing (reel/product_overlay.py + storyboard + compositor wiring) —
overhaul Phase D. A software product's generated scene keeps its abstract screen; the brand's REAL
screenshot/photo is composited on top so the product is actually SHOWN. Hermetic: run_ffmpeg + the
Chromium text render are stubbed; no real encode."""
from __future__ import annotations

from pathlib import Path

import reel.product_overlay as po
from poster.schemas import PosterBrief
from reel.creative import build_creative_storyboard
from reel.creative_director import CreativeReel, CreativeScene
from reel.schemas import ReelScene, Storyboard


def test_default_box_is_a_lower_inset():
    x, y, w, h = po._default_box(1080, 1920)
    assert 0 < w < 1080 and x == (1080 - w) // 2        # horizontally centred
    assert y > 1920 * 0.3                                # sits in the lower two-thirds (headroom above)


def test_composite_builds_overlay_graph(monkeypatch, tmp_path):
    clip = tmp_path / "c.mp4"; clip.write_bytes(b"v")
    asset = tmp_path / "a.png"; asset.write_bytes(b"i")
    cap: dict = {}
    monkeypatch.setattr(po, "run_ffmpeg", lambda a: cap.__setitem__("a", a) or (tmp_path / "o.mp4").write_bytes(b"o"))
    out = po.composite_product_screen(clip, asset, tmp_path / "o.mp4", width=1080, height=1920)
    g = " ".join(cap["a"])
    assert "overlay=" in g and "scale=" in g and "-loop" in g and "-shortest" in g
    assert "0:a?" in g                                   # source audio copied through
    assert out == tmp_path / "o.mp4"


def test_composite_returns_original_clip_on_missing_asset(tmp_path):
    clip = tmp_path / "c.mp4"; clip.write_bytes(b"v")
    assert po.composite_product_screen(clip, tmp_path / "nope.png", tmp_path / "o.mp4") == clip


def test_composite_returns_original_on_ffmpeg_failure(monkeypatch, tmp_path):
    clip = tmp_path / "c.mp4"; clip.write_bytes(b"v")
    asset = tmp_path / "a.png"; asset.write_bytes(b"i")
    monkeypatch.setattr(po, "run_ffmpeg", lambda a: (_ for _ in ()).throw(RuntimeError("ffmpeg boom")))
    assert po.composite_product_screen(clip, asset, tmp_path / "o.mp4") == clip   # never loses the clip


def test_storyboard_maps_the_product_asset():
    plan = CreativeReel(
        concept="c", hook="h", cta="x", language="en", images=["http://img/0.jpg", "http://img/1.jpg"],
        scenes=[
            CreativeScene(image_index=0, veo_prompt="A", hero_product=True, product_overlay="real_screen"),
            CreativeScene(image_index=1, veo_prompt="B", hero_product=True, product_overlay="real_photo"),
            CreativeScene(image_index=0, veo_prompt="C"),                       # not a hero product
        ])
    prof = {"visual": {"homepage_screenshot_path": "/tmp/shot.png"}}
    sb = build_creative_storyboard(plan, PosterBrief(business_name="NTI", headline="NTI"), profile=prof)
    assert sb.scenes[0].product_asset_url == "/tmp/shot.png"       # real_screen -> brand screenshot
    # real_photo NO LONGER COMPOSITES (2026-08-01). The owner's production rule splits by use —
    # a poster is zoomed into and read, a two-second clip is not — so video GENERATES its product
    # and only a screen is still pasted (an app UI is dense fine print that IS the product, and no
    # video model renders legible UI). Measured on the live Veo run that forced it: the pasted
    # pack drew "a STATIC IMAGE CRUDELY OVERLAID on top of unrelated footage" from QA, burned
    # three $1.60 renders, and starved three later scenes of any clip at all.
    assert sb.scenes[1].product_asset_url is None                  # real_photo -> generated
    assert sb.scenes[2].product_asset_url is None                  # no hero -> no composite


# --- compositor wiring (mirrors test_reel_reference_image's stub pattern) ---------------------

class _RecordingProvider:
    name = "fake"

    def generate(self, prompt, *, out_path, duration_s, width, height, palette=None, reference_image=None):
        Path(out_path).write_bytes(b"\x00")
        return Path(out_path)


def _stub_compositor_io(comp, monkeypatch):
    def _fake_text_seqs(storyboard, *, width, height, fps, out_dir, entrance_s=1.2, include_logo=True):
        out = []
        for i in range(len(storyboard.scenes)):
            d = Path(out_dir) / f"seq{i}"
            d.mkdir(parents=True, exist_ok=True)
            out.append(str(d / "f%04d.png"))
        return out
    monkeypatch.setattr(comp, "render_text_sequences", _fake_text_seqs)
    monkeypatch.setattr(comp, "run_ffmpeg", lambda args, **kw: Path(args[-1]).write_bytes(b"\x00"))


def test_compositor_composites_only_scenes_with_an_asset(tmp_path, monkeypatch):
    import reel.compositor as comp
    asset = tmp_path / "screen.png"
    asset.write_bytes(b"PNG")
    scenes = [
        ReelScene(kind="intro", duration_s=2.0, visual_prompt="INTRO", product_asset_url=str(asset)),
        ReelScene(kind="offering", duration_s=2.0, visual_prompt="OFFER"),        # no asset
        ReelScene(kind="outro", duration_s=2.0, visual_prompt="OUTRO"),
    ]
    sb = Storyboard(business_name="NTI", scenes=scenes, palette_hex=["#8B5542"], total_duration_s=6.0)
    _stub_compositor_io(comp, monkeypatch)
    calls: list = []

    def _fake_composite(clip, a, out, **kw):
        calls.append(str(a))
        Path(out).write_bytes(b"\x00")
        return Path(out)
    monkeypatch.setattr(po, "composite_product_screen", _fake_composite)

    comp.render_reel(sb, provider=_RecordingProvider(), out_path=tmp_path / "o.mp4", scale=0.25)
    assert calls == [str(asset)]                          # composited the one scene with a real asset, only
