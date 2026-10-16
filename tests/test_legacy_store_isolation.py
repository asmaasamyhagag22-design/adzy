# -*- coding: utf-8 -*-
"""§0 — the Legacy JobStore handle must isolate independent apps sharing one process.

WHY THIS GATE EXISTS. `get_persistence()` is a process-global cached singleton, but the JobStore is
created per app in the lifespan and mounted on `app.state`. To let `LegacyPersistence` answer job
questions through the typed provider surface, it needs a handle to "the store being served".

THE FIRST ATTEMPT WAS A PROCESS-GLOBAL AND THIS GATE KILLED IT. A module-level `_active_store` set
from each app's lifespan is last-writer-wins: mounting a second app rebound the handle, and the
first app stopped seeing its own jobs while it was still serving. Worse, it *appeared* to deny
cross-app reads — but it denied them by clobbering, not by isolating, so the pass was an accident
of ordering rather than a property. The replacement is a ContextVar bound per REQUEST from
`request.app.state.store`.

These tests therefore drive the provider THROUGH HTTP, because that is where the binding happens
and where every real Job route will live. Two probe routes stand in for the job routes that are not
wired yet; they call exactly the provider methods those routes will call.
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from api.job_access import issue_job_capability
from api.jobs.store import active_store, bind_active_store, reset_active_store
from api.main import lifespan
from api.persistence import LegacyPersistence


def _app() -> FastAPI:
    """An independent application instance using the REAL lifespan and the REAL middleware."""
    app = FastAPI(lifespan=lifespan)

    @app.middleware("http")
    async def _bind(request: Request, call_next):
        token = bind_active_store(getattr(request.app.state, "store", None))
        try:
            return await call_next(request)
        finally:
            reset_active_store(token)

    @app.post("/_probe/create")
    async def _create(request: Request) -> dict:
        digest = request.headers["x-cap"]
        return {"job_id": LegacyPersistence().create_capability_job(capability_digest=digest)}

    @app.get("/_probe/{job_id}")
    async def _read(job_id: str, request: Request) -> dict:
        digest = request.headers["x-cap"]
        snapshot = LegacyPersistence().job_snapshot(job_id=job_id, principals=[digest])
        return {"found": snapshot is not None}

    return app


def _make(client: TestClient) -> tuple[str, str]:
    _raw, principal = issue_job_capability()
    job_id = client.post("/_probe/create", headers={"x-cap": principal}).json()["job_id"]
    return job_id, principal


# =====================================================================
# the gate
# =====================================================================

def test_two_apps_in_one_process_do_not_share_a_job_store():
    """Both apps serving simultaneously — the real shape, not one politely after the other."""
    app_a, app_b = _app(), _app()

    with TestClient(app_a) as client_a, TestClient(app_b) as client_b:
        assert app_a.state.store is not app_b.state.store, "precondition: different stores"
        job_a, cap_a = _make(client_a)
        job_b, cap_b = _make(client_b)

        assert client_a.get(f"/_probe/{job_a}", headers={"x-cap": cap_a}).json()["found"] is True
        assert client_b.get(f"/_probe/{job_b}", headers={"x-cap": cap_b}).json()["found"] is True

        # The direction that actually leaks data.
        assert client_b.get(f"/_probe/{job_a}", headers={"x-cap": cap_a}).json()["found"] is False,\
            "app B answered for a job belonging to app A's store"
        assert client_a.get(f"/_probe/{job_b}", headers={"x-cap": cap_b}).json()["found"] is False,\
            "app A answered for a job belonging to app B's store"


def test_a_second_app_does_not_silence_a_still_running_first_app():
    """The exact defect the process-global had: B's presence blinded A to its own jobs."""
    app_a, app_b = _app(), _app()

    with TestClient(app_a) as client_a:
        job_a, cap_a = _make(client_a)
        with TestClient(app_b) as client_b:
            _make(client_b)                       # B starts, serves, and shuts down inside A
        assert client_a.get(f"/_probe/{job_a}", headers={"x-cap": cap_a}).json()["found"] is True,\
            "app A lost its own job because another app started and stopped"


def test_interleaved_requests_across_apps_each_see_their_own_store():
    """Not just first-then-second: alternate, so a sticky binding would show up."""
    app_a, app_b = _app(), _app()

    with TestClient(app_a) as client_a, TestClient(app_b) as client_b:
        job_a, cap_a = _make(client_a)
        job_b, cap_b = _make(client_b)
        for _ in range(3):
            assert client_a.get(f"/_probe/{job_a}", headers={"x-cap": cap_a}).json()["found"]
            assert client_b.get(f"/_probe/{job_b}", headers={"x-cap": cap_b}).json()["found"]
            assert not client_a.get(f"/_probe/{job_b}", headers={"x-cap": cap_b}).json()["found"]
            assert not client_b.get(f"/_probe/{job_a}", headers={"x-cap": cap_a}).json()["found"]


def test_the_binding_does_not_outlive_the_request():
    """A handle left bound after a response would make the next caller inherit someone's store."""
    app = _app()
    with TestClient(app) as client:
        _make(client)
        assert active_store() is None, "the store stayed bound after the request completed"


def test_no_store_bound_answers_none_rather_than_raising():
    """Outside a request, 'no such job' is the honest answer — not an exception into a caller."""
    provider = LegacyPersistence()
    _raw, principal = issue_job_capability()
    assert active_store() is None, "precondition: nothing bound"
    assert provider.job_snapshot(job_id="whatever", principals=[principal]) is None
    assert provider.job_result(job_id="whatever", principals=[principal]) is None
    assert provider.job_events_since(job_id="whatever", principals=[principal]) is None
    assert provider.job_is_terminal(job_id="whatever", principals=[principal]) is False


def test_creating_a_job_outside_a_request_is_refused_not_silently_dropped():
    """A create with nowhere to put the job must fail loudly."""
    from api.persistence import BackendMisconfigured

    provider = LegacyPersistence()
    with pytest.raises(BackendMisconfigured, match="no JobStore"):
        provider.create_capability_job(capability_digest="capability:deadbeef")


# =====================================================================
# background work started during a request keeps that request's store
# =====================================================================

def test_a_task_spawned_during_a_request_keeps_that_requests_store():
    """`asyncio.create_task` copies the context, which is what job runners rely on.

    A background runner started by a route must keep writing to the store its request was serving,
    even though the request has already returned and unbound its own token.
    """
    import asyncio

    app = _app()
    seen: dict = {}

    @app.post("/_spawn")
    async def _spawn(request: Request) -> dict:
        digest = request.headers["x-cap"]
        job_id = LegacyPersistence().create_capability_job(capability_digest=digest)
        done = asyncio.Event()

        async def _later():
            # Runs AFTER the request has returned and reset its own binding.
            await asyncio.sleep(0)
            seen["found"] = LegacyPersistence().job_snapshot(
                job_id=job_id, principals=[digest]) is not None
            done.set()

        request.app.state.spawned = (asyncio.create_task(_later()), done)
        return {"job_id": job_id}

    @app.get("/_spawned")
    async def _await_spawned(request: Request) -> dict:
        _task, done = request.app.state.spawned
        await asyncio.wait_for(done.wait(), timeout=5)
        return {"found": seen.get("found")}

    with TestClient(app) as client:
        _raw, principal = issue_job_capability()
        client.post("/_spawn", headers={"x-cap": principal})
        assert client.get("/_spawned").json()["found"] is True, (
            "a background task lost the store its request was serving")
