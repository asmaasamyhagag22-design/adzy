"""Reel caption DESIGN + pacing + voice (owner: 'the reel text is undesigned, too fast, unlistenable').

Hermetic — no Chromium/ffmpeg/TTS. Covers the routing that unlocks the designed caption styles,
the reading-speed durations, the readability-armor CSS, and the tone-driven edge voice.
"""
from __future__ import annotations

from poster.schemas import PosterBrief
from reel.creative import build_creative_storyboard
from reel.creative_director import CreativeReel, CreativeScene
from reel.schemas import ReelScene, Storyboard
from reel.textlayer import _scene_html


def _reel(caps, durs=None):
    durs = durs or [2.0] * len(caps)
    return CreativeReel(
        concept="c", hook="h", cta="Apply now", language="en",
        images=["https://x/0.jpg"],
        scenes=[CreativeScene(image_index=0, veo_prompt=f"scene {i}", on_screen_text=c, duration_s=d)
                for i, (c, d) in enumerate(zip(caps, durs))],
    )


def test_creative_captions_routed_to_designed_styles_and_readable_durations():
    reel = _reel(["ITI Institute", "85% get hired", "", "Apply to ITI now"])
    # captions default OFF since the owner reversal (2026-07-12) — this test pins the
    # PRESERVED routing behind the flag, not the default.
    sb = build_creative_storyboard(reel, PosterBrief(business_name="ITI", headline="ITI"),
                                   captions=True)
    s = sb.scenes
    # scene 0 caption -> intro HEADLINE (unlocks the hero lockup + logo), not a plain subline
    assert s[0].kind == "intro" and s[0].headline == "ITI Institute" and not s[0].sublines
    # a MIDDLE caption -> gallery HEADLINE (the big designed .headline, not the tiny flat .item)
    assert s[1].headline == "85% get hired" and not s[1].sublines
    # an empty caption -> plain gallery, no headline
    assert (s[2].headline or "") == ""
    # last caption -> outro CTA CHIP
    assert s[-1].kind == "outro" and s[-1].cta_text == "Apply to ITI now"
    # READABLE pacing: a captioned scene now holds >= 3s (was ~2s -> 'too fast')
    for sc in s:
        if sc.headline or sc.cta_text:
            assert sc.duration_s >= 3.0


def test_gallery_caption_gets_readability_armor_and_display_type():
    sc = ReelScene(kind="gallery", duration_s=3.0, visual_prompt="x", headline="85% get hired")
    sb = Storyboard(business_name="ITI", scenes=[sc], palette_hex=["#9c3c3c"], total_duration_s=3.0)
    html = _scene_html(sc, sb, 1080, 1920, None)
    assert "paint-order:stroke fill" in html                  # the black outline armor behind the fill
    assert "-webkit-text-stroke:2px" in html
    assert 'class="headline"' in html                         # the designed display caption, not .item
    assert "85%" in html and "hired" in html                  # verbatim (last word wrapped in the accent span)
    # the plain body subline style is NOT how this caption renders
    assert '<div class="items">' not in html


def test_edge_prosody_is_tone_driven_not_flat(monkeypatch):
    import reel.voiceover as vo
    monkeypatch.delenv("REEL_TTS_EDGE_RATE", raising=False)
    monkeypatch.delenv("REEL_TTS_EDGE_PITCH", raising=False)
    assert vo._edge_prosody_for_tone("playful energetic") == ("+9%", "+4Hz")
    assert vo._edge_prosody_for_tone("luxury") == ("-4%", "-1Hz")
    assert vo._edge_prosody_for_tone("") == ("+4%", "+2Hz")   # default has LIFT, never a flat +0%/+0Hz
    # an explicit override still wins
    monkeypatch.setenv("REEL_TTS_EDGE_RATE", "+0%")
    assert vo._edge_prosody_for_tone("playful")[0] == "+0%"


def test_dark_logo_becomes_white_knockout_for_the_reel_overlay():
    # topshoes reel frame: the dark crest was invisible on dark footage. The broadcast
    # watermark rule: a DARK logo ships as its white-knockout variant (same silhouette);
    # a light logo passes through untouched.
    import io as _io
    from PIL import Image
    from reel.textlayer import _knockout_if_dark

    def _logo(color):
        im = Image.new("RGBA", (200, 80), (0, 0, 0, 0))
        for x in range(10, 190):
            for y in range(10, 70):
                im.putpixel((x, y), (*color, 255))
        b = _io.BytesIO(); im.save(b, format="PNG")
        return b.getvalue()

    dark_out = _knockout_if_dark(_logo((25, 27, 48)))
    im = Image.open(_io.BytesIO(dark_out)).convert("RGBA")
    opaque = [p for p in im.getdata() if p[3] > 40]
    assert opaque and all(p[0] > 240 for p in opaque)          # white variant
    light = _logo((250, 250, 250))
    assert _knockout_if_dark(light) is light                   # untouched


def test_arabic_dialect_is_an_option_not_a_hardcode(monkeypatch):
    # Owner (2026-07-11): "خليها اوبشن — عامية ولا عربية". REEL_ARABIC_DIALECT switches the
    # copy + spoken register; default stays Egyptian colloquial for the pilot market.
    from reel.art_director import _copy_language_label, _spoken_language_label
    monkeypatch.delenv("REEL_ARABIC_DIALECT", raising=False)
    assert "مصرية" in _copy_language_label("ar")               # default: عامية
    monkeypatch.setenv("REEL_ARABIC_DIALECT", "fusha")
    assert "فصحى" in _copy_language_label("ar")                # option: فصحى
    assert "Standard Arabic" in _spoken_language_label("ar")
    monkeypatch.setenv("REEL_ARABIC_DIALECT", "masri")
    assert "Masri" in _spoken_language_label("ar")


def test_ratified_tts_routing_masri_openai_fusha_gemini(monkeypatch):
    # VERBATIK_API_KEY is in the real .env (the owner added it for the reel voice, 2026-07-25)
    # and auto-selection now prefers it — so this test must clear it or it measures the
    # developer's environment instead of the resolution logic. Same class as the video-backend
    # selection tests earlier today.
    monkeypatch.delenv("VERBATIK_API_KEY", raising=False)
    # Owner listen-test verdict (2026-07-11): fusha -> Gemini (Kore directed);
    # masri -> OpenAI gpt-audio. Explicit REEL_TTS_BACKEND still overrides everything.
    from reel.voiceover import _resolve_backend
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "proj")
    monkeypatch.setenv("OPENAI_API_KEY", "k")
    monkeypatch.delenv("REEL_TTS_BACKEND", raising=False)
    monkeypatch.setenv("REEL_ARABIC_DIALECT", "masri")
    assert _resolve_backend(None) == "openai"
    monkeypatch.setenv("REEL_ARABIC_DIALECT", "fusha")
    assert _resolve_backend(None) == "gemini"
    monkeypatch.setenv("REEL_TTS_BACKEND", "edge")
    assert _resolve_backend(None) == "edge"          # explicit override wins
