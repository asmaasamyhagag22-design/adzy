from __future__ import annotations

import socket
from datetime import datetime, timezone
from decimal import Decimal

import pytest
from pydantic import BaseModel, ValidationError

from api.persistence import LegacyPersistence
from reel.canonical_execution import (
    ProviderBillingMode,
    ProviderSurface,
    VERTEX_GEMINI_25_FLASH_STANDARD_PRICING_2026_08_23,
    resolve_text_provider_pricing,
)
from reel.canonical_live_planning import (
    DEFAULT_LIVE_CALL_BUDGETS,
    LiveStructuredCallController,
    PlanningCallBudget,
    PlanningCostState,
    PlanningPurpose,
    StructuredPlanningRequest,
    StructuredProviderResponse,
    TextModelConfiguration,
    TextModelStatus,
    build_planning_cost_envelope,
    canonical_google_vertex_planning_configuration,
)
from reel.canonical_strategy_reader import (
    CanonicalPersistenceStrategyReader,
    CanonicalStrategyReadError,
    CanonicalStrategyReadRequest,
    LivePlanningPreflightState,
    run_live_planning_preflight,
)


STRATEGY_ID = "52651d10-5fb2-46b9-89f0-bf0eec05e622"
DOMAIN = "buffaloburger_com"
OWNER = "owner-buffalo"


@pytest.fixture(autouse=True)
def _micro_gate_network_quarantine(monkeypatch):
    for key in (
        "OPENAI_API_KEY", "GOOGLE_API_KEY", "GEMINI_API_KEY", "SERPER_API_KEY",
        "SEARCHAPI_API_KEY", "APIFY_TOKEN", "ELEVENLABS_API_KEY", "VEO_API_KEY",
    ):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("BASEERA_NO_SPEND", "1")

    def unexpected(*_args, **_kwargs):
        raise AssertionError("Gate 2D.3A attempted outbound network I/O")

    monkeypatch.setattr(socket.socket, "connect", unexpected, raising=False)
    monkeypatch.setattr(socket.socket, "connect_ex", unexpected, raising=False)


def _claim(claim_id: str, statement: str):
    return {
        "claim_id": claim_id,
        "statement": statement,
        "epistemic_type": "INFERENCE",
        "verification_status": "SUPPORTED",
        "supporting_evidence_ids": ["ev_" + claim_id],
        "limitations": [],
    }


def _canonical_context(**updates):
    context = {
        "strategy_version_id": STRATEGY_ID,
        "run_kind": "strategy",
        "run_status": "succeeded",
        "run_brand_id": "brand-buffalo",
        "brand_id": "brand-buffalo",
        "domain_reference": DOMAIN,
        "is_current": True,
        "approval_state": "approved",
        "profile_snapshot_id": "profile-buffalo",
        "run_input_profile_id": "profile-buffalo",
        "profile": {
            "organization_name": "Buffalo Burger",
            "existing_ctas": [{"value": "Start ordering"}],
            "languages": ["en"],
        },
        "requested_config": {"objective": {
            "objective_type": "CONVERSION",
            "business": {"description": "Increase first orders through the mobile app"},
            "exact_cta": "Start ordering",
        }},
        "output": {"canonical_strategy": {
            "decision": {
                "decision_id": "decision-buffalo",
                "selected_option_id": "position-mobile",
                "options": [{
                    "option_id": "position-mobile",
                    "target": "mobile app users looking for deals",
                    "frame_of_reference": "restaurant",
                    "consumer_value": "Convenient mobile app with deals and personalization",
                    "reason_to_believe": "",
                    "consumer_value_claim_ids": ["claim-value"],
                    "reason_to_believe_claim_ids": [],
                    "internal_supporting_claim_ids": [],
                    "differentiation_claim_ids": [],
                }],
            },
            "claims": [_claim(
                "claim-value", "Convenient mobile app with deals and personalization")],
            "verification": {"eligible_for_approval": True},
        }},
    }
    context.update(updates)
    return context


class _CanonicalProvider:
    name = "postgres"

    def __init__(self, context=None, *, visible_owner=OWNER):
        self.context = _canonical_context() if context is None else context
        self.visible_owner = visible_owner
        self.calls = []
        self.mutations = 0
        self.raw_sql_calls = 0

    def supports_canonical_strategy(self):
        return True

    def canonical_strategy_context(self, **kwargs):
        self.calls.append(kwargs)
        if kwargs["user_id"] != self.visible_owner:
            return None
        return self.context

    def execute(self, *_args, **_kwargs):
        self.raw_sql_calls += 1
        raise AssertionError("Reel read layer executed raw SQL")

    def add(self, *_args, **_kwargs):
        self.mutations += 1
        raise AssertionError("Reel read layer attempted a mutation")


def _request(**updates):
    values = dict(owner_user_id=OWNER, domain_key=DOMAIN, strategy_version_id=STRATEGY_ID)
    values.update(updates)
    return CanonicalStrategyReadRequest(**values)


def _configuration(model_id="gemini-2.5-flash",
                   billing_mode=ProviderBillingMode.STANDARD):
    return canonical_google_vertex_planning_configuration(
        project="generate-504115", location="us-central1",
        model_id=model_id, billing_mode=billing_mode)


# 1
def test_exact_vertex_standard_pricing_tuple_resolves():
    price = resolve_text_provider_pricing(
        provider_name="google", model_id="gemini-2.5-flash",
        surface=ProviderSurface.VERTEX_AI, billing_mode=ProviderBillingMode.STANDARD)
    assert price == VERTEX_GEMINI_25_FLASH_STANDARD_PRICING_2026_08_23
    assert price.input_text_image_video_usd_per_million == Decimal("0.30")
    assert price.output_text_response_reasoning_usd_per_million == Decimal("2.50")
    assert price.cached_input_usd_per_million == Decimal("0.03")


# 2
def test_wrong_surface_does_not_reuse_vertex_price():
    assert resolve_text_provider_pricing(
        provider_name="google", model_id="gemini-2.5-flash",
        surface=ProviderSurface.GEMINI_API,
        billing_mode=ProviderBillingMode.STANDARD) is None


# 3
def test_priority_mode_does_not_reuse_standard_rate():
    assert resolve_text_provider_pricing(
        provider_name="google", model_id="gemini-2.5-flash",
        surface=ProviderSurface.VERTEX_AI,
        billing_mode=ProviderBillingMode.PRIORITY) is None
    assert build_planning_cost_envelope(
        _configuration(billing_mode=ProviderBillingMode.PRIORITY)
    ).state == PlanningCostState.COST_NOT_PROVABLE


# 4
def test_unknown_model_remains_cost_not_provable():
    envelope = build_planning_cost_envelope(_configuration(model_id="gemini-unknown"))
    assert envelope.state == PlanningCostState.COST_NOT_PROVABLE
    assert envelope.worst_case_cost_usd is None


# 5
def test_pricing_source_and_version_metadata_are_preserved():
    price = VERTEX_GEMINI_25_FLASH_STANDARD_PRICING_2026_08_23
    envelope = build_planning_cost_envelope(_configuration())
    assert price.pricing_source_url == (
        "https://cloud.google.com/vertex-ai/generative-ai/pricing")
    assert price.pricing_verified_date == "2026-08-23"
    assert envelope.pricing_version == (
        "vertex-gemini-2.5-flash-standard-2026-08-23")


# 6
def test_output_reasoning_tokens_are_included_in_billable_output():
    class Probe(BaseModel):
        ok: bool

    class UsageProvider:
        def generate_structured(self, request, response_model):
            return StructuredProviderResponse(
                payload={"ok": True}, provider_timestamp=datetime.now(timezone.utc),
                input_tokens=1000, cached_input_tokens=200,
                output_tokens=100, thinking_tokens=900,
                model_id="gemini-2.5-flash",
                provider_usage_metadata={"fixture": "usage"})

    configuration = _configuration()
    envelope = build_planning_cost_envelope(configuration)
    controller = LiveStructuredCallController(
        configuration=configuration, envelope=envelope, provider=UsageProvider())
    budget = DEFAULT_LIVE_CALL_BUDGETS[0]
    controller.call(StructuredPlanningRequest(
        purpose=PlanningPurpose.CONCEPT_PROPOSER,
        system_instruction="schema only", payload={"bounded": True},
        input_artifact_hashes=("hash",),
        maximum_output_tokens=budget.maximum_output_tokens,
        maximum_thinking_tokens=budget.maximum_thinking_tokens), Probe)
    trace = controller.traces[0]
    assert trace.billable_output_tokens == 1000
    assert trace.total_billable_tokens == 2000
    assert trace.cost_usd == Decimal("0.002746")
    assert trace.pricing_version_id == envelope.pricing_version


# 7
def test_finite_three_call_envelope_is_computed():
    envelope = build_planning_cost_envelope(_configuration())
    assert envelope.state == PlanningCostState.FINITE_AUTHORIZED
    assert envelope.maximum_live_calls == 3
    assert len(envelope.call_budgets) == 3
    assert envelope.worst_case_cost_usd == Decimal("0.0969")


# 8
def test_envelope_over_fifteen_cents_is_blocked():
    large = tuple(PlanningCallBudget(
        purpose=purpose, maximum_input_tokens=200_000,
        maximum_output_tokens=100_000, maximum_thinking_tokens=100_000,
        input_bound_basis="oversized test prompt", output_bound_basis="oversized test response",
        thinking_bound_basis="oversized test reasoning") for purpose in PlanningPurpose)
    envelope = build_planning_cost_envelope(_configuration(), call_budgets=large)
    assert envelope.state == PlanningCostState.PLANNING_COST_CAP_EXCEEDED
    assert envelope.worst_case_cost_usd > Decimal("0.15")


# 9
def test_exact_strategy_version_is_required():
    with pytest.raises(ValidationError):
        CanonicalStrategyReadRequest(
            owner_user_id=OWNER, domain_key=DOMAIN, strategy_version_id="")


# 10
def test_fixture_provider_cannot_satisfy_live_authority():
    class Fixture(_CanonicalProvider):
        name = "fixture"

    with pytest.raises(CanonicalStrategyReadError) as error:
        CanonicalPersistenceStrategyReader(Fixture())
    assert error.value.code == "CANONICAL_STRATEGY_UNAVAILABLE"


# 11
def test_local_json_cannot_satisfy_live_authority():
    with pytest.raises(CanonicalStrategyReadError):
        CanonicalPersistenceStrategyReader(_canonical_context())


# 12
def test_legacy_storage_cannot_satisfy_live_authority():
    with pytest.raises(CanonicalStrategyReadError):
        CanonicalPersistenceStrategyReader(LegacyPersistence())


# 13
def test_latest_strategy_cannot_replace_exact_requested_id():
    latest = _canonical_context(strategy_version_id="newer-strategy-id")
    reader = CanonicalPersistenceStrategyReader(_CanonicalProvider(latest))
    with pytest.raises(CanonicalStrategyReadError) as error:
        reader.load_approved_strategy_version(_request())
    assert error.value.code == "STRATEGY_VERSION_AUTHORITY_MISMATCH"


# 14
def test_wrong_owner_is_blocked_without_disclosing_existence():
    reader = CanonicalPersistenceStrategyReader(_CanonicalProvider())
    with pytest.raises(CanonicalStrategyReadError) as error:
        reader.load_approved_strategy_version(_request(owner_user_id="foreign-owner"))
    assert error.value.code == "CANONICAL_STRATEGY_UNAVAILABLE"


# 15
def test_wrong_domain_is_blocked():
    context = _canonical_context(domain_reference="other_domain")
    reader = CanonicalPersistenceStrategyReader(_CanonicalProvider(context))
    with pytest.raises(CanonicalStrategyReadError) as error:
        reader.load_approved_strategy_version(_request())
    assert error.value.code == "CANONICAL_STRATEGY_WRONG_DOMAIN"


# 16
def test_unapproved_strategy_is_blocked():
    reader = CanonicalPersistenceStrategyReader(
        _CanonicalProvider(_canonical_context(approval_state="pending")))
    with pytest.raises(CanonicalStrategyReadError) as error:
        reader.load_approved_strategy_version(_request())
    assert error.value.code == "STRATEGY_NOT_APPROVED"


# 17
def test_reel_reader_uses_canonical_provider_without_raw_sql():
    provider = _CanonicalProvider()
    loaded = CanonicalPersistenceStrategyReader(provider).load_approved_strategy_version(_request())
    assert loaded.context["strategy_version_id"] == STRATEGY_ID
    assert provider.calls == [{
        "domain_key": DOMAIN, "user_id": OWNER, "run_id": STRATEGY_ID}]
    assert provider.raw_sql_calls == 0


def _ready_preflight():
    reader = CanonicalPersistenceStrategyReader(_CanonicalProvider())
    return run_live_planning_preflight(
        configuration=_configuration(), request=_request(), reader=reader)


# 18
def test_micro_gate_makes_zero_text_provider_calls():
    result = _ready_preflight()
    assert result.state == LivePlanningPreflightState.LIVE_PLANNING_PREFLIGHT_READY
    assert result.provider_calls == 0


# 19
def test_micro_gate_makes_zero_media_calls():
    assert _ready_preflight().media_calls == 0


# 20
def test_micro_gate_makes_zero_database_mutations():
    provider = _CanonicalProvider()
    reader = CanonicalPersistenceStrategyReader(provider)
    result = run_live_planning_preflight(
        configuration=_configuration(), request=_request(), reader=reader)
    assert result.DB_mutations == 0
    assert provider.mutations == 0
