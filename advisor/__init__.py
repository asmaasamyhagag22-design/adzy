"""U5 — Missing-Info & Confidence Advisor (v2.2 §5, §6-W7).

A DATA-QUALITY report: concrete gaps, risks, and which persona axes are grounded vs asserted — every
unknown named openly with the action that fills it. Per §10 there is deliberately NO composite
"confidence %" magic number; honesty is per-signal, not a rolled-up score. Deterministic, no LLM.
Formalizes the detection currently inlined in `dashboard/build.py:_honesty_surface`.
"""
from advisor.advisor import build_missing_info_report
from advisor.schemas import Gap, MissingInfoReport

__all__ = ["Gap", "MissingInfoReport", "build_missing_info_report"]
