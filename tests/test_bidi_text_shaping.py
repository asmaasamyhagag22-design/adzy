# -*- coding: utf-8 -*-
"""Right-to-left text drawn with Pillow must be SHAPED and REORDERED before it is drawn.

MEASURED (2026-07-25, this machine): `PIL.features.check("raqm")` is False and the loaded face
reports `layout_engine == 0` (BASIC), so `ImageDraw.text` draws each codepoint in its ISOLATED form,
in LOGICAL order. Rendering nanocare's real copy «كل حباية بتوصل لمكانها» produced letters that read
backwards and never join — the artifact is scratchpad/ar_placeholder_before.png. That is the DEFAULT
free path for every image the web app produces (`api/webapp_images.render_placeholder`), so every
Arabic brand received an unreadable card.

The measurement that proves it without eyes: for an Arabic word, `textlength(word)` equals
`sum(textlength(ch) for ch in word)` exactly — i.e. no glyph ever joined its neighbour. After
shaping, the joined form must be NARROWER than the sum of isolated glyphs.

D-8: the fix is a script-agnostic layer, not an Arabic special case. Arabic, Persian and Urdu need
joining + bidi; Hebrew needs bidi only; Latin/CJK must pass through completely untouched (a shaping
layer that mangles English is a new bug wearing a fix's name)."""
from __future__ import annotations

from PIL import ImageDraw, ImageFont

from api.text_shaping import shape_for_pillow


def _font(size: int = 64):
    for name in ("arial.ttf", "Arial.ttf", "DejaVuSans.ttf", "segoeui.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _draw():
    from PIL import Image
    return ImageDraw.Draw(Image.new("RGB", (10, 10)))


def test_arabic_letters_actually_join_after_shaping():
    d, f = _draw(), _font()
    word = "بتوصل"
    isolated = sum(d.textlength(ch, font=f) for ch in word)
    shaped = d.textlength(shape_for_pillow(word), font=f)
    assert shaped < isolated, (
        f"shaped={shaped} isolated-sum={isolated} — glyphs still never join, so the text is drawn "
        "letter-by-letter exactly as the broken artifact showed")


def test_arabic_is_reordered_so_the_first_word_is_drawn_rightmost():
    """In visual order the logically-FIRST word must end up LAST in the drawn string.

    CORRECTED while writing this file: the first version searched the shaped string for the ORIGINAL
    letters. Reshaping replaces them with Unicode presentation forms (ك U+0643 -> U+FEDB), so that
    assertion could never hold and said nothing about ordering. NFKC folds the presentation forms
    back to their base letters, which is what makes the order actually checkable."""
    import unicodedata
    shaped = unicodedata.normalize("NFKC", shape_for_pillow("كل حباية"))
    assert shaped.index("ح") < shaped.index("ك"), "logical order survived — the line reads backwards"


def test_latin_text_is_returned_byte_identical():
    for s in ("Learn more", "AdArchitect 1.0", "Volume Cafe — Qatar", ""):
        assert shape_for_pillow(s) == s, f"a shaping layer must never touch Latin: {s!r}"


def test_hebrew_is_reordered_even_though_it_never_joins():
    src = "שלום עולם"
    out = shape_for_pillow(src)
    assert out != src, "Hebrew is RTL: it still needs bidi reordering"
    assert set(out) == set(src), "reordering must not add or drop characters"


def test_persian_and_urdu_are_shaped_too_not_just_arabic():
    for src in ("سلام دنیا", "خوش آمدید"):
        assert shape_for_pillow(src) != src, f"Arabic-script language left unshaped: {src}"


def test_mixed_arabic_and_latin_keeps_the_latin_run_readable():
    out = shape_for_pillow("اطلب الآن NanoCare")
    assert "NanoCare" in out, "the Latin run inside an RTL line was reversed"


def test_a_missing_shaping_backend_degrades_to_the_original_text(monkeypatch):
    """No crash, ever: the placeholder must still render if the libs are absent on another machine."""
    import api.text_shaping as ts
    monkeypatch.setattr(ts, "_reshaper", None)
    monkeypatch.setattr(ts, "_bidi", None)
    assert ts.shape_for_pillow("كل حباية") == "كل حباية"


# ---------------------------------------------------------------------------------------------
# The renderer itself — asserted on the PIXELS it produces, never on an intermediate call.
#
# CORRECTED while writing this file: the first version of these two tests spied on
# `webapp_images.shape_for_pillow` and asserted it was called. That is the exact defect class the
# project keeps catching (CS-02 / P-101 — "a test that watches a middle station and testifies for a
# hole"): the spy missed the headline entirely, because the wrapper calls the function through its
# own module namespace, and a spy would also pass if the shaped text were computed and then thrown
# away. These assert the drawn image instead.
# ---------------------------------------------------------------------------------------------

_BRAND_AR = {"organization_name": "نانو كير", "brand_colors": ["#0F5132"],
             "_profile": {"languages": ["ar"]}}
_BRAND_EN = {"organization_name": "NanoCare", "brand_colors": ["#0F5132"],
             "_profile": {"languages": ["en"]}}


def _ink_bias(png: bytes) -> float:
    """Horizontal centre of mass of the non-background pixels, 0.0 = hard left … 1.0 = hard right."""
    import io

    from PIL import Image
    img = Image.open(io.BytesIO(png)).convert("RGB")
    bg = img.getpixel((2, 2))
    weight = total = 0.0
    for x in range(0, img.width, 4):
        col = sum(1 for y in range(0, img.height, 4)
                  if sum(abs(a - b) for a, b in zip(img.getpixel((x, y)), bg)) > 60)
        weight += col * x
        total += col
    return (weight / total / img.width) if total else 0.5


def test_an_rtl_card_is_laid_out_right_to_left_and_an_ltr_card_is_not():
    import api.webapp_images as wi
    ar = _ink_bias(wi.render_placeholder(_BRAND_AR, headline="كل حباية بتوصل لمكانها",
                                         cta="اطلب الآن"))
    en = _ink_bias(wi.render_placeholder(_BRAND_EN, headline="Every capsule arrives",
                                         cta="Shop now"))
    assert ar > en, f"RTL card ink bias {ar:.2f} is not right of the LTR card's {en:.2f}"
    assert ar > 0.5, f"an Arabic card is still flush left (bias {ar:.2f})"


def test_the_rendered_arabic_is_not_the_raw_unshaped_string():
    """Falsifiable: revert the shaping call and this fails, because the card would then be
    pixel-identical to one drawing the raw logical-order string."""
    import io

    import api.webapp_images as wi
    from PIL import Image, ImageDraw

    headline = "كل حباية بتوصل لمكانها"
    card = Image.open(io.BytesIO(wi.render_placeholder(_BRAND_AR, headline=headline,
                                                       cta="اطلب الآن"))).convert("RGB")
    naive = card.copy()
    ImageDraw.Draw(naive).text((97, 405), headline, font=_font(88), fill=(255, 255, 255))
    assert list(card.getdata()) != list(naive.getdata()), "the raw unshaped string was drawn"


def test_the_default_cta_follows_the_brands_language_not_english(tmp_path):
    """D-8: a hardcoded English CTA on an Arabic brand's card is a language hardcode."""
    import api.webapp_images as wi
    png = wi.render_placeholder(_BRAND_AR, headline="عنوان")     # no cta passed
    en = wi.render_placeholder(_BRAND_EN, headline="A headline")
    assert png != en
    assert wi._DEFAULT_CTA.get("ar") and wi._DEFAULT_CTA["ar"] != "Learn more"
    assert wi.brand_language(_BRAND_AR) == "ar"
    assert wi.brand_language({}) == "", "an unknown language must not be guessed"
