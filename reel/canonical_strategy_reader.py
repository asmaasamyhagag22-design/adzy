"""Read-only exact StrategyVersion seam for canonical Reel planning.

The Reel layer knows no SQL, sessions, repositories, migrations, or legacy files. It delegates one
exact-id read to the application's existing canonical persistence provider and validates the DTO
before handing it to :class:`ReelCreativeContractCompiler`.
"""
from __future__ import annotations

import hashlib
import json
from enum import Enum
from typing import Any, Mapping, Optional, Protocol

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .canonical_contract import ReelCreativeContract, ReelCreativeContractCompiler
from .canonical_live_planning import (
    PlanningCostEnvelope,
    PlanningCostState,
    TextModelConfiguration,
    build_planning_cost_envelope,
)


class CanonicalStrategyReadError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class CanonicalStrategyAuthority(str, Enum):
    POSTGRESQL = "POSTGRESQL"


class CanonicalStrategyReadRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    owner_user_id: str = Field(min_length=1)
    domain_key: str = Field(min_length=1)
    strategy_version_id: str = Field(min_length=1)


def _hash_context(context: Mapping[str, Any]) -> str:
    raw = json.dumps(context, ensure_ascii=False, sort_keys=True,
                     separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class LoadedCanonicalStrategyVersion(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    request: CanonicalStrategyReadRequest
    authority: CanonicalStrategyAuthority = CanonicalStrategyAuthority.POSTGRESQL
    provider_name: str = "postgres"
    context: Mapping[str, Any]
    context_hash: str
    exact_version_verified: bool
    owner_authority_verified: bool
    domain_authority_verified: bool
    approval_verified: bool
    profile_dependency_verified: bool
    selected_positioning_verified: bool
    legacy_source_substituted: bool = False

    @model_validator(mode="after")
    def exact_immutable_authority(self) -> "LoadedCanonicalStrategyVersion":
        checks = (
            self.exact_version_verified, self.owner_authority_verified,
            self.domain_authority_verified, self.approval_verified,
            self.profile_dependency_verified, self.selected_positioning_verified,
        )
        if not all(checks) or self.legacy_source_substituted:
            raise ValueError("loaded StrategyVersion lacks exact PostgreSQL authority")
        if self.provider_name != "postgres":
            raise ValueError("loaded canonical strategy must come from PostgreSQL")
        if self.context_hash != _hash_context(self.context):
            raise ValueError("loaded StrategyVersion context hash mismatch")
        return self

    def compile_reel_creative_contract(self) -> ReelCreativeContract:
        # Serialization returns a defensive deep copy; the compiler never receives the stored
        # mapping object itself and cannot repair or mutate the canonical row through this seam.
        context_copy = json.loads(json.dumps(self.context, ensure_ascii=False, default=str))
        return ReelCreativeContractCompiler().compile(context_copy, current_only=True)


class CanonicalStrategyReader(Protocol):
    def load_approved_strategy_version(
        self, request: CanonicalStrategyReadRequest,
    ) -> LoadedCanonicalStrategyVersion: ...


class CanonicalPersistenceStrategyReader:
    """Adapter over the existing application persistence provider; never a DB architecture."""

    def __init__(self, canonical_provider: Any) -> None:
        provider_name = str(getattr(canonical_provider, "name", "") or "").casefold()
        supports = getattr(canonical_provider, "supports_canonical_strategy", None)
        if provider_name != "postgres" or not callable(supports) or not bool(supports()):
            raise CanonicalStrategyReadError(
                "CANONICAL_STRATEGY_UNAVAILABLE",
                "live Reel planning requires the existing PostgreSQL canonical provider")
        reader = getattr(canonical_provider, "canonical_strategy_context", None)
        if not callable(reader):
            raise CanonicalStrategyReadError(
                "CANONICAL_STRATEGY_READER_UNAVAILABLE",
                "PostgreSQL provider does not expose the canonical StrategyVersion read")
        self._provider = canonical_provider

    @classmethod
    def from_runtime(cls) -> "CanonicalPersistenceStrategyReader":
        from api.persistence import get_persistence

        return cls(get_persistence())

    def load_approved_strategy_version(
        self, request: CanonicalStrategyReadRequest,
    ) -> LoadedCanonicalStrategyVersion:
        context = self._provider.canonical_strategy_context(
            domain_key=request.domain_key,
            user_id=request.owner_user_id,
            run_id=request.strategy_version_id,
        )
        if not isinstance(context, Mapping):
            raise CanonicalStrategyReadError(
                "CANONICAL_STRATEGY_UNAVAILABLE",
                "exact StrategyVersion is absent or not visible to this owner")
        exact_id = str(context.get("strategy_version_id") or "")
        if exact_id != request.strategy_version_id:
            raise CanonicalStrategyReadError(
                "STRATEGY_VERSION_AUTHORITY_MISMATCH",
                "canonical provider returned another StrategyVersion")
        if str(context.get("domain_reference") or "") != request.domain_key:
            raise CanonicalStrategyReadError(
                "CANONICAL_STRATEGY_WRONG_DOMAIN",
                "StrategyVersion did not resolve through the requested brand/domain")
        if str(context.get("run_kind") or "") != "strategy" \
                or str(context.get("run_status") or "") != "succeeded":
            raise CanonicalStrategyReadError(
                "CANONICAL_STRATEGY_RUN_INVALID", "Strategy Run is not a successful strategy")
        if str(context.get("run_brand_id") or "") != str(context.get("brand_id") or ""):
            raise CanonicalStrategyReadError(
                "CANONICAL_STRATEGY_WRONG_BRAND", "Strategy Run belongs to another brand")
        if not bool(context.get("is_current")):
            raise CanonicalStrategyReadError(
                "CANONICAL_STRATEGY_NOT_CURRENT", "exact approved StrategyVersion is not current")
        if str(context.get("approval_state") or "").casefold() != "approved":
            raise CanonicalStrategyReadError(
                "STRATEGY_NOT_APPROVED", "exact Strategy Run has no approved decision")
        profile_id = str(context.get("profile_snapshot_id") or "")
        if not profile_id or profile_id != str(context.get("run_input_profile_id") or "") \
                or not isinstance(context.get("profile"), Mapping):
            raise CanonicalStrategyReadError(
                "PROFILE_SNAPSHOT_UNAVAILABLE",
                "exact StrategyVersion ProfileSnapshot dependency does not resolve")
        output = context.get("output")
        canonical = output.get("canonical_strategy") if isinstance(output, Mapping) else None
        decision = canonical.get("decision") if isinstance(canonical, Mapping) else None
        selected_id = str(decision.get("selected_option_id") or "") \
            if isinstance(decision, Mapping) else ""
        options = decision.get("options") if isinstance(decision, Mapping) else ()
        selected = next((item for item in (options or ()) if isinstance(item, Mapping)
                         and str(item.get("option_id") or "") == selected_id), None)
        if not selected_id or selected is None:
            raise CanonicalStrategyReadError(
                "STRATEGY_SELECTION_MISSING", "approved strategy has no selected positioning")
        copied = json.loads(json.dumps(context, ensure_ascii=False, default=str))
        return LoadedCanonicalStrategyVersion(
            request=request, context=copied, context_hash=_hash_context(copied),
            exact_version_verified=True,
            # Ownership is established by the provider's user-scoped canonical read. Absent and
            # foreign-owned are intentionally the same None result and never exposed here.
            owner_authority_verified=True,
            domain_authority_verified=True,
            approval_verified=True,
            profile_dependency_verified=True,
            selected_positioning_verified=True)


class LivePlanningPreflightState(str, Enum):
    LIVE_PLANNING_PREFLIGHT_READY = "LIVE_PLANNING_PREFLIGHT_READY"
    PRICING_READY_STRATEGY_WAITING_FOR_DB = "PRICING_READY_STRATEGY_WAITING_FOR_DB"
    LIVE_PLANNING_PRECONDITIONS_NEED_REPAIR = "LIVE_PLANNING_PRECONDITIONS_NEED_REPAIR"


class LivePlanningPreflight(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    state: LivePlanningPreflightState
    pricing_envelope: PlanningCostEnvelope
    strategy_loaded: bool
    strategy_context_hash: str = ""
    contract: Optional[ReelCreativeContract] = None
    blocker_code: str = ""
    provider_calls: int = 0
    media_calls: int = 0
    DB_mutations: int = 0

    @model_validator(mode="after")
    def micro_gate_has_no_side_effects(self) -> "LivePlanningPreflight":
        if self.provider_calls or self.media_calls or self.DB_mutations:
            raise ValueError("Gate 2D.3A must remain provider-free and read-only")
        if self.state == LivePlanningPreflightState.LIVE_PLANNING_PREFLIGHT_READY:
            if (self.pricing_envelope.state != PlanningCostState.FINITE_AUTHORIZED
                    or not self.strategy_loaded or self.contract is None):
                raise ValueError("ready preflight requires exact strategy and finite pricing")
        if self.state == LivePlanningPreflightState.PRICING_READY_STRATEGY_WAITING_FOR_DB:
            if self.pricing_envelope.state != PlanningCostState.FINITE_AUTHORIZED \
                    or self.strategy_loaded or self.contract is not None:
                raise ValueError("DB-waiting state requires pricing only")
        return self


def run_live_planning_preflight(
    *, configuration: TextModelConfiguration, request: CanonicalStrategyReadRequest,
    reader: Optional[CanonicalStrategyReader], reader_error: str = "",
) -> LivePlanningPreflight:
    envelope = build_planning_cost_envelope(configuration)
    if envelope.state != PlanningCostState.FINITE_AUTHORIZED:
        return LivePlanningPreflight(
            state=LivePlanningPreflightState.LIVE_PLANNING_PRECONDITIONS_NEED_REPAIR,
            pricing_envelope=envelope, strategy_loaded=False,
            blocker_code=envelope.state.value)
    if reader is None:
        return LivePlanningPreflight(
            state=LivePlanningPreflightState.PRICING_READY_STRATEGY_WAITING_FOR_DB,
            pricing_envelope=envelope, strategy_loaded=False,
            blocker_code=reader_error or "CANONICAL_STRATEGY_UNAVAILABLE")
    try:
        loaded = reader.load_approved_strategy_version(request)
        contract = loaded.compile_reel_creative_contract()
    except CanonicalStrategyReadError as error:
        return LivePlanningPreflight(
            state=LivePlanningPreflightState.PRICING_READY_STRATEGY_WAITING_FOR_DB,
            pricing_envelope=envelope, strategy_loaded=False, blocker_code=error.code)
    return LivePlanningPreflight(
        state=LivePlanningPreflightState.LIVE_PLANNING_PREFLIGHT_READY,
        pricing_envelope=envelope, strategy_loaded=True,
        strategy_context_hash=loaded.context_hash, contract=contract)


def assert_expected_buffalo_demo_contract(contract: ReelCreativeContract) -> None:
    """Post-load demo assertions only; never a source for constructing canonical authority."""
    expected = {
        "strategy_version_id": "52651d10-5fb2-46b9-89f0-bf0eec05e622",
        "target": "mobile app users looking for deals",
        "frame_of_reference": "restaurant",
        "consumer_value": "Convenient mobile app with deals and personalization",
        "primary_message": "Convenient mobile app with deals and personalization",
        "exact_cta": "Start ordering",
        "proof_points": (),
    }
    actual = {key: getattr(contract, key) for key in expected}
    if actual != expected:
        raise CanonicalStrategyReadError(
            "BUFFALO_CANONICAL_ASSERTION_FAILED",
            "loaded PostgreSQL StrategyVersion does not match the reviewed demo assertions")


__all__ = [
    "CanonicalPersistenceStrategyReader", "CanonicalStrategyAuthority",
    "CanonicalStrategyReadError", "CanonicalStrategyReadRequest", "CanonicalStrategyReader",
    "LivePlanningPreflight", "LivePlanningPreflightState", "LoadedCanonicalStrategyVersion",
    "assert_expected_buffalo_demo_contract", "run_live_planning_preflight",
]
