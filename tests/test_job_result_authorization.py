"""F-07: job status, result, error, and stream are private to their creator.

All assertions exercise the public HTTP routes.  Background work and network resolution are
replaced with local no-op seams; the JobStore state itself stays production-shaped.
"""
from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient

from api.schemas import JobStatus


@pytest.fixture(autouse=True)
def _isolated_webapp(tmp_path, monkeypatch):
    monkeypatch.setenv("BASEERA_WEBAPP_DIR", str(tmp_path / "webapp"))
    monkeypatch.setenv("BASEERA_AUTH_SECRET", "f07-hermetic-auth-secret")
    import api.user_auth as user_auth

    user_auth._SECRET_CACHE = None
    yield
    user_auth._SECRET_CACHE = None


@pytest.fixture
def client():
    from api.main import app

    with TestClient(app) as test_client:
        yield test_client


def _account(client: TestClient, email: str) -> dict[str, str]:
    response = client.post(
        "/api/auth/signup",
        json={"email": email, "password": "password123", "full_name": email},
    )
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


async def _no_scrape_job(**_kwargs) -> None:
    await asyncio.sleep(0)


def _start_user_job(client: TestClient, headers: dict[str, str], monkeypatch) -> str:
    monkeypatch.setattr("api.routes.scrape.is_safe_public_url", lambda _url: True)
    monkeypatch.setattr("api.routes.scrape._run_scrape_job", _no_scrape_job)
    response = client.post(
        "/api/scrape",
        headers=headers,
        json={"url": "https://synthetic-owner.invalid/"},
    )
    assert response.status_code == 200, response.text
    return response.json()["job_id"]


@pytest.fixture
def owned_jobs(client, monkeypatch):
    owner = _account(client, "f07-owner-a@test.local")
    stranger = _account(client, "f07-stranger-b@test.local")

    result_id = _start_user_job(client, owner, monkeypatch)
    error_id = _start_user_job(client, owner, monkeypatch)
    stranger_id = _start_user_job(client, stranger, monkeypatch)

    store = client.app.state.store
    result_job = store.get(result_id)
    result_job.status = JobStatus.SUCCEEDED
    result_job.result = {"private_result": "owner-a-only"}
    result_job.manifest_path = "C:/private/owner-a/manifest.json"
    result_job.profile_path = "C:/private/owner-a/profile.json"

    error_job = store.get(error_id)
    error_job.status = JobStatus.FAILED
    error_job.error = "owner-a-sensitive-error-detail"

    stranger_job = store.get(stranger_id)
    stranger_job.status = JobStatus.SUCCEEDED
    stranger_job.result = {"private_result": "stranger-b-only"}

    return {
        "owner": owner,
        "stranger": stranger,
        "result_id": result_id,
        "error_id": error_id,
        "stranger_id": stranger_id,
    }


def test_owner_can_poll_status_and_retrieve_result(client, owned_jobs):
    owner = owned_jobs["owner"]
    job_id = owned_jobs["result_id"]

    status = client.get(f"/api/jobs/{job_id}", headers=owner)
    result = client.get(f"/api/jobs/{job_id}/result", headers=owner)

    assert status.status_code == 200
    assert status.json()["status"] == "done"
    assert result.status_code == 200
    assert result.json()["profile"] == {"private_result": "owner-a-only"}


@pytest.mark.parametrize("suffix", ["", "/result"])
def test_other_authenticated_user_cannot_read_status_or_result(
        client, owned_jobs, suffix):
    job_id = owned_jobs["result_id"]
    response = client.get(
        f"/api/jobs/{job_id}{suffix}", headers=owned_jobs["stranger"]
    )

    assert response.status_code == 404
    assert "owner-a-only" not in response.text
    assert "manifest" not in response.text.lower()


def test_other_user_cannot_read_error_from_status_or_result(client, owned_jobs):
    job_id = owned_jobs["error_id"]
    for suffix in ("", "/result"):
        response = client.get(
            f"/api/jobs/{job_id}{suffix}", headers=owned_jobs["stranger"]
        )
        assert response.status_code == 404
        assert "owner-a-sensitive-error-detail" not in response.text


@pytest.mark.parametrize(
    ("headers", "expected_status"),
    [
        ({}, 404),
        ({"Authorization": "Bearer malformed-token"}, 401),
        ({"X-Job-Token": "invalid-job-capability"}, 404),
    ],
)
def test_missing_or_invalid_ownership_credential_cannot_read_job(
        client, owned_jobs, headers, expected_status):
    response = client.get(f"/api/jobs/{owned_jobs['result_id']}", headers=headers)
    assert response.status_code == expected_status
    assert "owner-a-only" not in response.text


def test_expired_frontend_identity_gets_session_expired_semantics_without_job_oracle(
        client, owned_jobs):
    headers = {"Authorization": "Bearer malformed-token"}
    existing = client.get(f"/api/jobs/{owned_jobs['result_id']}", headers=headers)
    absent = client.get("/api/jobs/does-not-exist", headers=headers)
    assert existing.status_code == absent.status_code == 401
    assert existing.json() == absent.json()


def test_unauthorized_and_nonexistent_jobs_have_the_same_response(client, owned_jobs):
    stranger = owned_jobs["stranger"]
    forbidden = client.get(f"/api/jobs/{owned_jobs['result_id']}", headers=stranger)
    absent = client.get("/api/jobs/does-not-exist", headers=stranger)
    malformed = client.get("/api/jobs/malformed!!", headers=stranger)

    assert forbidden.status_code == absent.status_code == malformed.status_code == 404
    assert forbidden.json() == absent.json() == malformed.json()


def test_alternate_sse_surface_is_owner_scoped(client, owned_jobs):
    job_id = owned_jobs["result_id"]
    with client.stream(
        "GET", f"/api/jobs/{job_id}/stream", headers=owned_jobs["stranger"]
    ) as forbidden:
        assert forbidden.status_code == 404
        forbidden.read()
        assert "owner-a-only" not in forbidden.text

    with client.stream(
        "GET", f"/api/jobs/{job_id}/stream", headers=owned_jobs["owner"]
    ) as allowed:
        assert allowed.status_code == 200


def test_frontend_polling_automatically_uses_existing_bearer_identity(client, owned_jobs):
    job_id = owned_jobs["result_id"]
    # This is the exact fetch contract used by frontend/index.html's api() helper.
    assert client.get(f"/api/jobs/{job_id}", headers=owned_jobs["owner"]).status_code == 200
    assert client.get(f"/api/jobs/{job_id}", headers=owned_jobs["stranger"]).status_code == 404


async def _no_legacy_run(**_kwargs) -> None:
    await asyncio.sleep(0)


def _start_legacy_job(client: TestClient, monkeypatch) -> dict:
    monkeypatch.setenv("BASEERA_API_KEY", "f07-api-key")
    monkeypatch.setattr("api.routes.run.is_safe_public_url", lambda _url: True)
    monkeypatch.setattr("api.routes.run._run_in_thread", _no_legacy_run)
    response = client.post(
        "/api/run",
        headers={"X-API-Key": "f07-api-key"},
        json={
            "url": "https://synthetic-legacy.invalid/",
            "skip_llm": True,
            "persist_to_disk": False,
        },
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload.get("job_token"), "legacy no-user jobs need a per-job capability"
    return payload


def test_legacy_capability_is_returned_once_and_unlocks_only_its_job(
        client, monkeypatch):
    first = _start_legacy_job(client, monkeypatch)
    second = _start_legacy_job(client, monkeypatch)
    assert first["job_token"] != second["job_token"]
    stored = client.app.state.store.get(first["job_id"])
    assert first["job_token"] != stored.owner_principal
    assert first["job_token"] not in repr(stored)

    own = client.get(
        f"/api/jobs/{first['job_id']}",
        headers={"X-Job-Token": first["job_token"]},
    )
    cross_job = client.get(
        f"/api/jobs/{second['job_id']}",
        headers={"X-Job-Token": first["job_token"]},
    )
    wrong = client.get(
        f"/api/jobs/{first['job_id']}",
        headers={"X-Job-Token": "wrong-capability"},
    )
    missing = client.get(f"/api/jobs/{first['job_id']}")

    assert own.status_code == 200
    assert cross_job.status_code == wrong.status_code == missing.status_code == 404
    assert first["job_token"] not in own.text


def test_legacy_capability_protects_result_and_error_surfaces(client, monkeypatch):
    first = _start_legacy_job(client, monkeypatch)
    second = _start_legacy_job(client, monkeypatch)
    store = client.app.state.store

    result_job = store.get(first["job_id"])
    result_job.status = JobStatus.SUCCEEDED
    result_job.result = {"legacy_private": "first-job-only"}

    error_job = store.get(second["job_id"])
    error_job.status = JobStatus.FAILED
    error_job.error = "second-job-private-error"

    own_result = client.get(
        f"/api/jobs/{first['job_id']}/result",
        headers={"X-Job-Token": first["job_token"]},
    )
    own_error = client.get(
        f"/api/jobs/{second['job_id']}",
        headers={"X-Job-Token": second["job_token"]},
    )
    assert own_result.status_code == 200
    assert own_result.json()["profile"] == {"legacy_private": "first-job-only"}
    assert own_error.status_code == 200
    assert own_error.json()["error"] == "second-job-private-error"

    with client.stream(
        "GET", f"/api/jobs/{first['job_id']}/stream",
        headers={"X-Job-Token": first["job_token"]},
    ) as own_stream:
        assert own_stream.status_code == 200

    for job_id, suffix in (
        (first["job_id"], "/result"),
        (second["job_id"], ""),
        (second["job_id"], "/result"),
    ):
        response = client.get(
            f"/api/jobs/{job_id}{suffix}",
            headers={"X-Job-Token": "unrelated-capability"},
        )
        assert response.status_code == 404
        assert "first-job-only" not in response.text
        assert "second-job-private-error" not in response.text
