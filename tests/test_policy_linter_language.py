# -*- coding: utf-8 -*-
"""P-93 (2026-07-18): the policy linter's rules are Arabic + English only, so copy in ANY other
language matched no rule and returned verdict="PASS" — a false "compliant" for copy it never
actually checked. It must return NEEDS_REVIEW (unchecked) for a language it has no rules for, not
PASS. Hermetic — pure, offline."""
from __future__ import annotations

from policy_linter import lint_campaign


def test_english_clean_copy_passes():
    r = lint_campaign(category="retail", copy_surfaces={"headline": "Discover our natural skincare range"})
    assert r.verdict == "PASS"


def test_arabic_clean_copy_passes():
    r = lint_campaign(category="retail", copy_surfaces={"headline": "اكتشفي مجموعتنا الطبيعية للعناية"})
    assert r.verdict == "PASS"


def test_other_language_copy_is_needs_review_not_a_false_pass():
    # French clean-looking copy: the ar/en rules can't check it, so it must NOT be certified PASS.
    r = lint_campaign(category="retail",
                      copy_surfaces={"headline": "Découvrez notre gamme de produits naturels de beauté",
                                     "primary_text": "Livraison gratuite dans toute la France cette semaine"})
    assert r.verdict == "NEEDS_REVIEW", "un-checkable language must not be certified compliant"


def test_a_caught_violation_is_still_violations_regardless_of_language():
    # a script-agnostic violation (100% guaranteed) still fires even in other-language copy
    r = lint_campaign(category="medical",
                      copy_surfaces={"headline": "Résultat 100% guaranteed pour tous"})
    assert r.verdict == "VIOLATIONS"


def test_gr1_catches_guaranteed_profit():
    # P-26: "guaranteed profit/returns" is a classic finance-ad guarantee GR-1 used to miss.
    for copy in ("Guaranteed profit every month", "We guarantee 200% returns", "100% guaranteed profit"):
        r = lint_campaign(category="finance", copy_surfaces={"headline": copy})
        assert r.verdict == "VIOLATIONS", copy
