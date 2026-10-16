# -*- coding: utf-8 -*-
"""§B groundwork — one definition of the public job view, and it must not drift.

WHY THIS EXISTS. `api/routes/jobs.py` owned the stage->progress table and the status map, and read
them off an in-memory `Job`. A PostgreSQL-backed job has to answer the SAME three fields from rows.
If each backend derived them separately the failure would be silent: the SPA polls until `status`
is `done` or `error`, so a backend that reached `done` one stage early would simply finish the
progress bar at the wrong time, on one backend, with neither store contradicting the other.

So the tables moved to `api/job_projection.py` and both backends project through it. These tests
pin the two things that make that safe: the tables still cover every enum member the application
can actually produce, and the route no longer carries a second copy.
"""
from __future__ import annotations

import pytest

from api.job_projection import (
    STAGE_PROGRESS, STATUS_MAP, TERMINAL_STATUSES, project_event, project_job,
)
from api.schemas import JobStatus, Stage, StageStatus


# =====================================================================
# the tables describe the application, not a subset of it
# =====================================================================

def test_every_stage_the_pipeline_can_emit_has_a_progress_and_a_label():
    """A stage with no entry silently projects as 'Starting' at 5% — a stalled-looking live job."""
    missing = sorted({s.value for s in Stage} - set(STAGE_PROGRESS))
    assert not missing, f"stages with no progress mapping: {missing}"


def test_every_job_status_projects_to_a_client_status():
    """An unmapped status defaults to 'running', which tells the SPA to poll a job forever."""
    missing = sorted({s.value for s in JobStatus} - set(STATUS_MAP))
    assert not missing, f"statuses with no client projection: {missing}"


def test_terminal_statuses_and_the_client_projection_agree():
    """A status that reads as finished must BE finished, or the SPA stops polling a moving job."""
    for status, projected in STATUS_MAP.items():
        finished = projected in ("done", "error")
        assert finished == (status in TERMINAL_STATUSES), (
            f"{status!r} projects to {projected!r} but terminal={status in TERMINAL_STATUSES}")


# =====================================================================
# the projection itself
# =====================================================================

@pytest.mark.parametrize("stage,expected_progress", [
    (Stage.SCRAPE, 15), (Stage.RULES, 35), (Stage.EVIDENCE_PACK, 50),
    (Stage.LLM, 75), (Stage.VALIDATE, 85), (Stage.MERGE, 92), (Stage.FINAL, 100),
])
def test_a_running_job_reports_its_stage_progress(stage, expected_progress):
    view = project_job(job_id="j", status=JobStatus.RUNNING, current_stage=stage)
    assert view["status"] == "running"
    assert view["progress"] == expected_progress


def test_a_succeeded_job_is_pinned_to_done_regardless_of_stage():
    """The terminal answer overrides the stage; a succeeded job at stage 'llm' is still 100%."""
    view = project_job(job_id="j", status=JobStatus.SUCCEEDED, current_stage=Stage.LLM)
    assert (view["status"], view["progress"], view["step"]) == ("done", 100, "Done")


def test_a_failed_job_says_failed_and_keeps_its_error():
    view = project_job(job_id="j", status=JobStatus.FAILED, current_stage=Stage.LLM,
                       error="provider refused")
    assert view["status"] == "error"
    assert view["step"] == "Failed"
    assert view["error"] == "provider refused"


def test_an_unknown_stage_degrades_to_starting_rather_than_raising():
    """An unrecognised stage must not 500 a polling route."""
    view = project_job(job_id="j", status="running", current_stage="not_a_stage")
    assert (view["progress"], view["step"]) == (5, "Starting")


def test_the_projection_accepts_enums_and_plain_strings_alike():
    """The durable store speaks strings, the legacy store enums; one projection serves both."""
    from_enum = project_job(job_id="j", status=JobStatus.RUNNING, current_stage=Stage.MERGE)
    from_text = project_job(job_id="j", status="running", current_stage="merge")
    assert from_enum == from_text


def test_absent_optional_fields_normalise_to_none_not_empty_string():
    """The SPA tests these for truthiness; '' and None must not be two different answers."""
    view = project_job(job_id="j", status="queued", domain_key="", warning="", error="")
    assert view["domain_key"] is None and view["warning"] is None and view["error"] is None


# =====================================================================
# events carry seq, because timestamps cannot order them
# =====================================================================

def test_an_event_carries_its_sequence_for_resumption():
    """Two events committed in one transaction share a timestamp; only seq orders them."""
    from datetime import datetime, timezone

    stamp = datetime(2026, 8, 18, 12, 0, 0, tzinfo=timezone.utc)
    first = project_event(stage=Stage.LLM, status=StageStatus.STARTED, occurred_at=stamp, seq=7)
    second = project_event(stage=Stage.LLM, status=StageStatus.DONE, occurred_at=stamp, seq=8)
    assert first["timestamp"] == second["timestamp"], "precondition: same instant"
    assert (first["seq"], second["seq"]) == (7, 8)


def test_an_event_projects_every_durable_field():
    """§B4 durability is only useful if the fields survive the projection to the client."""
    from datetime import datetime, timezone

    event = project_event(
        stage=Stage.VALIDATE, status=StageStatus.ERROR,
        occurred_at=datetime(2026, 8, 18, tzinfo=timezone.utc),
        duration_ms=1234, payload={"k": "v"}, error="bad", seq=3)
    assert event["stage"] == "validate" and event["status"] == "error"
    assert event["duration_ms"] == 1234
    assert event["payload"] == {"k": "v"}
    assert event["error"] == "bad"


# =====================================================================
# there is exactly one copy
# =====================================================================

def test_the_route_module_does_not_keep_a_second_copy_of_the_tables():
    """The whole point of the module: a second table is a second truth."""
    import api.routes.jobs as jobs_route

    assert not hasattr(jobs_route, "_STAGE_PROGRESS"), (
        "api/routes/jobs.py still defines its own stage->progress table")
    assert not hasattr(jobs_route, "_STATUS_MAP"), (
        "api/routes/jobs.py still defines its own status map")


def test_the_route_projects_through_the_shared_definition():
    """Proved by identity, not by reading the source: the snapshot the route serves IS project_job.

    The route no longer projects anything itself — it asks the provider for a snapshot and returns
    it — so the identity is asserted one layer down, where the projection now happens. Both are the
    same claim: there is one definition of what a job looks like to a client.
    """
    from api.jobs.store import JobStore, serving_store
    from api.persistence import LegacyPersistence

    store = JobStore(ttl_seconds=60)
    with serving_store(store):
        job = store.create(owner_principal="user:abc")
        job.domain_key = "example_com"
        job.status = JobStatus.RUNNING
        job.current_stage = Stage.EVIDENCE_PACK
        snapshot = LegacyPersistence().job_snapshot(job_id=job.job_id,
                                                   principals=["user:abc"])

    assert snapshot == project_job(
        job_id=job.job_id, status=JobStatus.RUNNING, current_stage=Stage.EVIDENCE_PACK,
        domain_key="example_com", warning=None, error=None)


def test_the_route_holds_no_projection_of_its_own():
    """The route may not rebuild the client view — it returns what the provider projected."""
    import inspect

    import api.routes.jobs as jobs_route

    source = inspect.getsource(jobs_route.get_job)
    assert "job_snapshot" in source, "the poll route no longer asks the provider for the snapshot"
    assert "STAGE_PROGRESS" not in source and "STATUS_MAP" not in source


def test_both_backends_project_job_views_through_this_module():
    """Neither backend may build the client view by hand."""
    import inspect

    from api.persistence import LegacyPersistence, PostgresPersistence

    for backend in (LegacyPersistence, PostgresPersistence):
        source = inspect.getsource(backend.job_snapshot)
        assert "project_job" in source, (
            f"{backend.__name__}.job_snapshot does not use the shared projection")
