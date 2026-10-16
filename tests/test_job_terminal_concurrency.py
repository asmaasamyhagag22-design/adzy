# -*- coding: utf-8 -*-
"""Terminal concurrency and claim races, against real PostgreSQL with real threads.

§12 and §13. Not "does the happy path work" — that is proved elsewhere — but what happens when two
finalizers reach one job at the same moment, and when two accounts reach one brand at the same
moment. The properties are the same in both cases and are the reason the transaction, rather than a
preflight, is the authority:

    exactly one winner · exactly one terminal event · the job state matches the winner ·
    the RESULT matches the job state · no reopen · no driver, constraint or psycopg name
    escaping to a caller

The last one matters more than it looks. A uniqueness violation surfacing as a 500 would tell the
loser "the server broke" when what actually happened is "someone else got there first" — and that
is the difference between a bug report and a correct answer.

Every race here uses OS threads against a real database. A race simulated by calling one function
after another proves the code path, not the concurrency.
"""
from __future__ import annotations

import os
import subprocess
import sys
import threading
import types
import uuid
from concurrent.futures import ThreadPoolExecutor
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

from api.persistence import ENV_BACKEND, POSTGRES, reset_persistence  # noqa: E402

_ROOT = Path(__file__).resolve().parents[1]
_DSN = os.environ.get("BASEERA_TEST_DATABASE_URL", "")

pytestmark = pytest.mark.skipif(
    not _DSN, reason="BASEERA_TEST_DATABASE_URL is required for the concurrency proof")

#: Names that must never reach a caller. A leaked constraint or driver name is not just ugly — it
#: tells the loser of a race that the server malfunctioned.
_LEAKS = ("psycopg", "IntegrityError", "UniqueViolation", "duplicate key", "constraint",
          "sqlalchemy", "ProgrammingError")


def _migrate() -> None:
    inherited = os.environ.get("PYTHONPATH", "")
    pythonpath = os.pathsep.join(p for p in (inherited, str(_ROOT)) if p)
    proc = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=str(_ROOT), capture_output=True, text=True, timeout=300,
        env={**os.environ, "PYTHONPATH": pythonpath, "BASEERA_DATABASE_URL": _DSN})
    assert proc.returncode == 0, f"could not provision the database:\n{proc.stderr[-2000:]}"


@pytest.fixture()
def provider(monkeypatch):
    pytest.importorskip("sqlalchemy", reason="SQLAlchemy is not installed")
    _migrate()
    monkeypatch.setenv(ENV_BACKEND, POSTGRES)
    monkeypatch.setenv("BASEERA_DATABASE_URL", _DSN)
    from db.session import dispose
    dispose()
    reset_persistence()
    from api.persistence import get_persistence
    yield get_persistence()
    dispose()
    reset_persistence()


@pytest.fixture()
def user(provider):
    from api.user_auth import hash_password

    return provider._service.create_user(
        email=f"tc{uuid.uuid4().hex[:8]}@test.local",
        hashed_password=hash_password("password123"), full_name="TC")


def _events(job_id: str) -> list:
    from uuid import UUID

    from sqlalchemy import select

    from db.models import JobEvent
    from db.session import get_session_factory

    with get_session_factory()() as session:
        return list(session.scalars(
            select(JobEvent).where(JobEvent.job_id == UUID(job_id)).order_by(JobEvent.seq)).all())


def _row(job_id: str):
    from uuid import UUID

    from db.models import Job
    from db.session import get_session_factory

    with get_session_factory()() as session:
        return session.get(Job, UUID(job_id))


def _direct_result(job_id: str):
    from uuid import UUID

    from db.models import JobResult
    from db.session import get_session_factory

    with get_session_factory()() as session:
        return session.get(JobResult, UUID(job_id))


def _race(*calls):
    """Run every call on its own OS thread, released together, and collect (value | exception)."""
    gate = threading.Barrier(len(calls))

    def go(fn):
        def wrapped():
            gate.wait(timeout=20)
            try:
                return ("ok", fn())
            except BaseException as exc:  # noqa: BLE001 — the exception IS the observation
                return ("raised", exc)
        return wrapped

    with ThreadPoolExecutor(max_workers=len(calls)) as pool:
        return [f.result(timeout=60) for f in [pool.submit(go(c)) for c in calls]]


def _agrees_with_the_outcome(outcomes, wanted, final_status) -> None:
    """Every caller learns whether the job ended the way IT asked — never whether it personally
    performed the write.

    Deliberate, and it is the contract the routes need: `/api/run` retries a finalize and must not
    fail a job that already succeeded, so "already terminal as a success" answers True to a caller
    asking for success. What must NEVER happen is a caller being told True when the job ended the
    other way, so agreement is asserted in both directions.
    """
    for want, (kind, value) in zip(wanted, outcomes):
        assert kind == "ok", f"a race participant raised instead of answering: {value!r}"
        assert value is (want == final_status), (
            f"a caller asking for {want} was told {value} on a job that ended {final_status}")


def _assert_no_leak(outcomes) -> None:
    for kind, value in outcomes:
        if kind != "raised":
            continue
        text = f"{type(value).__name__}: {value}"
        for needle in _LEAKS:
            assert needle.lower() not in text.lower(), (
                f"a storage-layer name escaped to the caller: {text}")


# =====================================================================
# §12 — terminal concurrency
# =====================================================================

def test_success_racing_failure_produces_exactly_one_terminal_event(provider, user):
    """FINAL/DONE vs ERROR on one job. One of them decides the outcome; both must agree afterwards."""
    job_id = provider.create_user_job(owner_user_id=str(user.id), domain_key="race_done_error")
    profile = {"name": {"value": "Raced"}}

    outcomes = _race(
        lambda: provider.finish_direct_result_job(job_id=job_id, profile=profile,
                                                  payload={"who": "success"}),
        lambda: provider.finish_job_failure(job_id=job_id, error="the other one"),
    )
    _assert_no_leak(outcomes)

    terminal = [e for e in _events(job_id) if e.is_terminal]
    assert len(terminal) == 1, [(e.stage, e.status, e.seq) for e in terminal]

    row = _row(job_id)
    assert row.status in ("succeeded", "failed")
    won_success = terminal[0].status == "done"
    assert row.status == ("succeeded" if won_success else "failed")

    # THE RESULT MUST MATCH THE STATE. A failed job holding a successful result is the exact
    # false success this lifecycle exists to make unreachable.
    stored = _direct_result(job_id)
    if won_success:
        assert stored is not None and stored.payload == profile
    else:
        assert stored is None, "a job that failed still stored a successful result"

    _agrees_with_the_outcome(outcomes, ["succeeded", "failed"], row.status)


def test_two_successes_racing_leave_one_result_and_one_terminal_event(provider, user):
    """FINAL/DONE vs FINAL/DONE. `job_results.job_id` is the PRIMARY KEY, so a second answer to one
    question is unrepresentable rather than merely discouraged — and the loser must learn that as a
    plain False, not as a primary-key violation."""
    job_id = provider.create_user_job(owner_user_id=str(user.id), domain_key="race_done_done")
    first = {"name": {"value": "first"}}
    second = {"name": {"value": "second"}}

    outcomes = _race(
        lambda: provider.finish_direct_result_job(job_id=job_id, profile=first),
        lambda: provider.finish_direct_result_job(job_id=job_id, profile=second),
    )
    _assert_no_leak(outcomes)

    terminal = [e for e in _events(job_id) if e.is_terminal]
    assert len(terminal) == 1
    assert _row(job_id).status == "succeeded"
    stored = _direct_result(job_id)
    assert stored is not None and stored.payload in (first, second)

    # Both asked for success and the job succeeded, so both are told so — and the PRIMARY KEY,
    # not a policy either of them remembered, is why only one payload exists.
    _agrees_with_the_outcome(outcomes, ["succeeded", "succeeded"], _row(job_id).status)


def test_a_terminal_job_never_reopens(provider, user):
    """Terminal is immutable — including against a delayed callback arriving much later."""
    job_id = provider.create_user_job(owner_user_id=str(user.id), domain_key="no_reopen")
    assert provider.finish_job_failure(job_id=job_id, error="decided") is True

    provider.record_job_event(job_id=job_id, stage="rules", status="started",
                              occurred_at=_now())
    assert provider.finish_direct_result_job(job_id=job_id, profile={"late": True}) is False

    row = _row(job_id)
    assert row.status == "failed" and row.error == "decided"
    assert _direct_result(job_id) is None
    assert len([e for e in _events(job_id) if e.is_terminal]) == 1


def _now():
    from db.repositories import utcnow
    return utcnow()


def test_many_concurrent_finalizers_still_produce_one_outcome(provider, user):
    """Eight threads, mixed success and failure, one job. The invariants are not a two-thread
    artifact: the row lock serialises every appender for THAT job and nobody else."""
    job_id = provider.create_user_job(owner_user_id=str(user.id), domain_key="race_many")
    calls = []
    for i in range(8):
        if i % 2:
            calls.append(lambda i=i: provider.finish_job_failure(job_id=job_id, error=f"f{i}"))
        else:
            calls.append(lambda i=i: provider.finish_direct_result_job(
                job_id=job_id, profile={"n": i}))

    outcomes = _race(*calls)
    _assert_no_leak(outcomes)

    assert len([e for e in _events(job_id) if e.is_terminal]) == 1
    row = _row(job_id)
    assert row.status in ("succeeded", "failed")
    if row.status == "failed":
        assert _direct_result(job_id) is None
    else:
        assert _direct_result(job_id) is not None
    _agrees_with_the_outcome(
        outcomes, ["failed" if i % 2 else "succeeded" for i in range(8)], row.status)


# =====================================================================
# §13 — claim races
# =====================================================================

def _accounts(provider, n: int = 2):
    from api.user_auth import hash_password

    return [provider._service.create_user(
        email=f"cr{uuid.uuid4().hex[:8]}@test.local",
        hashed_password=hash_password("password123"), full_name="CR") for _ in range(n)]


def _commit_scrape(provider, *, job_id: str, domain_key: str, user_id: str, url: str):
    return provider.commit_scrape(
        domain_key=domain_key, user_id=user_id, ticket=job_id,
        brand={"domain_key": domain_key, "name": "Raced"},
        profile={"name": {"value": "Raced"}}, source_url=url,
        display_name="Raced", warning="")


def _brands(domain_key: str) -> list:
    from sqlalchemy import select

    from db.models import Brand
    from db.session import get_session_factory

    with get_session_factory()() as session:
        return list(session.scalars(select(Brand).where(Brand.domain_key == domain_key)).all())


def _runs_for(brand_id) -> list:
    from sqlalchemy import select

    from db.models import Run
    from db.session import get_session_factory

    with get_session_factory()() as session:
        return list(session.scalars(select(Run).where(Run.brand_id == brand_id)).all())


def test_the_first_ever_brand_race_has_one_winner_and_a_truthful_loser(provider):
    """Two accounts, one brand-new domain, at the same instant.

    The ownership preflight is a COST gate and both callers pass it, which is exactly why the
    commit transaction has to be the authority. What is under test is that losing is an ANSWER: no
    partial brand, no losing Run, and a job that ends rather than sitting RUNNING forever.
    """
    first, second = _accounts(provider)
    domain = f"race{uuid.uuid4().hex[:8]}_test"
    url = f"https://{domain.replace('_', '')}.test/"

    jobs = {str(u.id): provider.begin_scrape(domain_key=domain, user_id=str(u.id))
            for u in (first, second)}

    outcomes = _race(*[
        (lambda uid=str(u.id): _commit_scrape(
            provider, job_id=jobs[uid], domain_key=domain, user_id=uid, url=url))
        for u in (first, second)])
    _assert_no_leak(outcomes)

    answers = [value for kind, value in outcomes if kind == "ok"]
    assert answers.count(None) == 1 and len(answers) == 2, (
        f"expected exactly one winner and one clean refusal, got {answers}")

    brands = _brands(domain)
    assert len(brands) == 1, f"a race created {len(brands)} brands for one domain"
    assert brands[0].owner_id is not None

    # Exactly one scrape Run — the loser persisted no business result at all.
    assert len(_runs_for(brands[0].id)) == 1

    # Now the loser's job terminalizes, the way the route does it. It must END, and it must not
    # end as a success.
    winner_id = next(uid for uid, jid in jobs.items()
                     if _row(jid).status == "succeeded")
    for uid, job_id in jobs.items():
        if uid == winner_id:
            continue
        assert provider.finish_job_failure(
            job_id=job_id, error="This website is already managed by another account.") is True
        row = _row(job_id)
        assert row.status == "failed", "the race loser was left RUNNING"
        assert row.brand_id is None and row.run_id is None
        assert len([e for e in _events(job_id) if e.is_terminal]) == 1


def test_an_unclaimed_brand_can_only_be_claimed_once(provider):
    """The other claim race: the brand EXISTS with no owner (a legacy import), and two accounts
    scrape it at once. Claiming is not creating, so it takes its own proof."""
    from sqlalchemy import update

    from db.models import Brand
    from db.session import get_session_factory

    first, second = _accounts(provider)
    domain = f"uncl{uuid.uuid4().hex[:8]}_test"
    url = f"https://{domain.replace('_', '')}.test/"

    seed_job = provider.begin_scrape(domain_key=domain, user_id=str(first.id))
    assert _commit_scrape(provider, job_id=seed_job, domain_key=domain,
                          user_id=str(first.id), url=url) is not None
    brand_id = _brands(domain)[0].id
    with get_session_factory()() as session:          # make it unclaimed, as an import would
        session.execute(update(Brand).where(Brand.id == brand_id).values(owner_id=None))
        session.commit()
    assert _brands(domain)[0].owner_id is None

    jobs = {str(u.id): provider.begin_scrape(domain_key=domain, user_id=str(u.id))
            for u in (first, second)}
    outcomes = _race(*[
        (lambda uid=str(u.id): _commit_scrape(
            provider, job_id=jobs[uid], domain_key=domain, user_id=uid, url=url))
        for u in (first, second)])
    _assert_no_leak(outcomes)

    answers = [value for kind, value in outcomes if kind == "ok"]
    assert answers.count(None) == 1, f"an unclaimed brand was claimed twice: {answers}"
    assert len(_brands(domain)) == 1
    owner = _brands(domain)[0].owner_id
    assert owner is not None

    winners = [jid for jid in jobs.values() if _row(jid).status == "succeeded"]
    assert len(winners) == 1
    for job_id in jobs.values():
        if job_id in winners:
            continue
        assert provider.finish_job_failure(job_id=job_id, error="claim lost") is True
        assert _row(job_id).status == "failed"
        assert _row(job_id).run_id is None
