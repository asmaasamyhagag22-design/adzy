# -*- coding: utf-8 -*-
"""The API served from PostgreSQL: real routes, real database, external generation mocked.

Everything below goes through FastAPI's own routing and the real `PERSISTENCE_BACKEND=postgres`
provider against a disposable PostgreSQL. Only the crawl and the model calls are replaced — those
are the boundaries that cost money and reach the network, and §51 requires them OUTSIDE any
transaction anyway, so mocking them is not hiding a database interaction.

What these prove, in the order the brief asks for them:

  §44  a first-ever scrape creates the JOB first and the Brand only on success — and a FAILED first
       scrape leaves no Brand behind at all
  §45  the loser of a claim race gets a terminal job, not a raw uniqueness error
  §46  approval is bound to the SWOT that was current when it was granted, so a newer SWOT closes
       the strategy gate again
  §47  two posts on one date is a 409, and the post id targets exactly one
  §48  starting a new influencer set supersedes the old one without destroying it
  §49  archiving a brand hides it without deleting its history
  §40  none of it writes to the legacy stores
"""
from __future__ import annotations

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

postgres = pytest.mark.skipif(
    not _DSN, reason="BASEERA_TEST_DATABASE_URL is required for the postgres route proof")

pytestmark = postgres


# =====================================================================
# harness
# =====================================================================

def _migrate() -> None:
    """Provision the disposable database as an OPERATOR would. The app never migrates itself (§9).

    The migration suites run `downgrade base` against this same database, so the schema may be gone
    by the time this file runs in a full-suite ordering.
    """
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
    monkeypatch.setenv("BASEERA_AUTH_SECRET", "test-secret-2gb")
    monkeypatch.setenv(ENV_BACKEND, POSTGRES)
    monkeypatch.setenv("BASEERA_DATABASE_URL", _DSN)
    import api.user_auth as ua
    ua._SECRET_CACHE = None
    from db.session import dispose
    dispose()
    reset_persistence()
    from api.main import app
    # Context-managed so the lifespan runs: `app.state.store` (the in-memory job store that drives
    # polling and SSE) is created there, and the scrape route needs it.
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
    address = email or f"pg{uuid.uuid4().hex[:8]}@test.local"
    response = client.post("/api/auth/signup",
                           json={"email": address, "password": "password123", "full_name": "PG"})
    assert response.status_code == 200, response.text
    body = response.json()
    return {"Authorization": f"Bearer {body['access_token']}"}, body["user"]["id"]


def _fake_pipeline(profile: dict):
    """Stands in for crawl + LLM. Lands a profile the way the real runner does."""
    def pipeline(job_id, _url, job_store, **kwargs):
        job_store.set_result_threadsafe(job_id, dict(profile))
        return {"ready_for_strategy": False, "profile_path": None}
    return pipeline


def _scrape(client, monkeypatch, url: str, headers: dict, profile: dict | None = None):
    """Drive a full scrape through the real route with the pipeline mocked."""
    from api.routes import scrape as scrape_route

    payload = profile or {"name": {"value": "Synthetic"}, "source_url": url}
    monkeypatch.setattr(scrape_route, "run_pipeline_job", _fake_pipeline(payload))
    monkeypatch.setattr(scrape_route, "has_llm", lambda: False)
    return client.post("/api/scrape", json={"url": url}, headers=headers)


def _wait(client, job_id: str, headers: dict, tries: int = 200) -> dict:
    """Poll the job to a terminal state. The work runs on the app's own loop."""
    import time

    for _ in range(tries):
        body = client.get(f"/api/jobs/{job_id}", headers=headers).json()
        if body.get("status") in ("done", "error"):
            return body
        time.sleep(0.05)
    raise AssertionError(f"job {job_id} never reached a terminal state")


def _durable_jobs(domain_key: str) -> list:
    from sqlalchemy import select

    from db.models import Job
    from db.session import get_session_factory

    with get_session_factory()() as session:
        return list(session.scalars(
            select(Job).where(Job.requested_domain_key == domain_key)).all())


def _brand_rows(domain_key: str) -> list:
    from sqlalchemy import select

    from db.models import Brand
    from db.session import get_session_factory

    with get_session_factory()() as session:
        return list(session.scalars(select(Brand).where(Brand.domain_key == domain_key)).all())


# =====================================================================
# §44 — the first-ever scrape
# =====================================================================

def test_a_first_ever_scrape_creates_the_job_before_the_brand(client, monkeypatch,
                                                              no_legacy_writes):
    headers, _ = _account(client)
    domain = f"first{uuid.uuid4().hex[:8]}_test"
    url = f"https://{domain.replace('_', '')}.test/"

    assert _brand_rows(domain) == [], "a brand existed before the scrape ran"
    response = _scrape(client, monkeypatch, url, headers)
    assert response.status_code == 200, response.text

    key = response.json()["job_id"]
    body = _wait(client, key, headers)
    assert body["status"] == "done", body

    # The durable job carries requested_domain_key and now points at the brand it produced.
    jobs = _durable_jobs(_domain_key(url))
    assert len(jobs) == 1, f"expected exactly one durable job, got {len(jobs)}"
    assert jobs[0].status == "succeeded"
    assert jobs[0].brand_id is not None and jobs[0].run_id is not None
    assert len(_brand_rows(_domain_key(url))) == 1


def _domain_key(url: str) -> str:
    from api.brand_translate import domain_key_from_url
    return domain_key_from_url(url)


def test_a_failed_first_scrape_leaves_a_failed_job_and_no_brand(client, monkeypatch,
                                                                no_legacy_writes):
    """§11/§44: no Brand namespace pollution, and the job does not stay running forever."""
    from api.routes import scrape as scrape_route

    headers, _ = _account(client)
    url = f"https://fail{uuid.uuid4().hex[:8]}.test/"
    key_for = _domain_key(url)

    def exploding_pipeline(job_id, _url, job_store, **kwargs):
        raise RuntimeError("crawl failed")

    monkeypatch.setattr(scrape_route, "run_pipeline_job", exploding_pipeline)
    monkeypatch.setattr(scrape_route, "has_llm", lambda: False)
    response = client.post("/api/scrape", json={"url": url}, headers=headers)
    assert response.status_code == 200
    body = _wait(client, response.json()["job_id"], headers)
    assert body["status"] == "error"

    assert _brand_rows(key_for) == [], "a failed first scrape created a brand"
    jobs = _durable_jobs(key_for)
    assert len(jobs) == 1 and jobs[0].status == "failed", [j.status for j in jobs]
    assert jobs[0].brand_id is None and jobs[0].run_id is None


# =====================================================================
# §45 — the claim race, at the orchestration level
# =====================================================================

def test_the_loser_of_a_claim_race_is_refused_without_a_raw_database_error(
        client, monkeypatch, no_legacy_writes):
    """Two accounts, one unclaimed domain. One wins; the other must be told, not crash.

    The preflight is a cost gate and both callers can pass it, which is exactly why the commit
    transaction has to be the authority. What is under test is the TRANSLATION: the loser sees a
    clean terminal failure rather than a uniqueness violation escaping as a 500.
    """
    first, _ = _account(client)
    second, _ = _account(client)
    url = f"https://race{uuid.uuid4().hex[:8]}.test/"
    key = _domain_key(url)

    won = _scrape(client, monkeypatch, url, first)
    assert _wait(client, won.json()["job_id"], first)["status"] == "done"

    # The second account now meets a brand it does not own: refused up front, and nothing spent.
    blocked = _scrape(client, monkeypatch, url, second)
    assert blocked.status_code == 409, blocked.text
    assert "another account" in blocked.json()["detail"].lower()
    for leak in ("uq_", "constraint", "psycopg", "sqlalchemy", "traceback"):
        assert leak not in blocked.text.lower()
    assert len(_brand_rows(key)) == 1


# =====================================================================
# §46 — approval currentness through HTTP
# =====================================================================

def _seed_swot(client, headers, domain_key: str, marker: str = "one") -> dict:
    """Record a successful SWOT the way the analysis job does, then read it back."""
    from api.persistence import get_persistence

    get_persistence().record_enrichment(
        domain_key=domain_key, user_id=_me(client, headers)["id"], kind="swot",
        patch={"has_swot": True, "swot": {"marker": marker}})
    return client.get(f"/api/brands/{domain_key}", headers=headers).json()


def _me(client, headers) -> dict:
    return client.get("/api/auth/me", headers=headers).json()


def test_a_newer_swot_closes_the_strategy_gate_until_it_is_approved_too(
        client, monkeypatch, no_legacy_writes):
    headers, _ = _account(client)
    url = f"https://gate{uuid.uuid4().hex[:8]}.test/"
    assert _wait(client, _scrape(client, monkeypatch, url, headers).json()["job_id"],
                 headers)["status"] == "done"
    key = _domain_key(url)

    brand = _seed_swot(client, headers, key, "first")
    assert brand["has_swot"] is True
    assert brand["swot_approved"] is False

    # Strategy is refused before approval.
    assert client.post("/api/strategy/generate", json={"domain_key": key},
                       headers=headers).status_code == 409

    approved = client.post("/api/swot/approve",
                           json={"domain_key": key, "swot_run_id": brand["swot_run_id"]},
                           headers=headers)
    assert approved.status_code == 200, approved.text
    assert client.get(f"/api/brands/{key}", headers=headers).json()["swot_approved"] is True

    # A NEWER successful SWOT becomes current. The old approval does not carry over.
    newer = _seed_swot(client, headers, key, "second")
    assert newer["swot_run_id"] != brand["swot_run_id"]
    assert newer["swot_approved"] is False, "an approval survived the SWOT it was granted for"
    assert client.post("/api/strategy/generate", json={"domain_key": key},
                       headers=headers).status_code == 409

    # Approving the OLD run id now is stale and refused.
    stale = client.post("/api/swot/approve",
                        json={"domain_key": key, "swot_run_id": brand["swot_run_id"]},
                        headers=headers)
    assert stale.status_code == 409, stale.text
    assert client.get(f"/api/brands/{key}", headers=headers).json()["swot_approved"] is False

    # Approving the current one reopens the gate.
    assert client.post("/api/swot/approve",
                       json={"domain_key": key, "swot_run_id": newer["swot_run_id"]},
                       headers=headers).status_code == 200
    assert client.get(f"/api/brands/{key}", headers=headers).json()["swot_approved"] is True


def test_a_failed_swot_does_not_displace_the_current_successful_one(client, monkeypatch,
                                                                    no_legacy_writes):
    """§15 — currentness follows successful runs only, so a failure changes nothing."""
    from db.services import LiveRunData

    from api.persistence import get_persistence

    headers, user_id = _account(client)
    url = f"https://keep{uuid.uuid4().hex[:8]}.test/"
    assert _wait(client, _scrape(client, monkeypatch, url, headers).json()["job_id"],
                 headers)["status"] == "done"
    key = _domain_key(url)

    good = _seed_swot(client, headers, key, "good")
    provider = get_persistence()
    row = provider._brand_row(domain_key=key, user_id=user_id)          # noqa: SLF001 — test probe
    provider._service.create_live_run(LiveRunData(                       # noqa: SLF001
        brand_id=row.id, user_id=provider._uuid(user_id), kind="swot",   # noqa: SLF001
        status="failed", error="generation failed"))

    after = client.get(f"/api/brands/{key}", headers=headers).json()
    assert after["swot_run_id"] == good["swot_run_id"], "a FAILED swot became current"
    assert after["swot"]["marker"] == "good"


# =====================================================================
# §47 — the calendar's duplicate date
# =====================================================================

def test_two_posts_on_one_date_are_ambiguous_and_the_post_id_is_not(client, monkeypatch,
                                                                    no_legacy_writes):
    from api.persistence import (
        AmbiguousCalendarDate, CalendarPostMissing, get_persistence)

    headers, user_id = _account(client)
    url = f"https://cal{uuid.uuid4().hex[:8]}.test/"
    assert _wait(client, _scrape(client, monkeypatch, url, headers).json()["job_id"],
                 headers)["status"] == "done"
    key = _domain_key(url)

    provider = get_persistence()
    provider.record_enrichment(domain_key=key, user_id=user_id, kind="calendar", patch={
        "content_calendar": {"calendar": [
            {"post_id": "post-a", "date": "2026-09-01", "topic": "Morning"},
            {"post_id": "post-b", "date": "2026-09-01", "topic": "Evening"},
            {"post_id": "post-c", "date": "2026-09-02", "topic": "Alone"},
        ]}})

    with pytest.raises(AmbiguousCalendarDate):
        provider.resolve_calendar_post(domain_key=key, user_id=user_id, date="2026-09-01")

    assert provider.resolve_calendar_post(
        domain_key=key, user_id=user_id, post_id="post-b")["topic"] == "Evening"
    assert provider.resolve_calendar_post(
        domain_key=key, user_id=user_id, date="2026-09-02")["topic"] == "Alone"
    with pytest.raises(CalendarPostMissing):
        provider.resolve_calendar_post(domain_key=key, user_id=user_id, date="2026-12-25")


def test_the_route_answers_409_for_an_ambiguous_date_and_never_picks_one(
        client, monkeypatch, no_legacy_writes):
    """The HTTP-level statement of §22: no first-match, and no image is rendered for a guess."""
    from api.persistence import get_persistence
    from api.routes import generate as generate_route

    headers, user_id = _account(client)
    url = f"https://calhttp{uuid.uuid4().hex[:8]}.test/"
    assert _wait(client, _scrape(client, monkeypatch, url, headers).json()["job_id"],
                 headers)["status"] == "done"
    key = _domain_key(url)

    get_persistence().record_enrichment(
        domain_key=key, user_id=user_id, kind="calendar", patch={
            "content_calendar": {"calendar": [
                {"post_id": "am", "date": "2026-09-01", "topic": "Morning"},
                {"post_id": "pm", "date": "2026-09-01", "topic": "Evening"},
            ]}})

    rendered: list[str] = []

    def _never(*_a, **_k):
        rendered.append("generate_image")
        raise AssertionError("an image was rendered for an ambiguous date")

    monkeypatch.setattr(generate_route.images, "generate_image", _never, raising=True)

    response = client.post("/api/calendar/generate-image",
                           json={"domain_key": key, "date": "2026-09-01"}, headers=headers)
    assert response.status_code == 409, response.text
    assert rendered == []
    for leak in ("uq_", "constraint", "psycopg", "traceback"):
        assert leak not in response.text.lower()


# =====================================================================
# §48 — creative history survives a reset
# =====================================================================

def test_starting_a_new_influencer_set_supersedes_the_old_one_without_destroying_it(
        client, monkeypatch, no_legacy_writes):
    from sqlalchemy import select

    from api.persistence import get_persistence
    from db.models import Run
    from db.session import get_session_factory

    headers, user_id = _account(client)
    url = f"https://inf{uuid.uuid4().hex[:8]}.test/"
    assert _wait(client, _scrape(client, monkeypatch, url, headers).json()["job_id"],
                 headers)["status"] == "done"
    key = _domain_key(url)

    provider = get_persistence()
    provider.record_enrichment(domain_key=key, user_id=user_id, kind="influencer_poster",
                               patch={"ai_influencer": {"candidates": {"a": {"id": "a"}},
                                                        "selected_id": "a"}})
    assert client.get(f"/api/brands/{key}", headers=headers).json()[
        "ai_influencer"]["selected_id"] == "a"

    # "Start over" — a NEW set, not a destroyed one.
    provider.replace_section(domain_key=key, user_id=user_id, kind="influencer_poster",
                             section="ai_influencer",
                             value={"candidates": {}, "selected_id": ""})
    current = client.get(f"/api/brands/{key}", headers=headers).json()["ai_influencer"]
    assert current["candidates"] == {} and current["selected_id"] == ""

    row = provider._brand_row(domain_key=key, user_id=user_id)           # noqa: SLF001 — probe
    with get_session_factory()() as session:
        runs = list(session.scalars(
            select(Run).where(Run.brand_id == row.id, Run.kind == "influencer_poster")).all())
    assert len(runs) == 2, "the earlier candidate set was destroyed rather than superseded"
    assert any((r.output or {}).get("ai_influencer", {}).get("selected_id") == "a" for r in runs), \
        "the rejected set is no longer recoverable"


# =====================================================================
# §49 — archive keeps history
# =====================================================================

def test_archiving_a_brand_hides_it_without_deleting_its_history(client, monkeypatch,
                                                                 no_legacy_writes):
    from sqlalchemy import select

    from db.models import ProfileSnapshot, Run
    from db.session import get_session_factory

    headers, user_id = _account(client)
    url = f"https://arch{uuid.uuid4().hex[:8]}.test/"
    assert _wait(client, _scrape(client, monkeypatch, url, headers).json()["job_id"],
                 headers)["status"] == "done"
    key = _domain_key(url)
    _seed_swot(client, headers, key, "before-archive")

    brand_id = _brand_rows(key)[0].id
    assert client.delete(f"/api/brands/{key}", headers=headers).status_code == 200

    assert client.get(f"/api/brands/{key}", headers=headers).status_code == 404
    assert key not in [b.get("domain_key") for b in
                       client.get("/api/brands", headers=headers).json()["brands"]]

    with get_session_factory()() as session:
        profiles = list(session.scalars(
            select(ProfileSnapshot).where(ProfileSnapshot.brand_id == brand_id)).all())
        runs = list(session.scalars(select(Run).where(Run.brand_id == brand_id)).all())
    assert profiles, "archiving destroyed the profile history"
    assert runs, "archiving destroyed the run history"
    assert _brand_rows(key), "archiving hard-deleted the brand row"


# =====================================================================
# §55 — the whole workflow, end to end, on PostgreSQL
# =====================================================================

def test_the_full_workflow_runs_against_postgres(client, monkeypatch, no_legacy_writes):
    """signup -> scrape -> SWOT -> approve -> strategy -> calendar -> creative for ONE post.

    The point is the JOINS between stages, which unit tests cannot reach: that the strategy gate
    reads the approval bound to the SWOT the scrape's brand produced, that the calendar attaches to
    that same brand, and that a creative targets one identified post of that calendar.
    """
    from api.persistence import get_persistence
    from api.routes import generate as generate_route

    headers, user_id = _account(client)
    url = f"https://flow{uuid.uuid4().hex[:8]}.test/"

    # 1. scrape
    started = _scrape(client, monkeypatch, url, headers)
    assert _wait(client, started.json()["job_id"], headers)["status"] == "done"
    key = _domain_key(url)
    brand = client.get(f"/api/brands/{key}", headers=headers).json()
    assert brand["domain_key"] == key and brand["_profile"]["name"]["value"] == "Synthetic"
    assert brand["owner_id"] == user_id and "unclaimed" not in brand

    # 2. SWOT, 3. approve, 4. strategy gate opens
    provider = get_persistence()
    swot = _seed_swot(client, headers, key, "flow")
    assert client.post("/api/strategy/generate", json={"domain_key": key},
                       headers=headers).status_code == 409
    assert client.post("/api/swot/approve",
                       json={"domain_key": key, "swot_run_id": swot["swot_run_id"]},
                       headers=headers).status_code == 200
    provider.record_enrichment(domain_key=key, user_id=user_id, kind="strategy",
                               patch={"has_strategy": True,
                                      "marketing_strategy": {"positioning": "flow"}})

    # 5. calendar
    provider.record_enrichment(domain_key=key, user_id=user_id, kind="calendar", patch={
        "has_plan": True,
        "content_calendar": {"calendar": [
            {"post_id": "flow-1", "date": "2026-10-05", "topic": "Launch", "cta": "Order"},
            {"post_id": "flow-2", "date": "2026-10-06", "topic": "Follow-up"},
        ]}})

    assembled = client.get(f"/api/brands/{key}", headers=headers).json()
    assert assembled["swot_approved"] is True
    assert assembled["has_strategy"] is True
    assert assembled["marketing_strategy"]["positioning"] == "flow"
    assert len(assembled["content_calendar"]["calendar"]) == 2
    assert assembled["_profile"]["name"]["value"] == "Synthetic", (
        "the profile was lost when later stages were folded on top of it")

    # 6. a creative for ONE identified post
    monkeypatch.setattr(generate_route.images, "generate_image",
                        lambda *_a, **_k: {
                            "image_b64": _PNG_B64, "filename": "flow.png", "is_preview": False,
                            "trust_violation": False, "degraded_reason": "",
                            "trust_note_ar": "", "qa_status": "pass", "qa_failed": False,
                            "qa_note": "",
                        }, raising=True)
    monkeypatch.setattr(generate_route.images, "qa_disclosure_from_output",
                        lambda out: {"qa_status": "pass", "qa_failed": False, "qa_note": ""},
                        raising=True)
    made = client.post("/api/calendar/generate-image",
                       json={"domain_key": key, "post_id": "flow-1"}, headers=headers)
    assert made.status_code == 200, made.text

    final = client.get(f"/api/brands/{key}", headers=headers).json()
    rows = {r["post_id"]: r for r in final["content_calendar"]["calendar"]}
    assert "poster" in rows["flow-1"], "the creative did not attach to the post it targeted"
    assert "poster" not in rows["flow-2"], "the creative attached to a post nobody asked for"
    assert final["swot_approved"] is True, "approval was lost across later writes"


#: A 1x1 PNG — enough for `base64.b64decode` and a file write, nothing rendered.
_PNG_B64 = ("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQ"
            "AAAABJRU5ErkJggg==")


# =====================================================================
# §41 — no legacy business reads
# =====================================================================

def test_brand_reads_do_not_fall_back_to_the_legacy_documents(client, monkeypatch):
    """A brand present ONLY in the JSON store must be invisible when postgres is authoritative.

    Reading it would be the read-side twin of a silent fallback: the API would answer from a
    document PostgreSQL knows nothing about, and no later reconciliation could tell which store the
    answer came from.
    """
    from api.brand_store import BrandStore

    headers, user_id = _account(client)
    key = f"legacyonly{uuid.uuid4().hex[:8]}_test"
    BrandStore().save({"domain_key": key, "name": "Legacy Only"}, owner_id=user_id)

    assert client.get(f"/api/brands/{key}", headers=headers).status_code == 404
    assert key not in [b.get("domain_key") for b in
                       client.get("/api/brands", headers=headers).json()["brands"]]
