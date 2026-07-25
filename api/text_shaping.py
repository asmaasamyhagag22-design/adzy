# -*- coding: utf-8 -*-
"""Shape and reorder right-to-left text so Pillow can draw it.

WHY THIS EXISTS (measured, 2026-07-25): Pillow only shapes text when it is built against libraqm.
On this environment `PIL.features.check("raqm")` is False and faces load with `layout_engine = BASIC`,
so `ImageDraw.text` emits every codepoint in its ISOLATED form, in LOGICAL order. Arabic drawn that
way reads backwards and the letters never join — nanocare's real headline «كل حباية بتوصل لمكانها»
came out as unreadable glyph soup on the web app's DEFAULT free image path.

The browser-based poster path (`poster/template.py` + Chromium) shapes correctly on its own and is
untouched. This module exists for the Pillow paths only.

D-8 — script-agnostic, not an Arabic patch:
  * Arabic-script languages (Arabic, Persian, Urdu, …) need JOINING then bidi reordering.
  * Hebrew and other RTL scripts need bidi reordering only.
  * Latin / CJK / digits are returned byte-identical; a shaping layer that mangles English would be
    a new bug wearing a fix's name.

DEGRADE, NEVER RAISE: if the backends are missing on another machine, the original string is
returned — a card with awkward text still beats a 500.
"""
from __future__ import annotations

try:                                        # joining: ligatures + contextual letter forms
    import arabic_reshaper as _reshaper
except Exception:                           # noqa: BLE001
    _reshaper = None                        # type: ignore[assignment]

try:                                        # bidi: logical order -> visual order (UAX #9)
    from bidi import algorithm as _bidi
except Exception:                            # noqa: BLE001
    _bidi = None                            # type: ignore[assignment]

# Unicode ranges that require the bidi pass. Arabic block + supplements cover Persian/Urdu; the
# Hebrew block covers Hebrew/Yiddish. Presentation-forms are included so already-shaped text still
# gets reordered.
_RTL_RANGES = (
    (0x0590, 0x05FF),    # Hebrew
    (0x0600, 0x06FF),    # Arabic
    (0x0700, 0x074F),    # Syriac
    (0x0750, 0x077F),    # Arabic Supplement
    (0x0780, 0x07BF),    # Thaana
    (0x08A0, 0x08FF),    # Arabic Extended-A
    (0xFB1D, 0xFDFF),    # Hebrew/Arabic Presentation Forms-A
    (0xFE70, 0xFEFF),    # Arabic Presentation Forms-B
)
# The subset that also needs JOINING (Hebrew letters never join, so reshaping them is wasted work).
_JOINING_RANGES = ((0x0600, 0x06FF), (0x0750, 0x077F), (0x08A0, 0x08FF))


def _in_any(ch: str, ranges) -> bool:
    cp = ord(ch)
    return any(lo <= cp <= hi for lo, hi in ranges)


def has_rtl(text: str) -> bool:
    """True when the string contains any right-to-left script."""
    return any(_in_any(c, _RTL_RANGES) for c in str(text or ""))


def shape_for_pillow(text: str) -> str:
    """Return `text` ready for `ImageDraw.text`: joined where the script joins, in VISUAL order.

    Left-to-right-only text is returned unchanged (identity), so callers can route every string
    through this without checking the language first."""
    s = str(text or "")
    if not s or not has_rtl(s):
        return s
    out = s
    if _reshaper is not None and any(_in_any(c, _JOINING_RANGES) for c in s):
        try:
            out = _reshaper.reshape(out)
        except Exception:  # noqa: BLE001 — a drawing helper must never break the response
            out = s
    if _bidi is not None:
        try:
            out = _bidi.get_display(out)
        except Exception:  # noqa: BLE001
            return s if out is None else out
    return out


def wrap_shaped(text: str, width: int, limit: int) -> list[str]:
    """Wrap THEN shape — wrapping shaped text would break at visual positions and scramble words.

    `width` is in characters (the caller's existing textwrap budget); `limit` caps the line count."""
    import textwrap
    lines = textwrap.wrap(str(text or ""), width=width)[:limit]
    return [shape_for_pillow(line) for line in lines]
