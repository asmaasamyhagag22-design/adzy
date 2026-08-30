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
    if any(f.severity == "block" for f in findings):
        verdict = "VIOLATIONS"                       # a caught violation is a violation, any language
    elif surfaces and not _copy_is_checkable("\n".join(str(copy_surfaces[s]) for s in surfaces)):
        # P-93: the rules are Arabic + English only. Clean copy in another language means the linter
        # could NOT actually check it — that is NEEDS_REVIEW, never a "compliant" PASS.
        verdict = "NEEDS_REVIEW"
    else:
        verdict = "PASS"
    return PolicyLintReport(verdict=verdict, findings=findings, surfaces_checked=surfaces)


def _copy_is_checkable(text: str) -> bool:
    """P-93: True only when the copy is in a language the rule set covers (Arabic or English). Any
    Arabic script counts as ar-covered; Latin copy is checkable only if langdetect calls it English.
    Pure/offline (langdetect), deterministic (seeded)."""
    import re as _re
    if _re.search(r"[؀-ۿ]", text):         # any Arabic script -> the ar rules apply
        return True
    letters = _re.sub(r"[^A-Za-zÀ-ɏ]", "", text)   # Latin incl. accented
    if len(letters) < 8:                             # too short to judge -> assume checkable (en-ish)
        return True
    try:
        from langdetect import DetectorFactory, detect
        DetectorFactory.seed = 0
        return detect(text) == "en"
    except Exception:  # noqa: BLE001 — detector unavailable/fails -> don't block, assume checkable
        return True


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
