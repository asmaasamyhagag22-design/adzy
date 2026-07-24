# -*- coding: utf-8 -*-
"""The art critic must not fail an ENGLISH brand for being in English.

MEASURED (Volume Qatar, 2026-07-21): a clean poster — verbatim fidelity 1.0, critic score 8/10 —
was REJECTED with "The poster uses Latin script, which violates the pass criteria." The brand's own
content language is English (`arabic=False`), so Latin script IS its copy.

The per-field instruction was already language-aware; the `overall_pass` conjunction was not — it
demanded "no Latin in the copy" for every brand on earth. Another D-8 language-axis leak: a rule
that is right for an Arabic brand, applied universally, silently blocks every English one.
Hermetic."""
from __future__ import annotations

from poster.vision_qa import _pass_criteria


def test_arabic_brand_still_forbids_latin_copy():
    assert "no Latin in the copy" in _pass_criteria(True)


def test_english_brand_is_not_judged_on_latin_script():
    en = _pass_criteria(False)
    assert "no Latin in the copy" not in en
    assert "NOT Arabic" in en and "must never count against" in en


def test_the_other_hard_gates_survive_in_both_languages():
    for arabic in (True, False):
        p = _pass_criteria(arabic)
        for gate in ("logo_ok", "script_wellformed", "invented_product", "third_party_mark",
                     "score >= 7"):
            assert gate in p, (arabic, gate)
