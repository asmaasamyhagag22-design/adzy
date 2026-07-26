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


def stray_text_verdict(*, seen_lines, approved, brand_name: str = "",
                       product_name: str = "") -> StrayTextVerdict:
    """Which rendered lines were never authorized? Empty `seen_lines` -> could-not-verify."""
    v = StrayTextVerdict()
    lines = [str(x) for x in (seen_lines or []) if str(x).strip()]
    if not lines:
        v.reason = "no text could be read from the render"
        return v

    allowed_tokens = set()
    for src in list(approved or []) + [brand_name, product_name]:
        allowed_tokens |= _tokens(src)

    stray = []
    for line in lines:
        norm = _norm(line)
        if len(norm) < _MIN_LEN:
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
