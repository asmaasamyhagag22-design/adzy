"""Deterministic Meta ad-policy detectors — compiled bilingual (ar + en) patterns per policy class.

Scope: `personal_attributes` and `guaranteed_results` are UNIVERSAL Meta policy (every category);
`before_after` and `medical_claims` apply to the medical + skincare category groups (the same groups
`poster/contracts.py:_COMPLIANCE_BY_CATEGORY` gates). Universal default beyond these = brand-safety
only (INTERFACES §D-8 — no vertical hack). Patterns target CLEAR violations to keep zero false
negatives on `block` rules without false-positiving ordinary copy.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List, Optional

from policy_linter.schemas import PolicyClass

# Business category -> policy group (reuses the compliance source's category keys).
_CATEGORY_GROUP = {
    "clinic": "medical", "hospital": "medical", "medical": "medical", "dental": "medical",
    "beauty": "skincare", "skincare": "skincare", "cosmetics": "skincare", "cosmetic": "skincare",
    "finance": "finlegal", "legal": "finlegal", "professional_services": "finlegal",
    "services_b2b": "finlegal",
}


def category_group(category) -> str:
    cat = str(getattr(category, "value", category) or "").strip().lower()
    return _CATEGORY_GROUP.get(cat, "default")


@dataclass(frozen=True)
class Rule:
    rule_id: str
    klass: PolicyClass
    severity: str                    # "block" | "warn"
    scope: Optional[frozenset]       # None = universal; else the groups it applies to
    patterns: tuple                  # compiled regexes
    message: str
    suggested_rewrite: Optional[str] = None

    def applies_to(self, group: str) -> bool:
        return self.scope is None or group in self.scope


def _rx(*pats: str) -> tuple:
    return tuple(re.compile(p, re.IGNORECASE | re.UNICODE) for p in pats)


_MED_SKIN = frozenset({"medical", "skincare"})

RULES: List[Rule] = [
    # ---- UNIVERSAL: implying knowledge of a personal attribute (Meta prohibited) ----
    Rule("PA-1", "personal_attributes", "block", None, _rx(
        r"\bdo you (suffer|struggle) (from|with)\b",
        r"\bare you (over[\s-]?weight|obese|diabetic|depressed|infertile|balding|struggling with)\b",
        r"تعان(ي|ين|ى)\s+من", r"بتعان(ي|ين|ى)\s+من", r"هل\s+تعان",
        r"عندك\s+مشكلة\s+(في|مع)", r"لو\s+بتعاني",
    ), "Implies a personal attribute/condition about the viewer (Meta personal-attributes policy). "
       "Address the need, not the person ('علاج تساقط الشعر' not 'بتعاني من تساقط الشعر؟')."),

    # ---- UNIVERSAL: guaranteed results / outcomes / returns ----
    Rule("GR-1", "guaranteed_results", "block", None, _rx(
        r"\bguarantee(d|s)?\b[^.\n]{0,30}\b(result|outcome|return|cure|weight\s*loss|success)\b",
        r"\bguaranteed\s+(results|outcomes|returns)\b",
        r"\b100%\s*(guaranteed|result|results|effective|success|cure)\b",
        r"نتيجة\s+مضمونة", r"نتائج\s+مضمونة", r"مضمون(ة|ه)?\s+(النتيجة|النتائج|الشفاء)",
        r"ضمان\s+(النتيجة|النتائج|الشفاء)", r"عائد\s+مضمون", r"ربح\s+مضمون", r"نتيجة\s+أكيدة",
    ), "Promises a guaranteed result/outcome/return (Meta prohibits guaranteeing results). State the "
       "offer without guaranteeing the outcome."),

    # ---- MEDICAL + SKINCARE: before/after imagery-or-copy ----
    Rule("BA-1", "before_after", "block", _MED_SKIN, _rx(
        r"\bbefore\s*[/&-]?\s*(and\s*)?after\b", r"\bbefore-and-after\b",
        r"قبل\s*[/و]\s*بعد", r"الفرق\s+قبل\s+وبعد",
    ), "Before/after framing (banned for medical/skincare ads). Show the service/experience, not a "
       "before/after comparison."),

    # ---- MEDICAL + SKINCARE: cure / definitive-treatment claims ----
    Rule("MC-1", "medical_claims", "block", _MED_SKIN, _rx(
        r"\b(permanent|guaranteed)\s+cure\b", r"\bcures?\s+(all|any|every)\b", r"\bmiracle\s+(cure|treatment)\b",
        r"علاج\s+نهائي", r"شفاء\s+تام", r"شفاء\s+نهائي", r"القضاء\s+نهائي(ا|اً)\s+على", r"يشفي\s+تمام",
    ), "Claims a definitive/miracle cure (Meta medical-claims policy). Describe the treatment offered, "
       "not a guaranteed cure."),
]
