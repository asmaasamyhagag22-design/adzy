"""Explicit business-objective parsing and the narrow legacy-text adapter."""
from __future__ import annotations

from typing import Any, Mapping
from uuid import uuid4

from .contracts import BusinessObjective, KnownValue, ObjectiveType, ValueStatus


_SAFE_TEXT_MAPPINGS = {
    "brand awareness": (ObjectiveType.AWARENESS_GROWTH, "DEMAND_CREATION"),
    "increase awareness": (ObjectiveType.AWARENESS_GROWTH, "DEMAND_CREATION"),
}
_AMBIGUOUS_TEXT = {
    "customer retention": "RETENTION",
    "new product launch": "PRODUCT_LAUNCH",
    "rebranding": "REPOSITIONING",
    "seasonal promotion": "PROMOTION",
}
_EMPTY_TEXT = {"", "let ai infer", "let the ai infer", "auto", "infer"}


def _known(raw: Any, *, default: ValueStatus = ValueStatus.UNKNOWN) -> KnownValue:
    if isinstance(raw, Mapping):
        status = ValueStatus(str(raw.get("status") or default.value).upper())
        return KnownValue(status=status, value=raw.get("value"))
    if raw not in (None, ""):
        return KnownValue(ValueStatus.KNOWN, raw)
    return KnownValue(default, None)


def objective_from_mapping(raw: Mapping[str, Any]) -> BusinessObjective:
    """Parse an explicit objective. Absence is an error; nothing is inferred from the brand."""
    business = raw.get("business") if isinstance(raw.get("business"), Mapping) else raw
    kind = ObjectiveType(str(business.get("type") or "").upper())
    geography_raw = business.get("geography") or []
    geography_rows = [geography_raw] if isinstance(geography_raw, str) else geography_raw
    constraints_raw = business.get("constraints") or []
    constraint_rows = [constraints_raw] if isinstance(constraints_raw, str) else constraints_raw
    return BusinessObjective(
        objective_id=str(raw.get("objective_id") or uuid4()),
        origin=str(raw.get("origin") or "USER_EXPLICIT"),
        business_type=kind,
        description=str(business.get("description") or "").strip(),
        metric=_known(business.get("metric")),
        baseline=_known(business.get("baseline")),
        target=_known(business.get("target")),
        time_horizon=_known(business.get("time_horizon")),
        geography=tuple(str(v).strip() for v in geography_rows if str(v).strip()),
        market=str(business.get("market") or "").strip(),
        constraints=tuple(str(v).strip() for v in constraint_rows if str(v).strip()),
        marketing_role=str(raw.get("marketing_role") or "UNSPECIFIED"),
        behavioral_objective=_known(raw.get("behavioral_objective")),
        media_optimization=_known(raw.get("media_optimization"), default=ValueStatus.NOT_SET),
    )


def objective_from_legacy_text(text: str) -> tuple[BusinessObjective | None, str]:
    """Map only unambiguous old UI values; ambiguous/empty text explicitly asks the owner."""
    normal = " ".join(str(text or "").strip().lower().split())
    if normal in _EMPTY_TEXT:
        return None, "ASK_OWNER"
    if normal in _SAFE_TEXT_MAPPINGS:
        kind, role = _SAFE_TEXT_MAPPINGS[normal]
        return BusinessObjective(
            objective_id=str(uuid4()), origin="LEGACY_FREE_TEXT_MAPPED",
            business_type=kind, description=str(text).strip(), marketing_role=role), "MAPPED"
    if normal in _AMBIGUOUS_TEXT:
        return BusinessObjective(
            objective_id=str(uuid4()), origin="LEGACY_FREE_TEXT_AMBIGUOUS",
            business_type=ObjectiveType.OTHER, description=str(text).strip(),
            marketing_role=_AMBIGUOUS_TEXT[normal]), "ASK_OWNER"
    return BusinessObjective(
        objective_id=str(uuid4()), origin="LEGACY_FREE_TEXT_UNRECOGNIZED",
        business_type=ObjectiveType.OTHER, description=str(text).strip()), "ASK_OWNER"
