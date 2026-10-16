"""The `text_arrow` CTA must emit a CSS escape for U+2190, not a control character.

MEASURED (elabdfoods.com, 2026-08-27). The rendered poster showed a missing-glyph box
followed by the digits "90" beside the CTA rule — visible in
outputs/posters/elabd_poster_v2.png. Cause: the CSS was assembled in an ordinary Python
f-string written `content:'\\2190'`. Python owns backslash escapes first, and `\\21` is a
valid OCTAL escape (U+0011 DEVICE CONTROL 1); the `9` and the `0` then stayed literal. So
the stylesheet said `content:'\\x1190'` and the browser drew tofu + "90" instead of the
left arrow the branch was written to draw.

This is the NO-GLYPH class the poster QA exists to catch, arriving from our OWN stylesheet
rather than from an image model.

The assertions read the HTML the real renderer produces — never the source file — so they
survive a rewrite of how the CSS is assembled, and fail if the escape regresses.
"""
import re

from poster.schemas import PosterBrief, PosterDesignSpec
from poster.template import render_poster_html


def _html(cta_style):
    brief = PosterBrief(
        business_name="Test Brand",
        category="retail",
        headline="A headline",
        cta_text="Order now",
        palette_hex=["#E4B4B4", "#C27C3B", "#6F3C2E"],
        primary_color="#E4B4B4",
    )
    return render_poster_html(brief, None, PosterDesignSpec(cta_style=cta_style))


def _cta_after_content(html):
    match = re.search(r"cta::after\{content:'([^']*)'", html)
    assert match, "the text_arrow CTA emitted no ::after content rule"
    return match.group(1)


def test_the_text_arrow_cta_emits_the_css_escape_for_a_left_arrow():
    content = _cta_after_content(_html("text_arrow"))
    assert content == "\\2190", f"emitted {content!r}, which CSS will not read as U+2190"


def test_the_text_arrow_cta_never_emits_a_control_character():
    """U+0011 is what the octal misreading produced, and what the browser drew as tofu."""
    content = _cta_after_content(_html("text_arrow"))
    offenders = [hex(ord(c)) for c in content if ord(c) < 0x20 or ord(c) == 0x7F]
    assert not offenders, f"control characters in the CTA glyph: {offenders}"


def test_no_poster_stylesheet_carries_a_control_character():
    """Whole-stylesheet sweep: any CTA archetype, not just the arrow one. A control character
    anywhere in the emitted CSS is a glyph the font cannot draw."""
    for style in ("pill", "block", "outline", "text_arrow"):
        html = _html(style)
        offenders = sorted({hex(ord(c)) for c in html
                            if ord(c) < 0x20 and c not in "\t\r\n"})
        assert not offenders, f"cta_style={style!r} emitted control characters {offenders}"
