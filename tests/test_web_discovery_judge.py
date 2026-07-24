# -*- coding: utf-8 -*-
"""1.2 (owner report 2026-07-19): NTI (education) got ZERO competitors — the relevance judge's
DROP list said "universities/courses" unconditionally, so an education subject's REAL peers were
all rejected ("judge dropped ALL"). The fix is vertical-NEUTRAL (D-8): the DROP list is built
dynamically, lifting the clause that names the subject's OWN kind — education keeps institutes,
software keeps tools — and every other category keeps the original prompt BYTE-IDENTICAL."""
from __future__ import annotations

from competitor.web_discovery import _judge_system_prompt, _self_kind_key

_LEGACY = (
    "You filter competitor candidates for a business-comparison report. Keep ONLY "
    "candidates that are plausibly a DIRECT COMPETITOR — an actual operating business "
    "of the SAME KIND as the subject, in or serving the subject's market. DROP: "
    "listicles/rankings, directories, news/media, universities/courses, software or "
    "tools FOR that industry, marketplaces, and anything unrelated. When unsure, DROP "
    "(a wrong competitor is worse than a missing one). Never add anything."
)


def test_education_subject_keeps_institutes():
    p = _judge_system_prompt("education")
    assert "universities/courses" not in p                      # the self-kind clause is lifted
    assert "KEEP" in p and ("institute" in p.lower() or "training" in p.lower())
    assert "listicles/rankings" in p and "directories" in p     # the rest of the DROP list stays
    assert "When unsure, DROP" in p                             # reject-only posture survives


def test_software_subject_keeps_tools():
    p = _judge_system_prompt("software")
    assert "software or tools FOR that industry" not in p
    assert "universities/courses" in p                          # only the SELF kind is lifted


def test_other_categories_byte_identical_to_legacy():
    for cat in ("clinic", "restaurant", "ecommerce", "", None):
        assert _judge_system_prompt(cat) == _LEGACY, cat


def test_self_kind_key_mapping():
    assert _self_kind_key("education") == "education"
    assert _self_kind_key("Training Academy") == "education"
    assert _self_kind_key("SaaS / software") == "software"
    assert _self_kind_key("clinic") is None
    assert _self_kind_key(None) is None
