from __future__ import annotations

from fastapi.testclient import TestClient

from api.main import app
from api.routes import analysis as route
from api.routes.auth import require_user
from strategy.positioning.service import CanonicalStrategyBuild

from test_gate2b_positioning_contracts import _objective, _strong_brand


class _Provider:
    def __init__(self, *, canonical: bool = True):
        self.canonical = canonical
        self.decisions = []

    def visible_brand(self, *, domain_key: str, user_id: str):
        if domain_key != "brand_test" or user_id != "user-1":
            return None
        return {**_strong_brand(), "has_swot": True, "swot_approved": True,
                "swot_run_id": "7b2775a4-1e70-4612-93a4-0f2ae771e5b5"}

    def supports_canonical_strategy(self) -> bool:
        return self.canonical

    def decide_strategy(self, **kwargs):
        self.decisions.append(kwargs)
        return {"run_id": kwargs["run_id"], "decision": kwargs["decision"],
                "approval_state": kwargs["decision"]}


def _client(monkeypatch, provider: _Provider):
    captured = {}

    def start(_request, domain_key, work, *, owner_id, kind):
        captured["build"] = work(provider.visible_brand(domain_key=domain_key, user_id=owner_id))
        captured["kind"] = kind
        return {"job_id": "job-1"}

    monkeypatch.setattr(route, "get_persistence", lambda: provider)
    monkeypatch.setattr(route, "start_brand_job", start)
    app.dependency_overrides[require_user] = lambda: {"id": "user-1"}
    return TestClient(app), captured


def test_http_strategy_route_requires_and_preserves_explicit_objective(monkeypatch):
    client, captured = _client(monkeypatch, _Provider())
    try:
        missing = client.post("/api/strategy/generate", json={"domain_key": "brand_test"})
        assert missing.status_code == 422
        started = client.post("/api/strategy/generate", json={
            "domain_key": "brand_test", "objective": _objective(),
            "requested_scope": {"geography": ["Qatar"]}})
        assert started.status_code == 200 and started.json() == {"job_id": "job-1"}
        assert captured["kind"] == "strategy"
        assert isinstance(captured["build"], CanonicalStrategyBuild)
        requested = captured["build"].requested_config
        assert requested["objective"]["objective_id"] == "obj_test"
        assert requested["objective"]["business"]["baseline"] == {
            "status": "UNKNOWN", "value": None}
    finally:
        app.dependency_overrides.clear()


def test_http_strategy_route_refuses_legacy_and_decides_exact_run(monkeypatch):
    legacy_client, _ = _client(monkeypatch, _Provider(canonical=False))
    try:
        refused = legacy_client.post("/api/strategy/generate", json={
            "domain_key": "brand_test", "objective": _objective()})
        assert refused.status_code == 503
    finally:
        app.dependency_overrides.clear()

    provider = _Provider()
    client, _ = _client(monkeypatch, provider)
    try:
        decided = client.post("/api/strategy/decide", json={
            "domain_key": "brand_test",
            "strategy_run_id": "f17cc2f4-8adc-4b35-abbb-79a758633900",
            "decision": "approved"})
        assert decided.status_code == 200
        assert provider.decisions == [{
            "domain_key": "brand_test", "user_id": "user-1",
            "run_id": "f17cc2f4-8adc-4b35-abbb-79a758633900",
            "decision": "approved", "note": ""}]
    finally:
        app.dependency_overrides.clear()
