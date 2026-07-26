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


# Therapeutic verbs on a cosmetic are a REGULATORY claim, not copywriting (reviewer 2026-07-26:
# «كلمة بيعالج claim علاجي. العبوة نفسها بتقول ANTI-DARK SPOTS — يعني ضد البقع، مش علاج البقع…
# لإعلان صيدلية في مصر ده حساس»). The pairs map a therapeutic verb to the cosmetic claim that
# says the same thing safely. Applied to COPY only; a page that explicitly uses the therapeutic
# term stays quotable through the Ledger path, not through ad copy.
_THERAPEUTIC = [
    (re.compile(r"بيعالج|يعالج|تعالج|بتعالج"), "بيقلل ظهور"),
    (re.compile(r"\bعلاج\b"), "تقليل ظهور"),
    (re.compile(r"يشفي|بيشفي|شفاء"), "بيساعد على تحسين"),
    (re.compile(r"\btreats?\b", re.I), "helps reduce"),
    (re.compile(r"\bcures?\b", re.I), "helps improve"),
    (re.compile(r"\bheals?\b", re.I), "helps restore"),
]


def soften_therapeutic_claims(text: str) -> tuple[str, bool]:
    """(softened_text, changed). A cosmetic 'treats/يعالج' becomes 'helps reduce/يقلل ظهور' —
    the difference between a cosmetic and a drug, and for a pharmacy ad the difference between
    postable and reportable."""
    out = str(text or "")
    changed = False
    for pat, repl in _THERAPEUTIC:
        new = pat.sub(repl, out)
        if new != out:
            changed = True
            out = new
    return _norm(out), changed
