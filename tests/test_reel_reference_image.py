"""Reel #4 — reference-image (Veo image-to-video) grounding.

Hermetic: no network, no Veo, no real ffmpeg/Chromium. Verifies the wiring that
carries a SCRAPED brand photo from the manifest -> profile -> storyboard -> the
video provider's image-to-video seed, and that it's seeded only on the brand
bookends (intro/outro).
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from scraper.schemas import ImageOfInterest, ImageRole

from business_profile.rules.from_visual import _hero_image_url
from poster.schemas import PosterBrief
from reel.schemas import ReelScene, Storyboard
from reel.storyboard import _reference_image_url, build_storyboard
from reel.art_director import build_brand_scene, build_scene_prompt, _BrandSceneResponse
from reel.video_provider import StubVideoProvider, _load_reference_image, _mime_for
from business_profile.llm.caller import MockCaller


# ---------------------------------------------------------------------
# Surfacing the hero onto the profile (business_profile/rules/from_visual)
# ---------------------------------------------------------------------

def _img(role, src, above_fold=False):
    return ImageOfInterest(role=role, src=src, page_url="https://x.com/", above_fold=above_fold)


def test_hero_prefers_above_fold_then_hero_then_og():
    m = SimpleNamespace(images_of_interest=[
        _img(ImageRole.LOGO, "https://x.com/logo.png", above_fold=True),
        _img(ImageRole.OG_IMAGE, "https://x.com/og.jpg"),
        _img(ImageRole.HERO, "https://x.com/hero-below.jpg", above_fold=False),
        _img(ImageRole.HERO, "https://x.com/hero-above.jpg", above_fold=True),
    ])
    # Above-the-fold HERO wins; the LOGO is never used as a reference photo.
    assert _hero_image_url(m) == "https://x.com/hero-above.jpg"


def test_hero_falls_back_to_og_then_none():
    only_og = SimpleNamespace(images_of_interest=[_img(ImageRole.OG_IMAGE, "https://x.com/og.jpg")])
    assert _hero_image_url(only_og) == "https://x.com/og.jpg"

    logo_only = SimpleNamespace(images_of_interest=[_img(ImageRole.LOGO, "https://x.com/logo.png")])
    assert _hero_image_url(logo_only) is None

    assert _hero_image_url(SimpleNamespace(images_of_interest=[])) is None


def test_hero_skips_non_http_src():
    m = SimpleNamespace(images_of_interest=[_img(ImageRole.HERO, "data:image/png;base64,AAAA", above_fold=True)])
    assert _hero_image_url(m) is None


# ---------------------------------------------------------------------
# Storyboard carries the photo seed (and only a photo, never a logo)
# ---------------------------------------------------------------------

def test_storyboard_seeds_reference_from_hero():
    brief = PosterBrief(business_name="Qasr Elkbabgi", headline="Authentic grills")
    profile = {"visual": {"hero_image_url": "https://x.com/hero.jpg", "logo_url": "https://x.com/logo.png"}}
    sb = build_storyboard(brief, profile=profile, caller=None, max_total_s=20.0)
    assert sb.reference_image_url == "https://x.com/hero.jpg"


def test_storyboard_reference_none_without_hero():
    # A logo present but no hero photo -> no Veo seed (logos make poor i2v seeds).
    assert _reference_image_url({"visual": {"logo_url": "https://x.com/logo.png"}}) is None
    assert _reference_image_url({"visual": {"hero_image_url": "data:image/png;base64,AA"}}) is None


# ---------------------------------------------------------------------
# Character anchor — coherence across text-to-video scenes (TrendPulse idea)
# ---------------------------------------------------------------------

def test_brand_scene_returns_scene_and_character():
    brief = PosterBrief(business_name="Digilians", category="education", headline="Grow")
    mock = MockCaller({"reel_scene": _BrandSceneResponse(
        scene="a bright Cairo tech lab", character="a young Egyptian woman in a hijab, 20s, denim jacket")})
    scene, character = build_brand_scene(brief, profile={"languages": ["ar"]}, caller=mock)
    assert scene == "a bright Cairo tech lab"
    assert "young Egyptian woman" in character
    # No caller -> (None, None), so the storyboard falls back with no anchor.
    assert build_brand_scene(brief, profile=None, caller=None) == (None, None)


def test_scene_prompt_repeats_character_anchor():
    brief = PosterBrief(business_name="Digilians", category="education", headline="Grow")
    anchor = "a young Egyptian woman in a hijab, 20s, denim jacket"
    p = build_scene_prompt("offering", brief, base_scene="a tech lab", character_anchor=anchor)
    assert "CONTINUITY" in p and anchor in p
    assert "IDENTICAL across all scenes" in p
    # Without an anchor: no continuity block (back-compatible).
    assert "CONTINUITY" not in build_scene_prompt("offering", brief, base_scene="a tech lab")


def test_scene_prompt_carries_per_run_variation():
    from poster.variation import build_variation
    brief = PosterBrief(business_name="Digilians", category="education", headline="Grow")
    v = build_variation(5)
    p = build_scene_prompt("intro", brief, base_scene="a tech lab", variation=v)
    assert v["mood"] in p                                 # the run's feel reached the scene
    # No variation -> no variation phrase (back-compatible).
    assert v["mood"] not in build_scene_prompt("intro", brief, base_scene="a tech lab")


def test_storyboard_threads_character_into_text_to_video_scenes():
    # No real photos -> text-to-video scenes; the recurring character must appear in each.
    brief = PosterBrief(business_name="Digilians", category="education",
                        headline="Graduate", offerings=["Data Science"])
    mock = MockCaller({"reel_scene": _BrandSceneResponse(
        scene="a Cairo tech lab", character="a young Egyptian man, 20s, glasses")})
    sb = build_storyboard(brief, profile={"languages": ["ar"]}, caller=mock, max_total_s=20.0)
    text_scenes = [s for s in sb.scenes if not s.seed_image_url]
    assert text_scenes                                   # no photos -> text-to-video path
    assert all("a young Egyptian man" in s.visual_prompt for s in text_scenes)
    assert _reference_image_url({}) is None


# ---------------------------------------------------------------------
# The reference-image loader (SSRF guard + local file + mime)
# ---------------------------------------------------------------------

@pytest.mark.parametrize("bad", [
    "http://localhost/x.png", "http://127.0.0.1/a.jpg",
    "http://169.254.169.254/meta", "file:///etc/passwd", "ftp://h/x.png", "junk",
])
def test_loader_blocks_ssrf_and_junk(bad):
    assert _load_reference_image(bad) is None


def test_loader_reads_local_file(tmp_path: Path):
    p = tmp_path / "hero.png"
    p.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 64)
    got = _load_reference_image(str(p))
    assert got is not None
    data, mime = got
    assert data and mime == "image/png"


def test_mime_rejects_svg():
    assert _mime_for("image/svg+xml", "x.svg") is None
    assert _mime_for("", "photo.jpg") == "image/jpeg"


def test_stub_provider_accepts_reference_image_kwarg():
    import inspect
    assert "reference_image" in inspect.signature(StubVideoProvider.generate).parameters


def test_vertical_seed_reframes_landscape_to_9x16():
    """A landscape seed is reframed to 9:16 so Veo i2v outputs full-frame (no bars)."""
    import io
    from PIL import Image
    from reel.video_provider import _to_vertical_seed
    buf = io.BytesIO()
    Image.new("RGB", (1600, 900), (180, 90, 40)).save(buf, format="JPEG")
    out, mime = _to_vertical_seed(buf.getvalue())
    w, h = Image.open(io.BytesIO(out)).size
    assert mime == "image/jpeg"
    assert abs((w / h) - (9 / 16)) < 0.01


def test_edge_bg_color_samples_the_border():
    from PIL import Image
    from reel.video_provider import _edge_bg_color
    assert _edge_bg_color(Image.new("RGB", (100, 100), (240, 240, 235))) == (240, 240, 235)


def test_vertical_seed_pad_is_seamless_and_keeps_the_whole_product(monkeypatch):
    """A single FEATURED product uses REEL_SEED_FILL=pad: CONTAIN the whole photo (nothing cropped)
    over its OWN background colour — seamless (owner: "الصورة مقصوصة" without a blurred band).
    A white studio shot pads with white, NOT a dim blurred copy."""
    import io
    from PIL import Image
    from reel.video_provider import _to_vertical_seed
    src = Image.new("RGB", (1600, 900), (255, 255, 255))          # white studio background
    for x in range(700, 900):                                     # a red product block in the middle
        for y in range(350, 550):
            src.putpixel((x, y), (200, 30, 30))
    buf = io.BytesIO(); src.save(buf, format="JPEG")

    monkeypatch.setenv("REEL_SEED_FILL", "pad")
    out, _ = _to_vertical_seed(buf.getvalue(), width=768, height=1366)
    im = Image.open(io.BytesIO(out)).convert("RGB")
    w, h = im.size
    assert abs((w / h) - (9 / 16)) < 0.01
    # the TOP padding IS the seed's own white background — near-white, NOT a dark blurred band
    assert min(im.getpixel((w // 2, 5))) > 235
    # the product survived the contain (red appears down the centre column) -> nothing cropped
    assert any(im.getpixel((w // 2, y))[0] > 150 and im.getpixel((w // 2, y))[1] < 120
               for y in range(0, h, 8))


def test_vertical_seed_blur_dims_the_band_so_pad_is_the_cleaner_default(monkeypatch):
    """Contrast: REEL_SEED_FILL=blur pads with a dimmed blurred copy (a visible band), which is
    exactly why a featured product now defaults to 'pad' instead."""
    import io
    from PIL import Image
    from reel.video_provider import _to_vertical_seed
    buf = io.BytesIO()
    Image.new("RGB", (1600, 900), (255, 255, 255)).save(buf, format="JPEG")
    monkeypatch.setenv("REEL_SEED_FILL", "blur")
    out, _ = _to_vertical_seed(buf.getvalue(), width=768, height=1366)
    im = Image.open(io.BytesIO(out)).convert("RGB")
    # blur mode dims the padding to ~0.55 brightness, so a white seed's band is clearly grey
    assert im.getpixel((im.size[0] // 2, 5))[0] < 210


# ---------------------------------------------------------------------
# Compositor seeds ONLY the brand bookends (intro + outro)
# ---------------------------------------------------------------------

class _RecordingProvider:
    name = "fake"

    def __init__(self):
        self.seen: list[tuple[str, object]] = []

    def generate(self, prompt, *, out_path, duration_s, width, height,
                 palette=None, reference_image=None):
        self.seen.append((prompt, reference_image))
        Path(out_path).write_bytes(b"\x00")
        return Path(out_path)


def test_compositor_seeds_only_intro_and_outro(tmp_path: Path, monkeypatch):
    import reel.compositor as comp

    scenes = [
        ReelScene(kind="intro", duration_s=2.0, visual_prompt="INTRO"),
        ReelScene(kind="offering", duration_s=2.0, visual_prompt="OFFER"),
        ReelScene(kind="contact", duration_s=2.0, visual_prompt="CONTACT"),
        ReelScene(kind="outro", duration_s=2.0, visual_prompt="OUTRO"),
    ]
    sb = Storyboard(
        business_name="Qasr Elkbabgi", scenes=scenes,
        reference_image_url="https://x.com/hero.jpg", palette_hex=["#8B5542"],
        total_duration_s=8.0,
    )

    # Stub out the heavy collaborators: no Chromium text render, no real ffmpeg.
    def _fake_text_seqs(storyboard, *, width, height, fps, out_dir, entrance_s=1.2,
                        include_logo=True):
        out = []
        for i in range(len(storyboard.scenes)):
            d = Path(out_dir) / f"seq{i}"
            d.mkdir(parents=True, exist_ok=True)
            out.append(str(d / "f%04d.png"))   # one image2 pattern per scene
        return out

    def _fake_run_ffmpeg(args, **kwargs):
        Path(args[-1]).write_bytes(b"\x00")  # the output path is always the last arg
        return None

    monkeypatch.setattr(comp, "render_text_sequences", _fake_text_seqs)
    monkeypatch.setattr(comp, "run_ffmpeg", _fake_run_ffmpeg)

    provider = _RecordingProvider()
    comp.render_reel(sb, provider=provider, out_path=tmp_path / "out.mp4", scale=0.25)

    # prompts carry the standing no-text clause since the calibration fix (2026-07-12)
    seeded = {prompt.split(" Absolutely no readable text")[0]: ref
              for prompt, ref in provider.seen}
    assert seeded["INTRO"] == "https://x.com/hero.jpg"
    assert seeded["OUTRO"] == "https://x.com/hero.jpg"
    assert seeded["OFFER"] is None
    assert seeded["CONTACT"] is None


# ---------------------------------------------------------------------
# Resilience: a per-scene provider failure (Veo 404/429/quota) degrades that
# scene to the fallback instead of crashing the whole reel.
# ---------------------------------------------------------------------

class _FailingProvider:
    name = "veo"

    def __init__(self):
        self.calls = 0

    def generate(self, prompt, *, out_path, duration_s, width, height,
                 palette=None, reference_image=None):
        self.calls += 1
        raise RuntimeError("404 model not found: veo-3.1-generate-001")


def _stub_compositor_io(comp, monkeypatch):
    """Stub the heavy collaborators (Chromium text render + real ffmpeg)."""
    def _fake_text_seqs(storyboard, *, width, height, fps, out_dir, entrance_s=1.2,
                        include_logo=True):
        out = []
        for i in range(len(storyboard.scenes)):
            d = Path(out_dir) / f"seq{i}"
            d.mkdir(parents=True, exist_ok=True)
            out.append(str(d / "f%04d.png"))   # one image2 pattern per scene
        return out

    def _fake_run_ffmpeg(args, **kwargs):
        Path(args[-1]).write_bytes(b"\x00")  # output path is always the last arg
        return None

    monkeypatch.setattr(comp, "render_text_sequences", _fake_text_seqs)
    monkeypatch.setattr(comp, "run_ffmpeg", _fake_run_ffmpeg)


def test_compositor_falls_back_when_primary_provider_raises(tmp_path: Path, monkeypatch):
    """When the primary provider raises on a scene, that scene is regenerated by the
    fallback and the reel still completes (no exception, fallback_used=True)."""
    import reel.compositor as comp

    scenes = [
        ReelScene(kind="intro", duration_s=2.0, visual_prompt="INTRO"),
        ReelScene(kind="offering", duration_s=2.0, visual_prompt="OFFER"),
        ReelScene(kind="outro", duration_s=2.0, visual_prompt="OUTRO"),
    ]
    sb = Storyboard(
        business_name="Qasr Elkbabgi", scenes=scenes,
        palette_hex=["#8B5542"], total_duration_s=6.0,
    )
    _stub_compositor_io(comp, monkeypatch)

    primary = _FailingProvider()
    fallback = _RecordingProvider()
    result = comp.render_reel(
        sb, provider=primary, out_path=tmp_path / "out.mp4",
        scale=0.25, fallback_provider=fallback,
    )

    # Primary was attempted for every scene; the fallback rendered every scene.
    assert primary.calls == len(scenes)
    assert {prompt.split(" Absolutely no readable text")[0]
            for prompt, _ in fallback.seen} == {"INTRO", "OFFER", "OUTRO"}
    assert result.fallback_used is True
    assert (tmp_path / "out.mp4").exists()


def test_compositor_default_fallback_is_kenburns_over_real_photos(tmp_path: Path, monkeypatch):
    """With no explicit fallback, a failing primary degrades to a KenBurns pass over the
    brand's REAL scraped photos (storyboard.content_images), not a blank gradient."""
    import reel.compositor as comp

    photos = ["https://cdn.example.com/a.jpg", "https://cdn.example.com/b.jpg"]
    sb = Storyboard(
        business_name="Qasr Elkbabgi",
        scenes=[ReelScene(kind="intro", duration_s=2.0, visual_prompt="INTRO")],
        palette_hex=["#8B5542"], content_images=photos, total_duration_s=2.0,
    )

    constructed: dict[str, object] = {}

    class _RecordingKenBurns(_RecordingProvider):
        name = "kenburns"

        def __init__(self, images=None, *, fallback_palette=None):
            super().__init__()
            constructed["images"] = images
            constructed["palette"] = fallback_palette

    monkeypatch.setattr(comp, "KenBurnsProvider", _RecordingKenBurns)
    _stub_compositor_io(comp, monkeypatch)

    result = comp.render_reel(
        sb, provider=_FailingProvider(), out_path=tmp_path / "out.mp4", scale=0.25,
    )

    assert constructed["images"] == photos          # seeded from the REAL photos
    assert constructed["palette"] == ["#8B5542"]    # brand palette for the gradient floor
    assert result.fallback_used is True


# ---------------------------------------------------------------------
# AIML gateway provider: Veo 3.1 i2v (+ native voiceover) without Vertex provisioning.
# Hermetic — submit/poll/download are mocked; no network, no AIML account.
# ---------------------------------------------------------------------

def test_aiml_provider_submit_poll_download(tmp_path: Path, monkeypatch):
    from reel.video_provider import AimlVeoProvider
    p = AimlVeoProvider(api_key="k", poll_interval=0, max_polls=5)

    submitted = {}
    def _fake_post(path, payload):
        submitted.update(payload)
        return {"id": "gen123"}
    polls = iter([
        {"status": "queued"},
        {"status": "completed", "video": {"url": "https://x.example/v.mp4"}},
    ])
    monkeypatch.setattr(p, "_post_json", _fake_post)
    monkeypatch.setattr(p, "_get_json", lambda path, params: next(polls))
    monkeypatch.setattr("scraper.url_utils.is_safe_public_url", lambda u: True)  # flow test, not the guard

    class _Resp:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self, *a): return b"MP4DATA"          # accepts the size cap arg
    monkeypatch.setattr("urllib.request.urlopen", lambda url, timeout=300: _Resp())

    out = p.generate(
        "a calm b-roll scene", out_path=tmp_path / "s.mp4", duration_s=6.0,
        width=1080, height=1920, reference_image="https://x.example/seed.jpg",
    )
    assert out == tmp_path / "s.mp4"
    assert out.read_bytes() == b"MP4DATA"
    assert submitted["model"] == "google/veo-3.1-i2v"
    assert submitted["image_url"] == "https://x.example/seed.jpg"   # http seed -> i2v


def test_aiml_provider_skips_local_seed_and_requires_key(tmp_path: Path, monkeypatch):
    from reel.video_provider import AimlVeoProvider
    # A non-http (local) seed is NOT sent as image_url (AIML needs a public URL).
    p = AimlVeoProvider(api_key="k", poll_interval=0)
    seen = {}
    monkeypatch.setattr(p, "_submit", lambda prompt, image_url: seen.setdefault("img", image_url) or "g")
    monkeypatch.setattr(p, "_poll", lambda gid: "https://x.example/v.mp4")
    monkeypatch.setattr("scraper.url_utils.is_safe_public_url", lambda u: True)  # flow test, not the guard
    monkeypatch.setattr("urllib.request.urlopen",
                        lambda url, timeout=300: type("R", (), {
                            "__enter__": lambda s: s, "__exit__": lambda *a: False,
                            "read": lambda s, *a: b"X"})())
    p.generate("x", out_path=tmp_path / "s.mp4", duration_s=6.0, width=1080, height=1920,
               reference_image="C:/local/seed.jpg")
    assert seen["img"] is None

    # No key -> a clear error (so the compositor falls back instead of a silent blank).
    monkeypatch.delenv("AIML_API_KEY", raising=False)
    with pytest.raises(RuntimeError):
        AimlVeoProvider(api_key=None).generate(
            "x", out_path=tmp_path / "s2.mp4", duration_s=6.0, width=1080, height=1920)


def test_default_video_provider_prefers_aiml(monkeypatch):
    from reel.video_provider import default_video_provider, AimlVeoProvider
    monkeypatch.delenv("REEL_FORCE_STUB", raising=False)
    monkeypatch.setenv("REEL_VIDEO_BACKEND", "aiml")
    monkeypatch.setenv("AIML_API_KEY", "k")
    assert isinstance(default_video_provider(), AimlVeoProvider)


def test_aiml_poll_accepts_a_string_video_field(monkeypatch):
    # Some gateways return `video` as a bare URL string (not a {"url":...} dict) —
    # a valid completed render must not be lost to an AttributeError.
    from reel.video_provider import AimlVeoProvider
    p = AimlVeoProvider(api_key="k", poll_interval=0, max_polls=2)
    monkeypatch.setattr(p, "_get_json",
                        lambda path, params: {"status": "completed", "video": "https://x.example/v.mp4"})
    assert p._poll("g") == "https://x.example/v.mp4"


def test_aiml_download_rejects_non_http_and_ssrf_url(tmp_path: Path, monkeypatch):
    # The gateway-returned download URL is guarded: file:// (local read) is rejected.
    from reel.video_provider import AimlVeoProvider
    p = AimlVeoProvider(api_key="k", poll_interval=0)
    monkeypatch.setattr(p, "_submit", lambda prompt, image_url: "g")
    monkeypatch.setattr(p, "_poll", lambda gid: "file:///C:/Windows/win.ini")
    with pytest.raises(RuntimeError):
        p.generate("x", out_path=tmp_path / "s.mp4", duration_s=6.0, width=1080, height=1920)


# --- default_video_provider precedence (a stale 3rd-party key must not hijack Veo) -------

_VIDEO_ENV = ("REEL_FORCE_STUB", "REEL_VIDEO_BACKEND", "RUNWAY_API_KEY", "AIML_API_KEY",
              "GEMINI_API_KEY", "GOOGLE_API_KEY", "GOOGLE_CLOUD_PROJECT")


def _clear_video_env(monkeypatch):
    for k in _VIDEO_ENV:
        monkeypatch.delenv(k, raising=False)


def test_stale_runway_key_is_fully_ignored_veo_wins(monkeypatch):
    # The real .env now carries a Kling key (the owner added it), and Kling is the default
    # video backend by her ruling — so a test asserting "Veo wins" has to clear EVERY Kling
    # variable, not just the pair. Otherwise it measures the developer's .env, not the code.
    for _k in ("KLING_API_KEY", "KLING_ACCESS_KEY", "KLING_SECRET_KEY"):
        monkeypatch.delenv(_k, raising=False)
    # Runway was REMOVED 2026-07-05 (owner directive) after a len-132 RUNWAY_API_KEY in .env
    # silently overrode Veo 3.1 and returned HTTP 400 on every scene -> Ken Burns stills. A
    # leftover RUNWAY_API_KEY must now be COMPLETELY inert: with a Vertex project present, Veo wins.
    from reel.video_provider import default_video_provider
    _clear_video_env(monkeypatch)
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "some-project")
    monkeypatch.setenv("RUNWAY_API_KEY", "x" * 132)
    assert default_video_provider().name == "veo"


def test_runway_backend_is_gone_and_falls_through_to_veo(monkeypatch):
    # The real .env now carries a Kling key (the owner added it), and Kling is the default
    # video backend by her ruling — so a test asserting "Veo wins" has to clear EVERY Kling
    # variable, not just the pair. Otherwise it measures the developer's .env, not the code.
    for _k in ("KLING_API_KEY", "KLING_ACCESS_KEY", "KLING_SECRET_KEY"):
        monkeypatch.delenv(_k, raising=False)
    # REEL_VIDEO_BACKEND=runway is no longer a selectable backend; it falls through to AUTO,
    # which picks our Vertex Veo. (No RunwayProvider exists to construct.)
    import reel.video_provider as vp
    assert not hasattr(vp, "RunwayProvider")
    _clear_video_env(monkeypatch)
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "some-project")
    monkeypatch.setenv("REEL_VIDEO_BACKEND", "runway")
    assert vp.default_video_provider().name == "veo"


def test_only_a_runway_key_and_no_vertex_is_stub_not_runway(monkeypatch):
    # The real .env now carries a Kling key (the owner added it), and Kling is the default
    # video backend by her ruling — so a test asserting "Veo wins" has to clear EVERY Kling
    # variable, not just the pair. Otherwise it measures the developer's .env, not the code.
    for _k in ("KLING_API_KEY", "KLING_ACCESS_KEY", "KLING_SECRET_KEY"):
        monkeypatch.delenv(_k, raising=False)
    # With NO Vertex/Gemini/AIML credential, a lone RUNWAY_API_KEY is NOT a fallback anymore
    # (Runway removed) — the pipeline degrades to the offline stub, never to Runway.
    from reel.video_provider import default_video_provider
    _clear_video_env(monkeypatch)
    monkeypatch.setenv("RUNWAY_API_KEY", "x" * 132)
    assert default_video_provider().name == "stub"


def test_explicit_veo_alias_selects_vertex(monkeypatch):
    from reel.video_provider import default_video_provider
    _clear_video_env(monkeypatch)
    monkeypatch.setenv("REEL_VIDEO_BACKEND", "veo")
    assert default_video_provider().name == "veo"


def test_aiml_remains_an_opt_in_backend(monkeypatch):
    # AIML (the other Veo-3.1 gateway) is untouched by the Runway removal — still opt-in.
    from reel.video_provider import default_video_provider
    _clear_video_env(monkeypatch)
    monkeypatch.setenv("REEL_VIDEO_BACKEND", "aiml")
    assert default_video_provider().name == "aiml-veo"
