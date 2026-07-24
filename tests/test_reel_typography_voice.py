# -*- coding: utf-8 -*-
"""Reel polish (owner: the reel looked "بشع" — silly typography + a voice that "بتتغير وبيقطع").

Hermetic (no network / no TTS / no ffmpeg): pins the pure decisions —
  * a luxury/fashion/beauty brand gets the ELEGANT serif caption theme, others stay BOLD;
  * the voice-over performance brief is tone-aware and no longer hardcodes an "appetizing food ad";
  * the continuous-narration script joins the scene lines in order (one voice, no per-scene call).
"""
from __future__ import annotations

from reel.text_overlay import _reel_theme
from reel.voiceover import _instructions_for


def _profile(tone: str = "", category: str = "") -> dict:
    return {
        "tone_of_voice": {"value": tone} if tone else None,
        "category": {"value": category} if category else None,
    }


# --- caption THEME (elegant serif vs bold) ---------------------------

def test_luxury_tone_selects_elegant_theme():
    assert _reel_theme(_profile(tone="luxury")) == "elegant"
    assert _reel_theme(_profile(tone="premium")) == "elegant"


def test_jewelry_category_selects_elegant_theme():
    assert _reel_theme(_profile(tone="", category="jewelry")) == "elegant"
    assert _reel_theme(_profile(tone="", category="Fashion & Beauty")) == "elegant"


def test_ordinary_brand_stays_bold():
    assert _reel_theme(_profile(tone="friendly", category="ecommerce")) == "bold"
    assert _reel_theme(_profile(tone="", category="")) == "bold"
    assert _reel_theme({}) == "bold"


# --- voice-over performance brief (no food hardcode; tone-aware) ------

def test_brief_has_no_food_hardcode():
    for tone in ("luxury", "playful", "", "corporate"):
        brief = _instructions_for(["Some brand line"], tone=tone).lower()
        assert "food" not in brief and "appetizing" not in brief


def test_brief_is_tone_aware_and_one_narrator():
    lux = _instructions_for(["Timeless elegance"], tone="luxury").lower()
    assert "unhurried" in lux or "refined" in lux
    # ONE consistent narrator is the fix for the changing voice.
    assert "one consistent" in lux or "same voice" in lux


def test_brief_detects_arabic_and_follows_the_region_not_hardcoded_egyptian():
    # P-87/88/05/89: an Arabic script triggers an Arabic dialect clause, but with NO region
    # evidence (no `dialect` passed) it is a NEUTRAL regional register — NOT a hardcoded Egyptian
    # read for every Arabic brand. The region is threaded via `dialect` from arabic_dialect_for.
    ar = _instructions_for(["روّاد التحول الرقمي"], tone="luxury")
    assert "Authentic Arabic" in ar and "EGYPTIAN" not in ar.upper()
    # a Gulf-evidenced dialect steers the clause to Khaleeji; an Egyptian one to Egyptian
    gulf = _instructions_for(["روّاد التحول الرقمي"], tone="luxury",
                             dialect="Gulf/Khaleeji Arabic (لهجة خليجية)")
    assert "Khaleeji" in gulf and "EGYPTIAN" not in gulf.upper()
    eg = _instructions_for(["روّاد التحول الرقمي"], tone="luxury",
                           dialect="Egyptian Arabic (عامية مصرية)")
    assert "Egyptian" in eg
    # a non-Arabic script gets NO Arabic dialect clause at all
    en = _instructions_for(["An enduring symbol"], tone="luxury")
    assert "EGYPTIAN" not in en and "Authentic Arabic" not in en


# --- the CLI reel path (textlayer._scene_html) must ALSO honour the theme ------------

def test_cli_scene_html_uses_serif_for_luxury_and_oswald_otherwise():
    from reel.schemas import ReelScene, Storyboard
    from reel.textlayer import _scene_html

    def html(tone: str) -> str:
        sb = Storyboard(business_name="B", primary_dir="ltr", tone=tone, palette_hex=["#ccb454"])
        scene = ReelScene(kind="intro", duration_s=3.0, visual_prompt="x",
                          headline="An Enduring Symbol")
        return _scene_html(scene, sb, 1080, 1920, None)

    assert "font-family:'Fraunces'" in html("luxury")     # refined serif for luxury
    assert "font-family:'Oswald'" in html("ecommerce")    # punchy default otherwise


# --- poster: the real logo is composited deterministically (never garbled) ----------

def test_overlay_real_logo_composites_and_changes_the_poster(tmp_path):
    import io

    from PIL import Image

    from poster.pipeline import _overlay_real_logo

    poster = tmp_path / "poster.png"
    Image.new("RGB", (1080, 1350), (18, 14, 12)).save(poster)   # dark poster
    before = poster.read_bytes()
    buf = io.BytesIO()
    Image.new("RGBA", (400, 160), (204, 180, 84, 255)).save(buf, "PNG")  # a gold "logo"
    assert _overlay_real_logo(poster, buf.getvalue(), rtl=False) is True
    after = poster.read_bytes()
    assert after != before                                  # the poster was modified (logo placed)
    assert Image.open(poster).size == (1080, 1350)          # still a valid same-size PNG
