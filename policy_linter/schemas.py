"""U4 data contracts (v2.2 §5). Additive, strict. `category` is the Meta policy CLASS the finding
belongs to (not the business category)."""
from __future__ import annotations

from typing import List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

PolicyClass = Literal["personal_attributes", "before_after", "guaranteed_results", "medical_claims"]


class LintFinding(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rule_id: str
    category: PolicyClass
    severity: Literal["block", "warn"]
    surface: str                       # which copy surface (e.g. "hook", "primary_text", "cta")
    span: str                          # the matched offending text
    message: str                       # why it violates + what to do
    suggested_rewrite: Optional[str] = None   # if produced, MUST re-pass Ledger + Linter before use


class PolicyLintReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    verdict: Literal["PASS", "VIOLATIONS"]     # recomputed in code: VIOLATIONS iff any block finding
    findings: List[LintFinding] = Field(default_factory=list)
    surfaces_checked: List[str] = Field(default_factory=list)
