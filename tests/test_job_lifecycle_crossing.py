# -*- coding: utf-8 -*-
"""The Job lifecycle, crossed to PostgreSQL and proved end to end over real HTTP.

Real routes, real `PERSISTENCE_BACKEND=postgres`, a real disposable PostgreSQL. Only the crawl and
the model calls are replaced — they cost money, reach the network, and §51 requires them outside any
transaction anyway, so mocking them hides no database interaction.

WHAT THIS FILE EXISTS TO CATCH. Moving the Job READS onto the provider without the writers was
tried, passed its own targeted suite, and broke the application: the write side still made 16-hex
in-memory ids, a durable read looked for them in PostgreSQL, and every poll 404ed forever. So the
question here is never "does this endpoint work" but "do the writer and the reader agree about
which store holds this job" — which is why every test drives a real HTTP workflow rather than
calling a provider method.

  §1/§2  the job is created BEFORE any external work, through the provider, with a durable id
  §3     progress is durable: events land in `job_events` and move the polled projection
  §4     success is atomic: business result + terminal status + terminal event, one commit
  §5     failure is truthful: an ERROR event, a failed job, and NO successful result
  §6/§7  polling is PostgreSQL-backed and projects the same public contract as legacy
  §8     the result route asks for THE result and never learns which shape produced it
  §9     SSE replays the durable log, ordered by seq
  §10    capability jobs: digest only, and a foreign token cannot reach them
  §11    zero JobStore Job authority under postgres
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import types
import uuid
from pathlib import Path

_fake = types.ModuleType("playwright")
_fake_sync = types.ModuleType("playwright.sync_api")


class _Stub: ...


for _n in ("Browser", "BrowserContext", "Page", "sync_playwright"):
    setattr(_fake_sync, _n, _Stub)
_fake_sync.TimeoutError = type("TimeoutError", (Exception,), {})
_fake_sync.Error = type("Error", (Exception,), {})
sys.modules.setdefault("playwright", _fake)
sys.modules.setdefault("playwright.sync_api", _fake_sync)

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from api.persistence import ENV_BACKEND, POSTGRES, reset_persistence  # noqa: E402

_ROOT = Path(__file__).resolve().parents[1]
_DSN = os.environ.get("BASEERA_TEST_DATABASE_URL", "")

pytestmark = pytest.mark.skipif(
    not _DSN, reason="BASEERA_TEST_DATABASE_URL is required for the job cutover proof")


# =====================================================================
# harness
# =====================================================================

def _migrate() -> None:
    """Provision the disposable database as an OPERATOR would. The app never migrates itself (§9)."""
    inherited = os.environ.get("PYTHONPATH", "")
    pythonpath = os.pathsep.join(p for p in (inherited, str(_ROOT)) if p)
    proc = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=str(_ROOT), capture_output=True, text=True, timeout=300,
        env={**os.environ, "PYTHONPATH": pythonpath, "BASEERA_DATABASE_URL": _DSN})
    assert proc.returncode == 0, f"could not provision the database:\n{proc.stderr[-2000:]}"


@pytest.fixture()
def client(tmp_path, monkeypatch):
    pytest.importorskip("sqlalchemy", reason="SQLAlchemy is not installed")
    _migrate()
    monkeypatch.setenv("BASEERA_WEBAPP_DIR", str(tmp_path / "webapp"))
    monkeypatch.setenv("BASEERA_AUTH_SECRET", "test-secret-crossing")
    monkeypatch.setenv(ENV_BACKEND, POSTGRES)
    monkeypatch.setenv("BASEERA_DATABASE_URL", _DSN)
    import api.user_auth as ua
    ua._SECRET_CACHE = None
    from db.session import dispose
    dispose()
    reset_persistence()
    from api.main import app
    with TestClient(app) as test_client:
        yield test_client
    dispose()
    reset_persistence()


@pytest.fixture()
def no_legacy_writes(monkeypatch):
    """Fails the test if ANY legacy authoritative write happens while postgres is selected (§40)."""
    import api.brand_store as bs
    import api.user_auth as ua

    calls: list[str] = []
    for module, cls, methods in (
            (bs, "BrandStore", ("save", "upsert", "update", "mutate", "replace_block", "delete",
                                "_persist_locked")),
            (ua, "UserStore", ("create", "_save")),
    ):
        target = getattr(module, cls)
        for method in methods:
            if hasattr(target, method):
                monkeypatch.setattr(
                    target, method,
                    (lambda n: lambda *a, **k: calls.append(n))(f"{cls}.{method}"),
                    raising=False)
    monkeypatch.setattr(bs, "record_history",
                        lambda *a, **k: calls.append("record_history"), raising=False)
    yield calls
    assert calls == [], f"postgres mode reached the legacy stores: {calls}"


def _account(client, email: str = "") -> tuple[dict, str]:
    address = email or f"jx{uuid.uuid4().hex[:8]}@test.local"
    response = client.post("/api/auth/signup",
                           json={"email": address, "password": "password123", "full_name": "JX"})
    assert response.status_code == 200, response.text
    body = response.json()
    return {"Authorization": f"Bearer {body['access_token']}"}, body["user"]["id"]


_PROFILE = {"name": {"value": "Synthetic"}, "primary_language": "en"}


def _fake_pipeline(profile: dict | None = None, *, fail: bool = False):
    """Stands in for crawl + LLM. Lands a profile exactly the way the real runner does."""
    payload = dict(profile or _PROFILE)

    def pipeline(job_id, _url, recorder, **kwargs):
        from datetime import datetime, timezone

        from api.schemas import Stage, StageEvent, StageStatus

        def emit(stage, status, **kw):
            recorder.emit_threadsafe(job_id, StageEvent(
                job_id=job_id, stage=stage, status=status,
                timestamp=datetime.now(timezone.utc), **kw))

        emit(Stage.SCRAPE, StageStatus.STARTED)
        emit(Stage.SCRAPE, StageStatus.DONE, payload={"pages_succeeded": 3})
        emit(Stage.RULES, StageStatus.STARTED)
        if fail:
            # Exactly what the real runner does on a stage failure: one ERROR event, then stop.
            emit(Stage.RULES, StageStatus.ERROR, error="rules stage failed (RuntimeError)",
                 payload={"error_type": "RuntimeError"})
            return None
        emit(Stage.RULES, StageStatus.DONE, payload={"fields_extracted": 4})
        recorder.set_result_threadsafe(job_id, dict(payload))
        return {"ready_for_strategy": False, "profile_path": None}

    return pipeline


def _scrape(client, monkeypatch, url: str, headers: dict, *, profile=None, fail=False):
    from api.routes import scrape as scrape_route

    monkeypatch.setattr(scrape_route, "run_pipeline_job", _fake_pipeline(profile, fail=fail))
    monkeypatch.setattr(scrape_route, "has_llm", lambda: False)
    return client.post("/api/scrape", json={"url": url}, headers=headers)


def _run(client, monkeypatch, url: str, *, profile=None, fail=False):
    from api.routes import run as run_route

    monkeypatch.setattr(run_route, "run_pipeline_job", _fake_pipeline(profile, fail=fail))
    monkeypatch.setattr(run_route, "has_llm", lambda: True)
    return client.post("/api/run", json={"url": url, "skip_llm": False})


def _wait(client, job_id: str, headers: dict, tries: int = 300) -> dict:
    import time

    for _ in range(tries):
        body = client.get(f"/api/jobs/{job_id}", headers=headers).json()
        if body.get("status") in ("done", "error"):
            return body
        time.sleep(0.05)
    raise AssertionError(f"job {job_id} never reached a terminal state")


def _domain_key(url: str) -> str:
    from api.brand_translate import domain_key_from_url
    return domain_key_from_url(url)


def _job_row(job_id: str):
    from uuid import UUID

    from db.models import Job
    from db.session import get_session_factory

    with get_session_factory()() as session:
        return session.get(Job, UUID(job_id))


def _events(job_id: str) -> list:
    from uuid import UUID

    from sqlalchemy import select

    from db.models import JobEvent
    from db.session import get_session_factory

    with get_session_factory()() as session:
        return list(session.scalars(
            select(JobEvent).where(JobEvent.job_id == UUID(job_id)).order_by(JobEvent.seq)).all())


def _stream(client, job_id: str, headers: dict) -> list[dict]:
    with client.stream("GET", f"/api/jobs/{job_id}/stream", headers=headers) as response:
        assert response.status_code == 200, response.status_code
        raw = b"".join(response.iter_bytes()).decode("utf-8", errors="replace")
    out = []
    for line in raw.splitlines():
        if line.startswith("data: "):
            try:
                out.append(json.loads(line[len("data: "):]))
            except json.JSONDecodeError:
                pass
    return out


# =====================================================================
# §11 — zero JobStore authority
# =====================================================================

def test_a_postgres_scrape_puts_no_job_in_the_in_memory_store(client, monkeypatch,
                                                              no_legacy_writes):
    """The in-memory store is still MOUNTED (the app builds one either way) and stays EMPTY.

    That distinction is the whole point of §11: LegacyPersistence may use the JobStore, but under
    postgres nothing may. An assertion that the store object is absent would pass for the wrong
    reason on any refactor that stopped mounting it; an assertion that it holds no jobs is about
    authority.
    """
    headers, _ = _account(client)
    url = f"https://xa{uuid.uuid4().hex[:8]}.test/"

    response = _scrape(client, monkeypatch, url, headers)
    assert response.status_code == 200, response.text
    job_id = response.json()["job_id"]
    assert _wait(client, job_id, headers)["status"] == "done"

    store = client.app.state.store
    assert store is not None, "the app no longer mounts a JobStore at all"
    assert store.all_job_ids() == [], (
        f"postgres mode created jobs in the in-memory store: {store.all_job_ids()}")


def test_the_job_id_the_client_polls_is_the_durable_row(client, monkeypatch, no_legacy_writes):
    """One id, not two. The measured failure was a 16-hex handle answered from a UUID store."""
    headers, _ = _account(client)
    url = f"https://xb{uuid.uuid4().hex[:8]}.test/"

    job_id = _scrape(client, monkeypatch, url, headers).json()["job_id"]
    assert uuid.UUID(job_id), "the client-facing job id is not a durable identifier"
    row = _job_row(job_id)
    assert row is not None and row.requested_domain_key == _domain_key(url)


# =====================================================================
# §1-§4, §6-§9 — the whole workflow, one job
# =====================================================================

def test_the_full_scrape_workflow_is_served_from_postgres(client, monkeypatch, no_legacy_writes):
    """auth → create → running → progress → business result → terminal → poll → result → SSE."""
    headers, _ = _account(client)
    url = f"https://xc{uuid.uuid4().hex[:8]}.test/"
    profile = {"name": {"value": "Crossing"}, "primary_language": "en"}

    job_id = _scrape(client, monkeypatch, url, headers, profile=profile).json()["job_id"]

    # §2 the durable job exists before any business row does.
    assert _job_row(job_id).status in ("queued", "running")

    poll = _wait(client, job_id, headers)
    assert poll["status"] == "done" and poll["progress"] == 100 and poll["step"] == "Done"
    assert poll["domain_key"] == _domain_key(url)

    # §4 business result, terminal status and terminal event are all present, and there is
    # exactly ONE terminal event.
    row = _job_row(job_id)
    assert row.status == "succeeded" and row.brand_id is not None and row.run_id is not None
    terminal = [e for e in _events(job_id) if e.is_terminal]
    assert len(terminal) == 1, [(e.stage, e.status) for e in terminal]
    assert (terminal[0].stage, terminal[0].status) == ("final", "done")

    # §3 progress landed durably, in sequence order.
    stages = [(e.stage, e.status) for e in _events(job_id)]
    assert ("scrape", "started") in stages and ("rules", "done") in stages
    assert [e.seq for e in _events(job_id)] == sorted(e.seq for e in _events(job_id))

    # §8 the result comes back, and it is the profile the pipeline produced.
    result = client.get(f"/api/jobs/{job_id}/result", headers=headers)
    assert result.status_code == 200, result.text
    assert result.json()["profile"]["name"]["value"] == "Crossing"

    # §9 SSE replays the durable log for a job that already finished.
    events = _stream(client, job_id, headers)
    assert [(e["stage"], e["status"]) for e in events][:2] == [("scrape", "started"),
                                                               ("scrape", "done")]
    assert ("final", "done") in [(e["stage"], e["status"]) for e in events]
    assert [e["seq"] for e in events] == sorted(e["seq"] for e in events)
    keys = [(e["stage"], e["status"], e["timestamp"]) for e in events]
    assert len(keys) == len(set(keys)), f"the stream published a duplicate: {keys}"


def test_a_failed_scrape_is_truthful_everywhere(client, monkeypatch, no_legacy_writes):
    """§5: an ERROR event, a failed job, no Brand, no Run — and no successful result to serve."""
    headers, _ = _account(client)
    url = f"https://xd{uuid.uuid4().hex[:8]}.test/"

    job_id = _scrape(client, monkeypatch, url, headers, fail=True).json()["job_id"]
    poll = _wait(client, job_id, headers)
    assert poll["status"] == "error" and poll["step"] == "Failed"

    row = _job_row(job_id)
    assert row.status == "failed" and row.brand_id is None and row.run_id is None
    assert row.error

    terminal = [e for e in _events(job_id) if e.is_terminal]
    assert len(terminal) == 1 and terminal[0].status == "error"

    result = client.get(f"/api/jobs/{job_id}/result", headers=headers)
    assert result.status_code == 500, result.text
    assert "failed" in result.json()["detail"]

    from api.brand_translate import domain_key_from_url  # noqa: F401
    from sqlalchemy import select

    from db.models import Brand
    from db.session import get_session_factory
    with get_session_factory()() as session:
        assert session.scalars(
            select(Brand).where(Brand.domain_key == _domain_key(url))).first() is None


def test_a_running_job_has_no_result_and_says_so(client, monkeypatch, no_legacy_writes):
    """§8: queued/running must never produce a completed result."""
    import threading

    from api.routes import scrape as scrape_route

    release = threading.Event()
    started = threading.Event()

    def slow_pipeline(job_id, _url, recorder, **kwargs):
        started.set()
        release.wait(20)
        return _fake_pipeline()(job_id, _url, recorder, **kwargs)

    headers, _ = _account(client)
    url = f"https://xe{uuid.uuid4().hex[:8]}.test/"
    monkeypatch.setattr(scrape_route, "run_pipeline_job", slow_pipeline)
    monkeypatch.setattr(scrape_route, "has_llm", lambda: False)
    job_id = client.post("/api/scrape", json={"url": url}, headers=headers).json()["job_id"]

    assert started.wait(20), "the pipeline never started"
    poll = client.get(f"/api/jobs/{job_id}", headers=headers).json()
    assert poll["status"] == "running"
    result = client.get(f"/api/jobs/{job_id}/result", headers=headers)
    assert result.status_code == 409, result.text
    release.set()
    assert _wait(client, job_id, headers)["status"] == "done"


# =====================================================================
# §10 — capability jobs
# =====================================================================

def test_a_capability_job_completes_and_serves_its_durable_result(client, monkeypatch):
    """`/api/run` has no user. Its job is owned by a DIGEST and its result is a `job_results` row."""
    url = f"https://xf{uuid.uuid4().hex[:8]}.test/"
    profile = {"name": {"value": "Capability"}, "primary_language": "en"}

    response = _run(client, monkeypatch, url, profile=profile)
    assert response.status_code == 200, response.text
    body = response.json()
    job_id, token = body["job_id"], body["job_token"]
    headers = {"X-Job-Token": token}

    assert uuid.UUID(job_id)
    assert _wait(client, job_id, headers)["status"] == "done"

    row = _job_row(job_id)
    assert row.access_mode == "capability"
    assert row.owner_user_id is None and row.run_id is None
    # The raw token is nowhere; only its digest, and the digest is not the token.
    assert row.capability_digest and row.capability_digest != token
    assert token not in json.dumps({"digest": row.capability_digest})

    result = client.get(f"/api/jobs/{job_id}/result", headers=headers)
    assert result.status_code == 200, result.text
    assert result.json()["profile"]["name"]["value"] == "Capability"

    from uuid import UUID

    from db.models import JobResult
    from db.session import get_session_factory
    with get_session_factory()() as session:
        stored = session.get(JobResult, UUID(job_id))
    assert stored is not None and stored.result_kind == "business_profile"


def test_a_capability_job_is_unreachable_without_its_own_token(client, monkeypatch):
    """Authorization and Job state are on the SAME backend, so a foreign token gets 404 — the same
    answer an absent job gets, because whether it exists is not the caller's information."""
    body = _run(client, monkeypatch, f"https://xg{uuid.uuid4().hex[:8]}.test/").json()
    job_id = body["job_id"]
    mine = {"X-Job-Token": body["job_token"]}
    assert _wait(client, job_id, mine)["status"] == "done"

    for foreign in ({}, {"X-Job-Token": "b" * 43}):
        assert client.get(f"/api/jobs/{job_id}", headers=foreign).status_code == 404
        assert client.get(f"/api/jobs/{job_id}/result", headers=foreign).status_code == 404
        assert client.get(f"/api/jobs/{job_id}/stream", headers=foreign).status_code == 404

    # A signed-in USER cannot reach a capability job either: the lookup that would find it is
    # never issued for a `user:` principal.
    other, _ = _account(client)
    assert client.get(f"/api/jobs/{job_id}", headers=other).status_code == 404


def test_a_failed_capability_job_stores_no_result(client, monkeypatch):
    """§5/§8: no successful durable result may exist for a job that failed."""
    from uuid import UUID

    from db.models import JobResult
    from db.session import get_session_factory

    body = _run(client, monkeypatch, f"https://xh{uuid.uuid4().hex[:8]}.test/", fail=True).json()
    job_id, headers = body["job_id"], {"X-Job-Token": body["job_token"]}
    assert _wait(client, job_id, headers)["status"] == "error"

    assert _job_row(job_id).status == "failed"
    with get_session_factory()() as session:
        assert session.get(JobResult, UUID(job_id)) is None
    assert client.get(f"/api/jobs/{job_id}/result", headers=headers).status_code == 500


# =====================================================================
# §4 — enrichment jobs: one Run, one terminal event, one commit
# =====================================================================

def test_an_enrichment_job_commits_its_run_with_its_terminal_state(client, monkeypatch,
                                                                   no_legacy_writes):
    """The C3 Class-A window, closed on the analysis path: the Run and the Job finish together."""
    from sqlalchemy import select

    from api.routes import analysis as analysis_route
    from db.models import Run
    from db.session import get_session_factory

    headers, _ = _account(client)
    url = f"https://xi{uuid.uuid4().hex[:8]}.test/"
    scrape_job = _scrape(client, monkeypatch, url, headers).json()["job_id"]
    assert _wait(client, scrape_job, headers)["status"] == "done"
    domain_key = _domain_key(url)

    monkeypatch.setattr(analysis_route.analysis, "generate_swot",
                        lambda _brand: {"has_swot": True, "swot": {"strengths": ["s"]}})
    started = client.post("/api/swot/generate", json={"domain_key": domain_key}, headers=headers)
    assert started.status_code == 200, started.text
    job_id = started.json()["job_id"]
    assert uuid.UUID(job_id)
    assert _wait(client, job_id, headers)["status"] == "done"

    row = _job_row(job_id)
    assert row.status == "succeeded" and row.run_id is not None
    with get_session_factory()() as session:
        runs = list(session.scalars(select(Run).where(
            Run.kind == "swot", Run.brand_id == row.brand_id)).all())
    assert len(runs) == 1, f"an enrichment job produced {len(runs)} swot runs"
    assert runs[0].id == row.run_id and runs[0].status == "succeeded"

    terminal = [e for e in _events(job_id) if e.is_terminal]
    assert len(terminal) == 1 and terminal[0].status == "done"

    # And the result the route serves is that Run's output — resolved in the provider, never by
    # the route inspecting `run_id`.
    result = client.get(f"/api/jobs/{job_id}/result", headers=headers)
    assert result.status_code == 200, result.text
    assert result.json()["profile"]["has_swot"] is True


def test_a_failed_enrichment_job_leaves_no_run(client, monkeypatch, no_legacy_writes):
    from sqlalchemy import select

    from api.routes import analysis as analysis_route
    from db.models import Run
    from db.session import get_session_factory

    headers, _ = _account(client)
    url = f"https://xj{uuid.uuid4().hex[:8]}.test/"
    scrape_job = _scrape(client, monkeypatch, url, headers).json()["job_id"]
    assert _wait(client, scrape_job, headers)["status"] == "done"

    def _explode(_brand):
        raise RuntimeError("generation failed")

    monkeypatch.setattr(analysis_route.analysis, "generate_swot", _explode)
    job_id = client.post("/api/swot/generate", json={"domain_key": _domain_key(url)},
                         headers=headers).json()["job_id"]
    assert _wait(client, job_id, headers)["status"] == "error"

    row = _job_row(job_id)
    assert row.status == "failed" and row.run_id is None
    brand_id = _job_row(scrape_job).brand_id
    with get_session_factory()() as session:
        assert list(session.scalars(select(Run).where(
            Run.kind == "swot", Run.brand_id == brand_id)).all()) == []
    terminal = [e for e in _events(job_id) if e.is_terminal]
    assert len(terminal) == 1 and terminal[0].status == "error"


# =====================================================================
# §9 — SSE resumes from the durable log
# =====================================================================

def test_the_stream_replays_the_whole_durable_history_to_a_late_subscriber(
        client, monkeypatch, no_legacy_writes):
    """A reader that connects after the job finished still gets every event, in seq order."""
    headers, _ = _account(client)
    url = f"https://xk{uuid.uuid4().hex[:8]}.test/"
    job_id = _scrape(client, monkeypatch, url, headers).json()["job_id"]
    assert _wait(client, job_id, headers)["status"] == "done"

    durable = [(e.stage, e.status) for e in _events(job_id)]
    streamed = [(e["stage"], e["status"]) for e in _stream(client, job_id, headers)]
    assert streamed == durable, "the stream is not the durable log"


def test_two_identical_events_are_two_rows_and_one_rendered_event(client, monkeypatch):
    """Presentation dedup is presentation-only (§9).

    Two events sharing stage, status and timestamp occupy two valid sequence positions, and the
    record keeps BOTH — position is identity. The SSE projection collapses them for legacy
    compatibility, and that collapse must never reach `JobEventRepository`: an audit trail edited
    to match a rendering detail is an audit trail that lies.
    """
    from datetime import datetime, timezone
    from uuid import UUID

    from api.persistence import get_persistence

    headers, user_id = _account(client)
    provider = get_persistence()
    job_id = provider.create_user_job(owner_user_id=user_id, domain_key="dup_test")
    stamp = datetime.now(timezone.utc)
    for _ in range(2):
        provider._service.append_job_event(job_id=UUID(job_id), stage="rules", status="started",
                                           occurred_at=stamp)

    rows = _events(job_id)
    assert len([r for r in rows if r.stage == "rules"]) == 2, (
        "the durable log dropped one of two events that occupy two valid sequence positions")
    assert sorted(r.seq for r in rows) == [r.seq for r in rows]

    provider.finish_job_failure(job_id=job_id, error="done with this fixture job")
    rendered = [(e["stage"], e["status"]) for e in _stream(client, job_id, headers)]
    assert rendered.count(("rules", "started")) == 1, (
        "the SSE projection published a duplicate the legacy contract collapses")


def test_the_stream_tails_a_live_job_and_closes_on_its_terminal_event(client, monkeypatch,
                                                                      no_legacy_writes):
    """The live half of §9: events reach an OPEN connection while the job is still running, and the
    connection ends on the terminal event rather than hanging until a proxy times it out.

    A replay-only proof would pass with a stream that never tails at all — every event is already
    in the log by the time a finished job is opened.
    """
    import threading

    from api.routes import scrape as scrape_route

    reached, release = threading.Event(), threading.Event()
    base = _fake_pipeline()

    def parked(job_id, url, recorder, **kwargs):
        from datetime import datetime, timezone

        from api.schemas import Stage, StageEvent, StageStatus

        recorder.emit_threadsafe(job_id, StageEvent(
            job_id=job_id, stage=Stage.SCRAPE, status=StageStatus.STARTED,
            timestamp=datetime.now(timezone.utc)))
        reached.set()
        assert release.wait(30), "the stream gate was never released"
        return base(job_id, url, recorder, **kwargs)

    headers, _ = _account(client)
    url = f"https://xm{uuid.uuid4().hex[:8]}.test/"
    monkeypatch.setattr(scrape_route, "run_pipeline_job", parked)
    monkeypatch.setattr(scrape_route, "has_llm", lambda: False)
    job_id = client.post("/api/scrape", json={"url": url}, headers=headers).json()["job_id"]
    assert reached.wait(30), "the pipeline never started"

    collected: list[dict] = []

    def read_stream():
        with client.stream("GET", f"/api/jobs/{job_id}/stream", headers=headers) as response:
            assert response.status_code == 200
            for line in response.iter_lines():
                if isinstance(line, bytes):
                    line = line.decode("utf-8", errors="replace")
                if line.startswith("data: "):
                    try:
                        collected.append(json.loads(line[len("data: "):]))
                    except json.JSONDecodeError:
                        pass

    reader = threading.Thread(target=read_stream, daemon=True)
    reader.start()

    # The connection is open on a job that has NOT finished. Release it and let the tail deliver.
    release.set()
    reader.join(timeout=60)
    assert not reader.is_alive(), "the stream never closed after the job reached terminal"

    seen = [(e["stage"], e["status"]) for e in collected]
    assert seen[-1] == ("final", "done"), seen
    assert ("rules", "done") in seen, seen
    keys = [(e["stage"], e["status"], e["timestamp"]) for e in collected]
    assert len(keys) == len(set(keys)), f"the live tail republished a replayed event: {keys}"
