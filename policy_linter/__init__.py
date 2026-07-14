"""U4 — Meta Policy Linter (v2.2 §5, §6-W7): the deterministic ad-policy gate (Gate ②).

A campaign's copy is linted against Meta ad-policy classes (personal attributes, before/after,
guaranteed results, medical claims) with COMPILED bilingual (ar+en) detectors — no LLM, no network
(PD-1). The verdict is recomputed in code, so nothing self-grades its own compliance. Category rules
reuse the single source in `poster/contracts.py:_COMPLIANCE_BY_CATEGORY`.
"""
from policy_linter.linter import lint_campaign
from policy_linter.rewrite import RewriteOutcome, suggest_and_regate
from policy_linter.schemas import LintFinding, PolicyLintReport

__all__ = ["LintFinding", "PolicyLintReport", "lint_campaign", "RewriteOutcome", "suggest_and_regate"]
