# -*- coding: utf-8 -*-
"""Deterministic copy rules — the ones a model keeps getting wrong because they are conventions.

REVIEWER (2026-07-26) on the Al Fouad poster's button:
  «الـ CTA buttons ما بتاخدش نقطة في الآخر. وكمان الجملة طويلة زيادة لزرار… واسم الصيدلية موجود
   في اللوجو والسبتايتل أصلاً — يعني اتكرر 3 مرات».

A button is a label, not a sentence. These are typography conventions, not creative choices, so
they belong in code where they hold every time — not in a prompt that obeys them most of the
time. Language-agnostic (D-8): the rules are about punctuation, length and repetition, none of
which are English facts.
"""
from __future__ import annotations

import re

MAX_CTA_WORDS = 4

# Sentence-final punctuation in the scripts this product ships in. A button never ends in one.
_TERMINALS = ".。۔؟?!،,;؛:"


def _norm(text) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


def tidy_cta(cta: str, *, brand_name: str = "", max_words: int = MAX_CTA_WORDS) -> str:
    """A button label: no terminal punctuation, no brand name it already sits beside, short.

    The brand name is dropped because the mark is composited onto the same poster and the
    subheadline names it too — three mentions of one pharmacy on one card. Shortening keeps the
    FIRST words: an imperative's verb is what makes it a button ("اطلبيه أونلاين من صيدليات
    الفؤاد" -> "اطلبيه أونلاين")."""
    text = _norm(cta)
    if not text:
        return ""
    brand = _norm(brand_name)
    if brand and brand in text:
        text = _norm(text.replace(brand, " "))
        # A preposition left dangling by the removal ("من", "from", "at") reads as a typo, and
        # one pass is not enough: «من ويبسايت صيدليات الفؤاد» leaves "من ويبسايت", then "من".
        # Punctuation first: the connective is only "trailing" once the full stop is gone
        # («اطلبيه أونلاين من .» ends in the stop, so the dangling «من» never matched).
        text = _strip_dangling(text.strip(_TERMINALS + " "))
    text = text.strip(_TERMINALS + " ")
    words = text.split()
    if len(words) > max_words:
        text = " ".join(words[:max_words])
        text = _strip_dangling(text)
    return text.strip(_TERMINALS + " ")


_DANGLING = re.compile(r"(?:\s|^)(?:من|في|فى|على|علي|مع|عن|from|at|on|via|of|the|to)$")


def _strip_dangling(text: str) -> str:
    """Drop trailing connective words until the label ends on a real one."""
    for _ in range(3):
        stripped = _norm(_DANGLING.sub("", text))
        if stripped == text:
            break
        text = stripped
    return text
