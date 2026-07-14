"""U4 Meta Policy Linter — deterministic ad-policy gate (v2.2 §6-W7 gate).

The core acceptance: ZERO false negatives on `block` rules across a seeded ar+en violation set, no
false positives on clean copy, correct category scoping, and a code-computed verdict.
"""
from __future__ import annotations

import pytest

from policy_linter import PolicyLintReport, lint_campaign
from policy_linter.linter import surfaces_from_compliance_sheet

# Seeded violations: (policy_class, category_that_triggers, {surface: violating_text}) — ar + en.
_SEEDED = [
    ("personal_attributes", "restaurant", {"hook": "هل تعاني من الجوع؟"}),
    ("personal_attributes", "clinic", {"hook": "do you suffer from hair loss"}),
    ("guaranteed_results", "finance", {"benefit": "عائد مضمون على استثمارك"}),
    ("guaranteed_results", "clinic", {"benefit": "100% guaranteed results"}),
    ("before_after", "clinic", {"proof": "شوف الفرق قبل وبعد"}),
    ("before_after", "beauty", {"proof": "real before/after photos"}),
    ("medical_claims", "clinic", {"claim": "علاج نهائي للصلع"}),
    ("medical_claims", "skincare", {"claim": "a permanent cure for acne"}),
]


@pytest.mark.parametrize("klass,category,copy", _SEEDED)
def test_seeded_block_violations_are_all_caught(klass, category, copy):
    r = lint_campaign(category=category, copy_surfaces=copy)
    assert r.verdict == "VIOLATIONS"
    assert any(f.category == klass and f.severity == "block" for f in r.findings), (klass, r.findings)
    # the finding names its surface + the offending span
    f = next(f for f in r.findings if f.category == klass)
    assert f.surface in copy and f.span and f.span in copy[f.surface]


def test_clean_copy_passes_with_no_findings():
    r = lint_campaign(category="clinic", copy_surfaces={
        "hook": "احجز استشارتك مع أطباء المركز", "benefit": "خبرة 15 سنة في زراعة الشعر",
        "cta": "اتصل بنا اليوم"})
    assert r.verdict == "PASS" and r.findings == []
    assert set(r.surfaces_checked) == {"hook", "benefit", "cta"}


def test_before_after_and_medical_are_scoped_to_medical_skincare_only():
    copy = {"a": "قبل وبعد الوجبة", "b": "علاج نهائي للملل"}
    # a restaurant (default group): before/after + medical-claim rules do NOT apply
    assert lint_campaign(category="restaurant", copy_surfaces=copy).findings == []
    # a clinic: both apply
    r = lint_campaign(category="clinic", copy_surfaces=copy)
    assert {f.category for f in r.findings} == {"before_after", "medical_claims"}


def test_universal_rules_apply_to_every_category():
    for cat in ("restaurant", "ecommerce", "clinic", "finance"):
        r = lint_campaign(category=cat, copy_surfaces={"x": "نتيجة مضمونة", "y": "بتعاني من كذا؟"})
        assert {f.category for f in r.findings} == {"guaranteed_results", "personal_attributes"}
        assert r.verdict == "VIOLATIONS"


def test_verdict_is_pass_when_no_findings_and_report_is_strict():
    r = lint_campaign(category="ecommerce", copy_surfaces={"h": "شحن مجاني لأول طلب"})
    assert r.verdict == "PASS"
    assert PolicyLintReport.model_validate(r.model_dump()).verdict == "PASS"   # strict round-trip


def test_empty_surfaces_pass():
    assert lint_campaign(category="clinic", copy_surfaces={}).verdict == "PASS"
    assert lint_campaign(category="clinic", copy_surfaces={"h": "", "c": "   "}).surfaces_checked == []


def test_surfaces_from_compliance_sheet_extracts_shipped_copy():
    sheet = {"shipped_copy": [
        {"copy_field": "headline", "copy_text": "نتيجة مضمونة"},
        {"copy_field": "sub", "copy_text": ""},                       # blank -> skipped
        {"copy_field": "cta", "copy_text": "احجز الآن"}]}
    surfaces = surfaces_from_compliance_sheet(sheet)
    assert surfaces == {"headline": "نتيجة مضمونة", "cta": "احجز الآن"}
    r = lint_campaign(category="clinic", copy_surfaces=surfaces)
    assert r.verdict == "VIOLATIONS" and any(f.surface == "headline" for f in r.findings)
