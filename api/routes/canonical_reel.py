"""Provider-free canonical Reel authority endpoint (Gate 2D.1).

This router cannot render: its only output is an immutable contract, the renderable planner
projection, and an explicitly selected capability profile.  The legacy `/reel/from-profile`
route remains a separate router and is never called from here.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict

from api.persistence import CapabilityNotWired, get_persistence
from api.routes.auth import require_user
from reel.canonical_contract import ReelContractError
from reel.canonical_execution import (
    CanonicalReelExecutionError,
    CanonicalReelExecutionPreferences,
    CanonicalReelPreparation,
    prepare_canonical_reel,
)


router = APIRouter()


class CanonicalReelPrepareRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    domain_key: str
    strategy_version_id: str
    execution_preferences: CanonicalReelExecutionPreferences


def _strategy_context(*, domain_key: str, strategy_version_id: str,
                      user_id: str) -> dict:
    try:
        context = get_persistence().canonical_strategy_context(
            domain_key=domain_key, user_id=user_id, run_id=strategy_version_id)
    except CapabilityNotWired as exc:
        raise HTTPException(status_code=503, detail={
            "code": "CANONICAL_REEL_REQUIRES_STRATEGY_AUTHORITY",
            "message": "Canonical Reel requires immutable StrategyVersion storage.",
        }) from exc
    if context is None:
        raise HTTPException(status_code=404, detail={
            "code": "STRATEGY_VERSION_NOT_FOUND",
            "message": "The exact StrategyVersion is missing or not visible.",
        })
    if str(context.get("strategy_version_id") or "") != strategy_version_id:
        raise HTTPException(status_code=409, detail={
            "code": "STRATEGY_VERSION_AUTHORITY_MISMATCH",
            "message": "Storage returned a different StrategyVersion than the exact request.",
        })
    return context


@router.post("/reel/canonical/prepare", response_model=CanonicalReelPreparation)
def prepare_reel(request: CanonicalReelPrepareRequest,
                 user: dict = Depends(require_user)) -> CanonicalReelPreparation:
    """Compile and capability-check only. Provider calls and artifact delivery are impossible."""
    context = _strategy_context(
        domain_key=request.domain_key,
        strategy_version_id=request.strategy_version_id,
        user_id=str(user["id"]),
    )
    try:
        return prepare_canonical_reel(context, request.execution_preferences)
    except ReelContractError as exc:
        raise HTTPException(status_code=409, detail={
            "code": exc.code,
            "message": str(exc),
        }) from exc
    except CanonicalReelExecutionError as exc:
        raise HTTPException(status_code=409, detail={
            "code": exc.code,
            "message": str(exc),
        }) from exc


__all__ = ["CanonicalReelPrepareRequest", "prepare_reel", "router"]
