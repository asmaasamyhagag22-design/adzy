from __future__ import annotations

import os
import subprocess
import sys
import time
import uuid
from copy import deepcopy
from pathlib import Path

import pytest

from strategy.positioning.objective import objective_from_mapping
from strategy.positioning.service import build_positioning_strategy

from test_gate2b_positioning_contracts import _objective, _strong_brand


ROOT = Path(__file__).resolve().parents[1]
DSN = os.environ.get("BASEERA_TEST_DATABASE_URL", "")


def _migrate() -> None:
    env = {**os.environ, "BASEERA_DATABASE_URL": DSN, "PERSISTENCE_BACKEND": "postgres"}
    inherited = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = os.pathsep.join(p for p in (inherited, str(ROOT)) if p)
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"], cwd=ROOT,
        env=env, capture_output=True, text=True, timeout=300)
    assert result.returncode == 0, result.stderr[-3000:]


@pytest.fixture()
def crossing(monkeypatch):
    if not DSN:
        pytest.skip("BASEERA_TEST_DATABASE_URL is required for the Gate 2B PostgreSQL proof")
    pytest.importorskip("sqlalchemy")
    _migrate()
    monkeypatch.setenv("PERSISTENCE_BACKEND", "postgres")
    monkeypatch.setenv("BASEERA_DATABASE_URL", DSN)
    from api.persistence import PostgresPersistence
    from api.user_auth import hash_password
    from db.services import PersistenceService, ProfileData, ScrapeSuccess
    from db.session import dispose, get_session_factory
    from db.uow import unit_of_work_factory

    dispose()
    service = PersistenceService(unit_of_work_factory(get_session_factory()))
    user = service.create_user(
        email=f"gate2b-{uuid.uuid4().hex[:10]}@test.local",
        hashed_password=hash_password("password123"))
    domain = f"gate2b-{uuid.uuid4().hex[:10]}.test"
    job = service.create_user_job(owner_user_id=user.id, requested_domain_key=domain)
    service.claim_job(job_id=job.id, worker_id=str(job.id), lease_seconds=600)
    committed = service.commit_scrape_success(ScrapeSuccess(
        domain_key=domain, user_id=user.id, job_id=job.id, worker_id=str(job.id),
        profile=ProfileData(payload={
            "source_url": f"https://{domain}", "name": {"value": "Gate 2B Brand"},
            "extraction_meta": {}, "quality": {}}, schema_version=1),
        display_name="Gate 2B Brand", source_url=f"https://{domain}/"))
    yield service, PostgresPersistence(service), committed.brand, user
    dispose()


def test_canonical_strategy_crosses_with_config_dependency_approval_and_projection(crossing):
    from sqlalchemy import select

    from api.persistence import StaleStrategyApproval, StrategyNotApprovable
    from db.models import Approval, Run, RunDependency
    from db.services import LiveRunData
    from db.session import get_session_factory

    service, provider, brand, user = crossing
    swot = service.create_live_run(LiveRunData(
        brand_id=brand.id, user_id=user.id, kind="swot", status="succeeded",
        input_profile_id=brand.current_profile_id,
        output={"swot": {"strengths": [], "weaknesses": [], "opportunities": [], "threats": []},
                "has_swot": True}, output_schema_version=1))
    service.approve_current_swot(brand_id=brand.id, user_id=user.id, expected_run_id=swot.id)

    build = build_positioning_strategy(
        _strong_brand(), objective=objective_from_mapping(_objective()),
        requested_scope={"geography": ["Qatar"]}, swot_run_id=str(swot.id))
    assert build.output["canonical_strategy"]["verification"]["eligible_for_approval"] is True
    job = service.create_user_job(owner_user_id=user.id, requested_domain_key=brand.domain_key)
    service.claim_job(job_id=job.id, worker_id=str(job.id), lease_seconds=600)
    document = provider.finish_canonical_strategy_job(
        job_id=str(job.id), domain_key=brand.domain_key, user_id=str(user.id), build=build)
    assert document is not None

    run = service.current_successful_run(brand_id=brand.id, kind="strategy", user_id=user.id)
    assert run.input_profile_id == brand.current_profile_id
    assert run.requested_config == build.requested_config
    assert run.effective_config == build.effective_config
    assert run.output == build.output
    with get_session_factory()() as session:
        edge = session.scalar(select(RunDependency).where(RunDependency.run_id == run.id))
        assert edge.depends_on_run_id == swot.id and edge.relation == "uses_swot"
        durable = session.get(Run, run.id)
        assert durable.requested_config["objective"]["business"]["baseline"]["status"] == "UNKNOWN"

    outcome = provider.decide_strategy(
        domain_key=brand.domain_key, user_id=str(user.id), run_id=str(run.id),
        decision="approved")
    assert outcome == {"run_id": str(run.id), "decision": "approved",
                       "approval_state": "approved"}
    projected = provider.visible_brand(domain_key=brand.domain_key, user_id=str(user.id))
    assert projected["strategy_version_id"] == str(run.id)
    assert projected["strategy_approval_state"] == "approved"
    assert projected["has_strategy"] is True
    assert projected["marketing_strategy"]["strategic_posture"] is None
    assert projected["marketing_strategy"]["strategy_version_id"] == str(run.id)
    with get_session_factory()() as session:
        approval = session.scalar(select(Approval).where(
            Approval.subject_run_id == run.id, Approval.subject_kind == "strategy"))
        assert approval.decision == "approved" and approval.decided_by_user_id == user.id

    repaired = service.create_live_run(LiveRunData(
        brand_id=brand.id, user_id=user.id, kind="strategy", status="succeeded",
        input_profile_id=brand.current_profile_id,
        requested_config=build.requested_config, effective_config=build.effective_config,
        output=build.output, output_schema_version=1, dependencies=((swot.id, "uses_swot"),)))
    assert service.decision_for_current_strategy(brand_id=brand.id, user_id=user.id) is None
    with pytest.raises(StaleStrategyApproval):
        provider.decide_strategy(domain_key=brand.domain_key, user_id=str(user.id),
                                 run_id=str(run.id), decision="approved")

    broken_output = {"canonical_strategy": {
        **build.output["canonical_strategy"],
        "verification": {"status": "NEEDS_REPAIR", "eligible_for_approval": False,
                         "defects": [{"code": "INSUFFICIENT_RESEARCH"}]}}}
    ineligible = service.create_live_run(LiveRunData(
        brand_id=brand.id, user_id=user.id, kind="strategy", status="succeeded",
        input_profile_id=brand.current_profile_id,
        requested_config=build.requested_config, effective_config=build.effective_config,
        output=broken_output, output_schema_version=1, dependencies=((swot.id, "uses_swot"),)))
    with pytest.raises(StrategyNotApprovable):
        provider.decide_strategy(domain_key=brand.domain_key, user_id=str(user.id),
                                 run_id=str(ineligible.id), decision="approved")
    assert repaired.id != ineligible.id


def _persist_repair_fixture(crossing, *, creative_ready=False):
    from db.services import LiveRunData

    service, provider, brand, user = crossing
    swot = service.create_live_run(LiveRunData(
        brand_id=brand.id, user_id=user.id, kind="swot", status="succeeded",
        input_profile_id=brand.current_profile_id,
        output={"has_swot": True, "swot": {"strengths": [], "weaknesses": [],
                                             "opportunities": [], "threats": []}},
        output_schema_version=1))
    service.approve_current_swot(brand_id=brand.id, user_id=user.id, expected_run_id=swot.id)
    from product_slice_fixtures import brand as synthetic_brand
    source = synthetic_brand() if creative_ready else _strong_brand()
    build = build_positioning_strategy(
        source, objective=objective_from_mapping(_objective()),
        requested_scope={"geography": ["Qatar"]}, swot_run_id=str(swot.id))
    assert build.output["canonical_strategy"]["verification"]["eligible_for_approval"] is True

    def persist():
        job = service.create_user_job(
            owner_user_id=user.id, requested_domain_key=brand.domain_key)
        service.claim_job(job_id=job.id, worker_id=str(job.id), lease_seconds=600)
        document = provider.finish_canonical_strategy_job(
            job_id=str(job.id), domain_key=brand.domain_key,
            user_id=str(user.id), build=build)
        assert document is not None
        return service.current_successful_run(
            brand_id=brand.id, kind="strategy", user_id=user.id)

    return service, provider, brand, user, build, persist


def test_old_strategy_approval_does_not_migrate_to_repaired_run(crossing):
    service, provider, brand, user, _build, persist = _persist_repair_fixture(crossing)
    old_run = persist()
    old_decision = provider.decide_strategy(
        domain_key=brand.domain_key, user_id=str(user.id), run_id=str(old_run.id),
        decision="approved")
    repaired_run = persist()

    assert old_decision["run_id"] == str(old_run.id)
    assert repaired_run.id != old_run.id
    assert service.decision_for_current_strategy(
        brand_id=brand.id, user_id=user.id) is None


def test_repaired_strategy_creates_a_new_version_without_mutating_the_old_one(crossing):
    service, _provider, brand, user, _build, persist = _persist_repair_fixture(crossing)
    old_run = persist()
    old_output = deepcopy(old_run.output)
    old_requested = deepcopy(old_run.requested_config)
    repaired_run = persist()
    reread_old = service.run_for_user(run_id=old_run.id, user_id=user.id)

    assert repaired_run.id != old_run.id
    assert repaired_run.kind_seq == old_run.kind_seq + 1
    assert reread_old.output == old_output
    assert reread_old.requested_config == old_requested


def test_new_approved_strategy_compiles_contract_without_creating_a_poster(crossing):
    from sqlalchemy import func, select

    from db.models import Run
    from db.session import get_session_factory
    from poster.strategy_contract import CreativeContractCompiler

    service, provider, brand, user, _build, persist = _persist_repair_fixture(crossing, creative_ready=True)
    repaired_run = persist()
    provider.decide_strategy(
        domain_key=brand.domain_key, user_id=str(user.id), run_id=str(repaired_run.id),
        decision="approved")
    with get_session_factory()() as session:
        before = session.scalar(select(func.count()).select_from(Run).where(
            Run.brand_id == brand.id, Run.kind == "poster"))
    context = provider.canonical_strategy_context(
        domain_key=brand.domain_key, user_id=str(user.id), run_id=str(repaired_run.id))
    contract = CreativeContractCompiler().compile(context)
    with get_session_factory()() as session:
        after = session.scalar(select(func.count()).select_from(Run).where(
            Run.brand_id == brand.id, Run.kind == "poster"))

    assert contract.strategy_version_id == str(repaired_run.id)
    assert contract.primary_message == contract.consumer_value
    assert len(contract.proof_points) == 1
    assert contract.proof_points[0].statement == "Published guided specialist onboarding process"
    assert contract.proof_points[0].claim_id in contract.reason_to_believe_claim_ids
    assert contract.proof_points[0].supporting_evidence_ids
    assert before == after == 0


def test_legacy_provider_cannot_masquerade_as_canonical():
    from api.persistence import CapabilityNotWired, LegacyPersistence

    legacy = LegacyPersistence()
    assert legacy.supports_canonical_strategy() is False
    with pytest.raises(CapabilityNotWired):
        legacy.decide_strategy(domain_key="brand", user_id="user", run_id="run",
                               decision="approved")


@pytest.fixture()
def http_client(monkeypatch, tmp_path):
    if not DSN:
        pytest.skip("BASEERA_TEST_DATABASE_URL is required for the Gate 2B HTTP crossing proof")
    _migrate()
    monkeypatch.setenv("PERSISTENCE_BACKEND", "postgres")
    monkeypatch.setenv("BASEERA_DATABASE_URL", DSN)
    monkeypatch.setenv("BASEERA_AUTH_SECRET", "gate2b-http-secret")
    monkeypatch.setenv("BASEERA_WEBAPP_DIR", str(tmp_path / "webapp"))
    import api.user_auth as auth
    from api.main import app
    from api.persistence import reset_persistence
    from db.session import dispose
    from fastapi.testclient import TestClient

    auth._SECRET_CACHE = None
    dispose()
    reset_persistence()
    with TestClient(app) as client:
        yield client
    dispose()
    reset_persistence()


def test_http_objective_to_postgres_run_to_exact_approval_to_projection(http_client):
    """The complete vertical crossing through real HTTP routes and the real background job."""
    from api.persistence import get_persistence
    from db.services import LiveRunData, ProfileData, ScrapeSuccess

    email = f"gate2b-http-{uuid.uuid4().hex[:10]}@test.local"
    signup = http_client.post("/api/auth/signup", json={
        "email": email, "password": "password123", "full_name": "Gate 2B HTTP"})
    assert signup.status_code == 200, signup.text
    body = signup.json()
    user_id = body["user"]["id"]
    headers = {"Authorization": f"Bearer {body['access_token']}"}
    domain = f"gate2b-http-{uuid.uuid4().hex[:10]}.test"
    provider = get_persistence()
    service = provider._service  # noqa: SLF001 — crossing probe below the public HTTP seam
    actor = provider._uuid(user_id)  # noqa: SLF001
    job = service.create_user_job(owner_user_id=actor, requested_domain_key=domain)
    service.claim_job(job_id=job.id, worker_id=str(job.id), lease_seconds=600)
    profile = dict(_strong_brand()["_profile"])
    committed = service.commit_scrape_success(ScrapeSuccess(
        domain_key=domain, user_id=actor, job_id=job.id, worker_id=str(job.id),
        profile=ProfileData(payload=profile, schema_version=1),
        display_name="Gate 2B HTTP", source_url=f"https://{domain}/"))
    strong = _strong_brand()
    swot = service.create_live_run(LiveRunData(
        brand_id=committed.brand.id, user_id=actor, kind="swot", status="succeeded",
        input_profile_id=committed.brand.current_profile_id,
        output={"has_swot": True, "swot": {"strengths": [], "weaknesses": [],
                                            "opportunities": [], "threats": []},
                "_swot_raw": {"strengths": [], "weaknesses": [],
                              "opportunities": [], "threats": []},
                "_full_result": strong["_full_result"]}, output_schema_version=1))
    approved_swot = http_client.post("/api/swot/approve", headers=headers, json={
        "domain_key": domain, "swot_run_id": str(swot.id)})
    assert approved_swot.status_code == 200, approved_swot.text

    started = http_client.post("/api/strategy/generate", headers=headers, json={
        "domain_key": domain, "objective": _objective(),
        "requested_scope": {"geography": ["Qatar"]}})
    assert started.status_code == 200, started.text
    job_id = started.json()["job_id"]
    terminal = None
    for _ in range(200):
        terminal = http_client.get(f"/api/jobs/{job_id}", headers=headers).json()
        if terminal.get("status") in ("done", "error"):
            break
        time.sleep(0.05)
    assert terminal["status"] == "done", terminal

    candidate_doc = http_client.get(f"/api/brands/{domain}", headers=headers).json()
    candidate = candidate_doc["strategy_candidate"]
    assert candidate["eligible_for_approval"] is True
    assert candidate["objective"]["business"]["type"] == "QUALIFIED_LEADS_GROWTH"
    assert candidate["objective"]["business"]["description"] == "Increase qualified launch leads"
    assert candidate_doc["has_strategy"] is False
    assert candidate_doc["marketing_strategy"] == {}
    exact_run_id = candidate["run_id"]
    decision = http_client.post("/api/strategy/decide", headers=headers, json={
        "domain_key": domain, "strategy_run_id": exact_run_id, "decision": "approved"})
    assert decision.status_code == 200, decision.text

    final = http_client.get(f"/api/brands/{domain}", headers=headers).json()
    assert final["strategy_version_id"] == exact_run_id
    assert final["strategy_approval_state"] == "approved"
    assert final["marketing_strategy"]["strategy_version_id"] == exact_run_id
    assert final["marketing_strategy"]["strategic_posture"] is None
    assert final["has_strategy"] is True


def test_unrelated_original_process_does_not_prove_fast_onboarding(crossing):
    from poster.strategy_contract import CreativeContractCompiler, CreativeContractError
    service, provider, brand, user, _build, persist = _persist_repair_fixture(crossing)
    run = persist()
    provider.decide_strategy(domain_key=brand.domain_key, user_id=str(user.id), run_id=str(run.id), decision="approved")
    context = provider.canonical_strategy_context(domain_key=brand.domain_key, user_id=str(user.id), run_id=str(run.id))
    with pytest.raises(CreativeContractError) as caught:
        CreativeContractCompiler().compile(context)
    assert caught.value.code == "PROOF_MESSAGE_MISMATCH"
