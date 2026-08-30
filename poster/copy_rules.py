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
        # v8 shipped «اطلبيه دلوقتي من صيدليات» — the cap sliced a name in half. Cut at the last
        # CONNECTIVE before the limit when there is one, so the label ends on a whole idea.
        cut = max_words
        for i in range(min(max_words, len(words)) - 1, 0, -1):
            if _DANGLING.search(" " + words[i]):
                cut = i
                break
        text = " ".join(words[:cut])
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
    # ONLY the verb forms are rewritten. A noun form («علاج البقع») rewritten to a verb phrase
    # breaks the sentence, so it is left standing and reported by `claim_risks` for a rewrite.
    for pat, repl in _SAFE_REWRITE:
        new = pat.sub(repl, out)
        if new != out:
            changed = True
            out = new
    return _norm(out), changed


# ---------------------------------------------------------------------------------------------
# CLAIM RISKS — morphology-aware, and honest about what it cannot rewrite.
#
# REVIEWER (2026-07-26), measuring the softener: «يعالج → يقلل ظهور بيمسك صيغة واحدة. الجذر
# ع-ل-ج بيطلع في معالجة · علاجي · للعلاج… والاستبدال الحتمي خطر لما الصيغة تختلف: علاج البقع
# (مصدر) → يقلل ظهور البقع بيكسر الجملة نحوياً. لو الصيغة مش matching pattern معروف ⟵ ارفض
# واطلب إعادة صياغة. عربي مكسور أسوأ من رفض».
#
# Two whole categories were also missing: ABSOLUTES (100% / مضمون / الأفضل / آمن تماماً) and
# OPERATIONAL PROMISES («يوصلك النهاردة» is a delivery commitment, not copy flair — it needs a
# verified coverage source, and it does not belong to the medical softener at all).
# ---------------------------------------------------------------------------------------------

# Prefixes Arabic glues onto a stem: بـ / الـ / لـ / للـ / بالـ / و / ف / ي / ت / ن / م.
_AR_PREFIX = r"(?:و|ف|ب|بال|ال|ل|لل|كال|ك|ي|ت|ن|م|مت|است)?"
_THERAPEUTIC_ROOT = re.compile(
    rf"\b{_AR_PREFIX}(?:يعالج|تعالج|نعالج|أعالج|عالج|عولج|يتعالج|معالج\w*|علاج\w*)\b|"
    r"\b(?:treats?|treating|treatment|cures?|curing|heals?|healing|therapy|therapeutic)\b",
    re.IGNORECASE | re.UNICODE)

# The forms we can rewrite SAFELY (verb phrases). Anything else in the root is reported.
_SAFE_REWRITE = [
    (re.compile(r"(?:بي|ب|ي)عالج"), "بيقلل ظهور"),
    (re.compile(r"(?:بت|ت)عالج"), "بتقلل ظهور"),
    (re.compile(r"\btreats?\b", re.I), "helps reduce"),
    (re.compile(r"\bcures?\b", re.I), "helps improve"),
    (re.compile(r"\bheals?\b", re.I), "helps restore"),
]

_ABSOLUTE = re.compile(
    r"(?:١٠٠|100)\s*%|\bمضمون\w*|\bنهائي\w*|\bالأفضل\b|\bالأقوى\b|\bالأول\b|\bالوحيد\b|"
    r"\bتماما?ً?\b|\bفوري\w*|\bدائما?ً?\b|"
    r"\b(?:guaranteed|permanent|instant|best|strongest|only|100%)\b",
    re.IGNORECASE | re.UNICODE)

_OPERATIONAL = re.compile(
    r"يوصل\w*|توصيل|التوصيل|نفس اليوم|النهارده?|بكرة|خلال\s*\d+\s*(?:ساعة|ساعات|يوم|أيام)|"
    r"\b(?:same[- ]day|delivered today|next[- ]day|within \d+ (?:hours?|days?))\b",
    re.IGNORECASE | re.UNICODE)


def claim_risks(text: str) -> list:
    """Every risky claim in one copy line: [{kind, match, action}].

    kind: therapeutic | absolute | operational.
    action: `softened` when a safe verb rewrite exists, `rewrite_required` when the form is one
    we must not patch blindly, `verify_required` for an operational promise that needs a real
    coverage source. Reporting beats guessing: broken Arabic is worse than a refusal."""
    line = str(text or "")
    if not line.strip():
        return []
    risks: list = []
    for m in _THERAPEUTIC_ROOT.finditer(line):
        frag = m.group(0)
        safe = any(pat.search(frag) for pat, _ in _SAFE_REWRITE)
        risks.append({"kind": "therapeutic", "match": frag,
                      "action": "softened" if safe else "rewrite_required"})
    for m in _ABSOLUTE.finditer(line):
        risks.append({"kind": "absolute", "match": m.group(0), "action": "rewrite_required"})
    for m in _OPERATIONAL.finditer(line):
        risks.append({"kind": "operational", "match": m.group(0),
                      "action": "verify_required"})
    return risks


# ---------------------------------------------------------------------------------------------
# ENFORCEMENT — because detection was never the missing half.
#
# MEASURED 2026-08-02 on a rendered Al Fouad poster for a Uriage spot paste. `claim_risks` did its
# job: it flagged «علاج» (therapeutic, rewrite_required) and «فوري» (absolute, rewrite_required).
# Its ONE production caller — `poster/pipeline.py`, under a comment reading "THE CLAIM LINTER, on
# what will actually ship" — logged the risks, appended them to `concept.remediation`, and shipped
# the copy unchanged. The poster went out reading:
#
#     «علاج يورياچ هيسياك الموضعي للحبوب. مفعول فوري من بالليل للصبح.»
#
# A therapeutic claim and an immediacy promise, on a PHARMACY ad, both flagged and both printed.
# And the audit then recorded `remediated: true` — because `remediation` was non-empty. "Handled"
# meant "detected". That is this repo's oldest defect standing in its most expensive place.
#
# WHY SENTENCE-LEVEL. The reviewer's rule stands and is the reason `claim_risks` refuses to patch:
# «عربي مكسور أسوأ من رفض» — a noun form («علاج البقع») rewritten into a verb phrase breaks the
# grammar. So nothing here edits inside a sentence it cannot safely edit: the verb forms go through
# the existing softener, and a sentence still carrying an un-rewritable claim is DROPPED WHOLE.
# Losing a line costs a supporting sentence; printing it costs the client a regulatory problem.
_SENTENCE_SPLIT = re.compile(r"(?<=[.!؟?])\s+|\n+")


def enforce_claim_rules(text: str) -> tuple[str, list]:
    """(safe_text, actions) — the copy as it may actually ship.

    Order matters: soften first, because a sentence the softener fixes must not then be dropped
    for the claim it no longer makes.

    `actions` records what was DONE, not what was seen: {kind, match, action, field?} with action
    in {softened, dropped}. An empty list means the text was already safe — which is why callers
    can finally treat a non-empty list as "we changed something".
    """
    original = str(text or "")
    if not original.strip():
        return original, []

    softened, did_soften = soften_therapeutic_claims(original)
    actions: list = []
    if did_soften:
        actions.append({"kind": "therapeutic", "match": "verb form", "action": "softened"})

    kept: list[str] = []
    for sentence in _SENTENCE_SPLIT.split(softened):
        if not sentence.strip():
            continue
        blocking = [r for r in claim_risks(sentence) if r.get("action") == "rewrite_required"]
        if blocking:
            for r in blocking:
                actions.append({"kind": r["kind"], "match": r["match"], "action": "dropped"})
            continue
        kept.append(sentence.strip())

    out = " ".join(kept).strip()
    # EVERY sentence was unsafe. Returning "" is correct and deliberate: a blank subheadline is a
    # missing line, and a poster can carry that. A poster carrying a therapeutic claim cannot.
    return _norm(out), actions
