"""Build the Missing-Info report — pure detection from the real artifacts (no fabrication, no
composite score). Mirrors `dashboard/build.py:_honesty_surface`, formalized into typed Gaps."""
from __future__ import annotations

from typing import Any, List, Optional

from advisor.schemas import Gap, MissingInfoReport

# The persona axes that carry demographic weight (Persona.interests is a list, handled separately).
_CORE_AXES = ("age_range", "gender", "income_level", "location_summary")


def _val(profile: Any, key: str) -> str:
    if not isinstance(profile, dict):
        return ""
    v = profile.get(key)
    if isinstance(v, dict):
        v = v.get("value")
    return str(v).strip() if v else ""


def _missing_elements(profile: Any) -> List[str]:
    """Brand facts a media buyer wants but the site didn't surface (honest 'not found', never guessed)."""
    if not isinstance(profile, dict):
        return []
    out: List[str] = []
    offerings = profile.get("offerings") or []
    priced = any(isinstance(o, dict) and (o.get("price_text") or o.get("price")) for o in offerings)
    if not priced:
        out.append("pricing (no priced offerings found on-site)")
    if not (profile.get("trust_signals") or []):
        out.append("proof/testimonials (no trust signals found on-site)")
    if not (profile.get("value_propositions") or []):
        out.append("value propositions (none distinctly stated on-site)")
    return out


def build_missing_info_report(*, media_plan: Any = None, profile: Any = None, swot_mode: str = "",
                              competitor_count: int = 0, n_reviews: int = 0,
                              ads_available: bool = False) -> MissingInfoReport:
    """Concrete gaps + risks + per-axis grounding for a plan. `media_plan` is a `MediaPlan` (or None).
    Deterministic; never invents a number or a fact."""
    gaps: List[Gap] = []
    risks: List[str] = []

    # 1) competitor set (empty is VALID -> flagged here; §6-W2)
    if str(swot_mode).lower() == "standalone" or int(competitor_count or 0) == 0:
        gaps.append(Gap(field="competitors", severity="action",
                        why_it_matters="the analysis stands on the brand's own site — the market "
                        "comparison (opportunities/threats) is incomplete",
                        fills_via="confirm/broaden the competitor set (Discovery v2 / a manual list)"))
        risks.append("SWOT has no competitor-derived Opportunities/Threats yet")

    # 2) customer voice
    if int(n_reviews or 0) == 0:
        gaps.append(Gap(field="customer_reviews", severity="action",
                        why_it_matters="no voice-of-customer signal to ground S/W or objections",
                        fills_via="connect the brand's Google-Maps/Vezeeta reviews (U8a pull)"))

    # 3) competitor ads (owner-blocked on Meta identity)
    if not ads_available:
        gaps.append(Gap(field="competitor_ads", severity="info",
                        why_it_matters="competitors' running ads (winner signals + format priors) "
                        "are not available yet",
                        fills_via="the one-time free Meta ad-account identity confirmation (U2 live)"))

    # 4) KPI target + 5) persona grounding — read the real MediaPlan
    objectives = list(getattr(media_plan, "objectives", None) or [])
    obj0 = objectives[0] if objectives else None
    if obj0 is not None:
        kpi = getattr(obj0, "kpi_target", None)
        if kpi is not None and getattr(kpi, "target_value", None) is None:
            gaps.append(Gap(field="kpi_target", severity="info",
                            why_it_matters="the cost target is optimise-only until launch data exists "
                            "— pacing can't be validated pre-launch",
                            fills_via="calibrate from the first ~2 weeks of real spend (we never invent one)"))
            risks.append("no numeric KPI target yet — budget pacing is an estimate until launch")

    grounded_axes: List[str] = []
    ungrounded_axes: List[str] = []
    persona = getattr(media_plan, "base_persona", None)
    if persona is not None:
        for axis in _CORE_AXES:
            ref = getattr(persona, axis, None)
            if ref is None:
                continue                       # not asserted -> honestly absent, not a claim
            (grounded_axes if getattr(ref, "is_grounded", False) else ungrounded_axes).append(axis)
        if ungrounded_axes:
            gaps.append(Gap(field="audience_demographics", severity="info",
                            why_it_matters=f"asserted persona axes without site evidence "
                            f"({', '.join(ungrounded_axes)}) — provisional",
                            fills_via="settles from campaign performance; treat as a hypothesis"))

    missing_elements = _missing_elements(profile)
    return MissingInfoReport(gaps=gaps, risks=risks, grounded_axes=grounded_axes,
                             ungrounded_axes=ungrounded_axes, missing_elements=missing_elements)
