# -*- coding: utf-8 -*-
"""Database Gate 1 K: the selected PostgreSQL authority must own application startup."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import api.main as main
import api.persistence as persistence


@pytest.fixture(autouse=True)
def _fresh_persistence():
    from db.session import dispose

    persistence.reset_persistence()
    dispose()
    yield
    persistence.reset_persistence()
    dispose()


def _schema_absence_failure(monkeypatch):
    class UndefinedTable(RuntimeError):
        pass

    cause = UndefinedTable('relation "alembic_version" does not exist (SQLSTATE 42P01)')

    def fail_readiness():
        raise persistence.BackendMisconfigured(
            "PERSISTENCE_BACKEND=postgres but the schema could not be verified. "
            "Refusing to fall back to the legacy stores."
        ) from cause

    monkeypatch.setenv(persistence.ENV_BACKEND, persistence.POSTGRES)
    monkeypatch.setenv(
        "BASEERA_DATABASE_URL",
        "postgresql+psycopg://nobody:nothing@127.0.0.1:1/schema_absent",
    )
    monkeypatch.setattr(persistence, "verify_postgres_ready", fail_readiness)
    return cause


def test_explicit_postgres_readiness_failure_aborts_startup_and_preserves_cause(monkeypatch):
    cause = _schema_absence_failure(monkeypatch)

    with pytest.raises(persistence.BackendMisconfigured) as caught:
        with TestClient(main.app):
            pytest.fail("the application served after PostgreSQL readiness failed")

    assert caught.value.__cause__ is cause
    assert "Refusing to fall back" in str(caught.value)
    assert "42P01" in str(caught.value.__cause__)


def test_explicit_postgres_readiness_failure_never_constructs_legacy(monkeypatch):
    _schema_absence_failure(monkeypatch)
    legacy_constructions: list[bool] = []

    def forbidden_legacy():
        legacy_constructions.append(True)
        raise AssertionError("legacy fallback was constructed")

    monkeypatch.setattr(persistence, "LegacyPersistence", forbidden_legacy)

    with pytest.raises(persistence.BackendMisconfigured):
        with TestClient(main.app):
            pytest.fail("the application served after PostgreSQL readiness failed")

    assert legacy_constructions == []


def test_explicit_postgres_readiness_success_allows_startup(monkeypatch):
    readiness_calls: list[str] = []
    reconcile_calls: list[str] = []

    monkeypatch.setenv(persistence.ENV_BACKEND, persistence.POSTGRES)
    monkeypatch.setenv(
        "BASEERA_DATABASE_URL",
        "postgresql+psycopg://nobody:nothing@127.0.0.1:1/readiness_was_stubbed",
    )

    def ready():
        readiness_calls.append(persistence.POSTGRES)
        return persistence.EXPECTED_SCHEMA_HEAD

    def reconcile(_self):
        reconcile_calls.append(persistence.POSTGRES)
        return {"class_a": 0, "class_b": 0, "scanned": 0}

    monkeypatch.setattr(persistence, "verify_postgres_ready", ready)
    monkeypatch.setattr(persistence.PostgresPersistence, "reconcile_jobs", reconcile)

    with TestClient(main.app) as client:
        response = client.get("/api/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert readiness_calls == [persistence.POSTGRES]
    assert reconcile_calls == [persistence.POSTGRES]


def test_legacy_startup_behavior_is_unchanged(monkeypatch):
    monkeypatch.delenv(persistence.ENV_BACKEND, raising=False)
    monkeypatch.setenv(
        "BASEERA_DATABASE_URL",
        "postgresql+psycopg://nobody:nothing@127.0.0.1:1/must_not_be_used",
    )

    def database_must_not_be_checked():
        raise AssertionError("legacy startup attempted PostgreSQL readiness")

    def legacy_reconciliation_failure(_self):
        raise RuntimeError("legacy reconciliation remains best-effort")

    monkeypatch.setattr(persistence, "verify_postgres_ready", database_must_not_be_checked)
    monkeypatch.setattr(
        persistence.LegacyPersistence,
        "reconcile_jobs",
        legacy_reconciliation_failure,
    )

    with TestClient(main.app) as client:
        response = client.get("/api/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"
