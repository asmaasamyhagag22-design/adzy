# -*- coding: utf-8 -*-
"""Every string on the poster is one we asked for — or the poster is rejected.

REVIEWER (2026-07-26): «OCR على الـ output → لو فيه نص مش موجود في الـ approved copy list →
reject. ده يمسك الـ garbled packaging أوتوماتيك».

The source of invented labels is already closed — the real product is composited now, not drawn
(`poster.product_composite`). This is the gate behind that fix: whatever text the render still
contains must trace to something we authored. Three things are legitimately readable and are
NOT stray:

  * the approved copy (headline / subheadline / CTA / proof chips),
  * the brand's own wordmark, composited from the real logo file,
  * the real product's own label, composited from the real product photo — correct by
    construction, and rejecting it would reject the cure.

Anything else — a garbled "LABORATONE DERHATOLOGIQUE", a fabricated shop sign, a stray
"TRUSTWORSHY" — is the model writing, and the model does not write.

Reading is done by the vision caller the render already uses (`poster.oneshot` reads the same
way); the JUDGEMENT here is code comparing strings, so nothing hallucinates a verdict.
TRI-STATE: no lines read = `checked=False, passed=None`.
"""
from __future__ import annotations

import logging
import re
import unicodedata
from dataclasses import dataclass, field
from typing import List, Optional

logger = logging.getLogger(__name__)

# Short fragments are noise on any render (a stray glyph, a reflection, a UI artefact in a photo).
_MIN_LEN = 4


def _norm(text: str) -> str:
    s = unicodedata.normalize("NFKC", str(text or "")).lower()
    s = re.sub(r"[ً-ْـ]", "", s)          # Arabic diacritics + tatweel
    s = re.sub(r"[^\w؀-ۿ]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def _tokens(text: str) -> set:
    return {t for t in _norm(text).split() if len(t) >= 3}


@dataclass
class StrayTextVerdict:
    passed: Optional[bool] = None
    checked: bool = False
    stray: List[str] = field(default_factory=list)
    reason: str = ""


_LATIN_RE = re.compile(r"[A-Za-z]")
_NON_LATIN_WORD_RE = re.compile(r"[^\W\dA-Za-z_]", re.UNICODE)


def _is_latin_line(text: str) -> bool:
    """A line written in Latin script (allowing digits and punctuation around it)."""
    return bool(_LATIN_RE.search(text or "")) and not _NON_LATIN_WORD_RE.search(text or "")



def _resembles_any(line: str, tokens: set, *, floor: float = 0.62) -> bool:
    """Does any word in this line look like a word we authorized?

    A misread of a real asset is CLOSE ("FoMad" vs "fouad", "LABORATOIRE" vs a label word);
    an invented string is not. Ratio-based so a one-glyph transcription slip is forgiven and a
    fabricated phrase is not."""
    import difflib

    words = [w for w in _norm(line).split() if len(w) >= 3]
    if not words or not tokens:
        return False
    for w in words:
        best = difflib.get_close_matches(w, list(tokens), n=1, cutoff=floor)
        if best:
            return True
    return False


_LABEL_SYSTEM = (
    "You are a VERBATIM label reader. List every line of text printed ON THE PRODUCT PACKAGING in "
    "this photograph, exactly as printed, one per line. Do not translate, correct, summarise or "
    "add anything. Ignore the background and any surface the product sits on. If no packaging text "
    "is legible, return nothing."
)


def product_label_lines(*, asset, caller) -> List[str]:
    """What is actually PRINTED on the real product, read off the asset itself.

    This is the evidence that makes the exemption below honest. The alternative — excusing a whole
    script when assets are composited — would blind the gate to invented Arabic copy, which is the
    only thing it exists to catch.

    No caller or no asset returns [] : no evidence, no exemption. It never invents a list, because
    a fabricated "label" would approve exactly the text it is supposed to refuse.
    """
    if not asset or caller is None:
        return []
    try:
        from pydantic import BaseModel

        class _Lines(BaseModel):
            lines: List[str] = []

        data = asset
        if not isinstance(data, (bytes, bytearray)):
            from pathlib import Path
            p = Path(str(asset))
            if not p.is_file():
                return []
            data = p.read_bytes()
        resp, _u = caller(_LABEL_SYSTEM, "Read the packaging text.", _Lines,
                          group_name="product_label_ocr", images=[(bytes(data), "image/jpeg")])
        return [str(x).strip() for x in (getattr(resp, "lines", None) or []) if str(x).strip()]
    except Exception as exc:  # noqa: BLE001 — unreadable asset = no exemption, never a crash
        logger.warning("could not read the product label (%s)", type(exc).__name__)
        return []


def stray_text_verdict(*, seen_lines, approved, brand_name: str = "",
                       product_name: str = "",
                       assets_composited: bool = False,
                       product_label_lines: Optional[List[str]] = None) -> StrayTextVerdict:
    """Which rendered lines were never authorized? Empty `seen_lines` -> could-not-verify.

    `assets_composited`: the logo AND the product were pasted from their real files. MEASURED on
    this gate's first live run: it flagged "FoMad" — the critic misreading the real wordmark
    «Fo♥ad», the heart read as an M — and "LABORATOIRE DERMATOLOGIQUE", the product's real label,
    which is simply not a word the product's NAME contains. Both are correct by construction, and
    both would have failed a good poster. When the assets are composited the Latin on the page
    comes from those files, so Latin lines are not judged; the copy's own script still is, which
    is where invented text actually appears."""
    v = StrayTextVerdict()
    lines = [str(x) for x in (seen_lines or []) if str(x).strip()]
    if not lines:
        v.reason = "no text could be read from the render"
        return v

    allowed_tokens = set()
    for src in list(approved or []) + [brand_name, product_name]:
        allowed_tokens |= _tokens(src)

    # WHAT THE REAL BOX SAYS. Read off the asset we pasted, so it is evidence rather than a guess.
    # MEASURED on Nano Care: «حديد + حمض الفوليك + فيتامين ب٦» and «يقلل التعب والإرهاق» are printed
    # on the packshot, and the gate rejected the poster three times for rendering them — burning
    # three paid images per run. The Latin exemption below could not help: this label is Arabic,
    # which is the script most of this system's brands print in.
    label_tokens = set()
    for src in (product_label_lines or []):
        label_tokens |= _tokens(src)

    stray = []
    for line in lines:
        norm = _norm(line)
        if len(norm) < _MIN_LEN:
            continue
        # On the product's own label, in ANY script — approved because it is on the asset.
        if label_tokens and _resembles_any(line, label_tokens):
            continue
        if assets_composited and _is_latin_line(line):
            # Latin on a composited render comes from pixel-exact files, so it is not judged
            # HERE. Trying to separate real label words from invented ones by string distance
            # was measured to fail both ways: «LABORATOIRE» and «ULTRA-LONG UVA PROTECTION» are
            # real label text that appears in no product NAME, while «FIC. TRUSTWORSHY» is
            # near-plausible. Invented strings are caught by the critic's `garbled_text` gate,
            # which reads the pixels instead of guessing from a transcription.
            continue
        toks = _tokens(line)
        if not toks:
            continue
        # A line is ours when MOST of its words are words we authorized. Partial credit matters:
        # a renderer wraps "احمي بشرتك مع لاروش" across two lines, and neither half is a defect.
        covered = len(toks & allowed_tokens) / len(toks)
        if covered < 0.5:
            stray.append(line.strip())

    v.checked = True
    v.stray = stray
    v.passed = not stray
    if stray:
        v.reason = ("text nobody authorized was rendered: "
                    + "; ".join(s[:40] for s in stray[:3]))
        logger.info("[ocr] %s", v.reason)
    return v
