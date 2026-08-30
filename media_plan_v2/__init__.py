"""Media Plan Engine v2 — turns the grounded data into a Senior Media Buyer's strategic plan.

The v1 media plan is a data receipt (objective + KPI + geo). v2 adds the strategist's layers, each
GROUNDED or DETERMINISTIC (never an LLM guess about money):
  §2 Strategic Narrative  — why this campaign, from competitor weakness × client strength (SWOT/TOWS).
  §3 Test Cells           — the budget split into 3 hypotheses (audience / offer / competitor).
  §5 Reality Check        — capacity ceiling, kill-switch max-CPL, and the scale/fatigue rules.
The kill-switch / scale / fatigue numbers are industry rules from config + decision_engine, NOT the
model's opinion; the marketing-intel input (offers/USPs/pain-points) is the evidence-bound moat.
"""
from .engine import build_media_plan_v2
from .reality_check import build_reality_check
from .schemas import KillSwitch, MediaPlanV2, RealityCheck, TestCell

__all__ = ["RealityCheck", "KillSwitch", "TestCell", "MediaPlanV2",
           "build_reality_check", "build_media_plan_v2"]
