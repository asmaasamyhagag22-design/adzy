"""The linter — scans campaign copy against the category-relevant rules and returns a report whose
verdict is recomputed in code (PD-1). Pure: no LLM, no network."""
from __future__ import annotations

from typing import Dict, Optional

from policy_linter.rules import RULES, category_group
from policy_linter.schemas import LintFinding, PolicyLintReport


def lint_campaign(*, category, copy_surfaces: Dict[str, str]) -> PolicyLintReport:
    """Lint each copy surface (name -> text, e.g. {"hook": ..., "primary_text": ..., "cta": ...})
    against the Meta policy rules that apply to this business category. `verdict="VIOLATIONS"` iff any
    `block` finding — computed here, never taken from a model."""
    group = category_group(category)
    findings = []
    surfaces = [k for k, v in (copy_surfaces or {}).items() if v and str(v).strip()]
    for surface in surfaces:
        text = str(copy_surfaces[surface])
        for rule in RULES:
            if not rule.applies_to(group):
                continue
            for pat in rule.patterns:
                m = pat.search(text)
                if m:
                    findings.append(LintFinding(
                        rule_id=rule.rule_id, category=rule.klass, severity=rule.severity,
                        surface=surface, span=m.group(0), message=rule.message,
                        suggested_rewrite=rule.suggested_rewrite))
                    break   # one finding per (surface, rule) — the first hit is enough evidence
    verdict = "VIOLATIONS" if any(f.severity == "block" for f in findings) else "PASS"
    return PolicyLintReport(verdict=verdict, findings=findings, surfaces_checked=surfaces)


def surfaces_from_compliance_sheet(sheet: Optional[dict]) -> Dict[str, str]:
    """Extract the shipped-copy surfaces from a `grounding.compliance.build_compliance_sheet` dict,
    so U6 can lint exactly the copy that ships. {copy_field: copy_text}."""
    out: Dict[str, str] = {}
    if not isinstance(sheet, dict):
        return out
    for i, row in enumerate(sheet.get("shipped_copy") or []):
        if isinstance(row, dict) and (row.get("copy_text") or "").strip():
            out[str(row.get("copy_field") or f"copy_{i}")] = str(row["copy_text"])
    return out
