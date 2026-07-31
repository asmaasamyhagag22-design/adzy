"""Reel voice-over — the non-network logic (narration extraction + backend select).
The actual Gemini/OpenAI TTS calls are live-only (out of CI)."""
from __future__ import annotations

from reel.schemas import ReelScene, Storyboard
from reel.voiceover import narration_lines, _resolve_backend


def _storyboard():
    return Storyboard(business_name="Orange Egypt", scenes=[
        ReelScene(kind="intro", duration_s=3.0, visual_prompt="x", headline="Stay Connected"),
        ReelScene(kind="offering", duration_s=3.0, visual_prompt="x", sublines=["Orange Cash"]),
        ReelScene(kind="outro", duration_s=3.0, visual_prompt="x", cta_text="Shop now"),
        ReelScene(kind="value_prop", duration_s=3.0, visual_prompt="x"),   # no text -> silent
    ])


def test_narration_lines_are_per_scene_verbatim_visible_text():
    # headline -> first subline -> CTA; empty for a scene with no text (stays aligned).
    assert narration_lines(_storyboard()) == ["Stay Connected", "Orange Cash", "Shop now", ""]


def test_textlayer_is_bottom_anchored_scrim_and_accent():
    # The redesign: a bottom-anchored caption block with a strong linear scrim, one
    # brand-accent word, a CTA chip, and the old centered-mid-screen system removed.
    from reel.textlayer import _scene_html
    sb = Storyboard(business_name="Orange Egypt", primary_dir="ltr",
                    palette_hex=["#62a3c7", "#b73b28"], primary_color="#000000")
    scene = ReelScene(kind="intro", duration_s=3.0, visual_prompt="x",
                      headline="Stay Connected", cta_text="Shop")
    html = _scene_html(scene, sb, 1080, 1920, None)
    assert "class=\"lower\"" in html and "linear-gradient(to top" in html   # bottom scrim
    # hero (intro/outro) scene -> a MINIMAL clean white lockup (no clashing accent word)
    assert "headline lockup" in html and "class=\"lw\"" in html
    assert "class=\"cta\"" in html                                           # CTA chip
    assert "border-radius:999px" in html                                     # CTA is a full pill
    assert "translateY(-50%)" not in html                                    # old centered rows gone
    # Arabic (RTL) must NOT get negative letter-spacing (it breaks glyph joining);
    # the LTR hero lockup DOES get tight tracking.
    rtl_sb = Storyboard(business_name="قصر", primary_dir="rtl",
                        palette_hex=["#512283", "#6449cd"], primary_color="#1b1340")
    rtl_html = _scene_html(ReelScene(kind="intro", duration_s=3.0, visual_prompt="x",
                                     headline="المسافات بينا"),
                           rtl_sb, 1080, 1920, None)
    assert "letter-spacing:0;" in rtl_html and "letter-spacing:-1px" not in rtl_html
    assert "letter-spacing:-1px" in html                                     # the LTR lockup is tight-tracked
    # KINETIC scaffolding: per-element animation data + the in-page __seek(t) driver
    # (the frame-sequence overlay is built by seeking this; no static single PNG).
    assert "window.__seek" in html and "data-anim" in html
    assert "translateY(-50%)" not in rtl_html                                # no centered-row hack in RTL either


def test_textlayer_kinetic_stagger_and_frame_count():
    # Per-element STAGGER (headline -> sublines -> CTA) + the entrance frame-count math.
    from reel.textlayer import _scene_html, _n_entrance_frames
    sb = Storyboard(business_name="X", primary_dir="ltr", palette_hex=["#62a3c7"])
    off = ReelScene(kind="offering", duration_s=4.0, visual_prompt="x",
                    headline="Fast Internet", sublines=["one", "two", "three"], cta_text="Go")
    html = _scene_html(off, sb, 1080, 1920, None)
    assert "window.__seek" in html and "data-anim" in html
    # the non-hero headline pops with a spring/overshoot ('back') easing
    assert 'data-ease="back"' in html
    # sublines enter on a staggered cadence: 0.300s, 0.450s, 0.600s
    assert 'data-d="0.300"' in html and 'data-d="0.450"' in html and 'data-d="0.600"' in html
    # the CTA enters LAST (~0.55s), after every subline
    assert 'data-d="0.55"' in html
    # frame-count: a 1.2s entrance at the video fps; clamped to short scenes
    assert _n_entrance_frames(4.0, 30) == 36
    assert _n_entrance_frames(0.5, 30) == 15
    assert _n_entrance_frames(3.0, 24, entrance_s=1.0) == 24
    # a NON-hero scene keeps the simpler single-line accent word (.hl)
    off = ReelScene(kind="offering", duration_s=4.0, visual_prompt="x", headline="Fast Internet")
    assert "class=\"hl\"" in _scene_html(off, sb, 1080, 1920, None)


def test_resolve_backend_explicit_env_and_auto(monkeypatch):
    # VERBATIK_API_KEY is in the real .env (the owner added it for the reel voice, 2026-07-25)
    # and auto-selection now prefers it — so this test must clear it or it measures the
    # developer's environment instead of the resolution logic. Same class as the video-backend
    # selection tests earlier today.
    monkeypatch.delenv("VERBATIK_API_KEY", raising=False)
    monkeypatch.delenv("REEL_TTS_BACKEND", raising=False)
    monkeypatch.delenv("GOOGLE_CLOUD_PROJECT", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "k")              # a paid key is present
    assert _resolve_backend("gemini") == "gemini"          # explicit wins
    assert _resolve_backend("openai") == "openai"
    assert _resolve_backend("edge") == "edge"              # free native-ar-EG backend
    assert _resolve_backend(None) == "openai"              # no GCP, has OpenAI key -> openai
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    assert _resolve_backend(None) == "edge"                # no paid backend -> free edge fallback
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "proj")
    assert _resolve_backend(None) == "gemini"              # GCP present -> gemini (auto)
    monkeypatch.setenv("REEL_TTS_BACKEND", "openai")
    assert _resolve_backend(None) == "openai"              # env override beats auto
