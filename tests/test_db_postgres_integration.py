# -*- coding: utf-8 -*-
"""The schema, proven against a REAL PostgreSQL — or skipped, never faked.

`test_db_foundation.py` proves the constraints EXIST. `test_db_migration_parity.py` proves the
migration is frozen and agrees with the models. Neither proves the SERVER refuses the rows those
constraints forbid, and neither can say anything at all about row locking. That is what this file
is for, and it is the only file allowed to make those claims.

It runs only when `BASEERA_TEST_DATABASE_URL` names an obviously disposable PostgreSQL:

  * no default DSN, so it cannot wander onto whatever is listening on 5432
  * its own variable, so a test run can never inherit the production DSN
  * a name guard, so a database that does not announce itself as disposable is left alone
  * SQLite is refused by `db.config` — it would pass a suite full of guarantees it does not provide

Every assertion checks the CONSTRAINT NAME PostgreSQL reports, not merely that something failed. A
test that passes because of an unrelated NOT NULL is a test that proves nothing.
"""
from __future__ import annotations

import concurrent.futures
import os
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]

pytestmark = pytest.mark.skipif(
    not os.environ.get("BASEERA_TEST_DATABASE_URL"),
    reason="BASEERA_TEST_DATABASE_URL is not set — no disposable PostgreSQL to prove against",
)

sqlalchemy = pytest.importorskip("sqlalchemy", reason="SQLAlchemy is not installed")


# =====================================================================
# safety: refuse anything that is not unmistakably disposable
# =====================================================================

def _guarded_url() -> str:
    from db.config import database_url
    from db.test_database_safety import require_disposable_test_database

    url = database_url(test=True)
    require_disposable_test_database(url)
    return url


def test_the_target_database_announces_itself_as_disposable():
    from db.config import safe_dsn

    url = _guarded_url()
    masked = safe_dsn(url)
    assert "***" in masked or "@" not in url, "credentials must be masked before display"
    print(f"\n[db] proving against {masked}")


# =====================================================================
# 7 — the MIGRATION is under test, not create_all
# =====================================================================

def _alembic(*args: str) -> subprocess.CompletedProcess:
    """Run the real Alembic CLI in a subprocess.

    PYTHONPATH is EXTENDED, never replaced: the DB dependencies may live on a path this process
    inherited (an isolated install directory), and overwriting the variable would leave the child
    unable to import SQLAlchemy — which fails the migration for a reason that has nothing to do
    with the migration.
    """
    inherited = os.environ.get("PYTHONPATH", "")
    pythonpath = os.pathsep.join(p for p in (inherited, str(_ROOT)) if p)
    env = {**os.environ, "PYTHONPATH": pythonpath,
           "BASEERA_DATABASE_URL": os.environ["BASEERA_TEST_DATABASE_URL"]}
    return subprocess.run([sys.executable, "-m", "alembic", *args],
                          cwd=str(_ROOT), capture_output=True, text=True, timeout=300, env=env)


def _tables_in_db(engine) -> set[str]:
    from sqlalchemy import inspect

    return set(inspect(engine).get_table_names())


def _expected_head() -> str:
    """The newest revision this build ships, derived from the migration files.

    Was a hardcoded `"0002_legacy_compat"` in three places. That is a assertion about "the chain
    reached its end", written as an assertion about one revision's NAME, so every new migration
    broke it for a reason unrelated to what it was testing.
    """
    from api.persistence import EXPECTED_SCHEMA_HEAD
    return EXPECTED_SCHEMA_HEAD


def _revision_in_db(engine) -> str | None:
    from sqlalchemy import inspect, text as sql

    if "alembic_version" not in inspect(engine).get_table_names():
        return None
    with engine.connect() as conn:
        return conn.execute(sql("SELECT version_num FROM alembic_version")).scalar_one_or_none()


@pytest.fixture(scope="module")
def raw_engine():
    from sqlalchemy import create_engine

    eng = create_engine(_guarded_url(), future=True)
    yield eng
    eng.dispose()


def test_full_migration_chain_through_0001_is_repeatable(raw_engine):
    """base -> head -> 0001 -> head -> base -> head, using the real Alembic chain."""
    pytest.importorskip("alembic", reason="alembic is not installed")
    from db.models import Base

    expected = set(Base.metadata.tables)

    # start clean
    Base.metadata.drop_all(raw_engine, checkfirst=True)
    with raw_engine.begin() as c:
        c.exec_driver_sql("DROP TABLE IF EXISTS alembic_version")

    up1 = _alembic("upgrade", "head")
    assert up1.returncode == 0, up1.stderr[-2000:]
    after_up = _tables_in_db(raw_engine)
    assert expected <= after_up, f"migration did not create: {sorted(expected - after_up)}"
    assert _revision_in_db(raw_engine) == _expected_head()

    to_0001 = _alembic("downgrade", "0001_initial_schema")
    assert to_0001.returncode == 0, to_0001.stderr[-2000:]
    assert _revision_in_db(raw_engine) == "0001_initial_schema"
    from sqlalchemy import inspect
    assert "kind_seq" not in {c["name"] for c in inspect(raw_engine).get_columns("runs")}

    back_to_head = _alembic("upgrade", "head")
    assert back_to_head.returncode == 0, back_to_head.stderr[-2000:]
    assert _revision_in_db(raw_engine) == _expected_head()

    down = _alembic("downgrade", "base")
    assert down.returncode == 0, down.stderr[-2000:]
    after_down = _tables_in_db(raw_engine)
    leftover = expected & after_down
    assert not leftover, f"downgrade left objects behind: {sorted(leftover)}"

    up2 = _alembic("upgrade", "head")
    assert up2.returncode == 0, up2.stderr[-2000:]
    assert expected <= _tables_in_db(raw_engine), "the migration is not repeatable"
    assert _revision_in_db(raw_engine) == _expected_head()


@pytest.fixture(scope="module")
def engine(raw_engine):
    """Schema built by the MIGRATION, so every test below runs against what Alembic really makes.

    A silent fallback to `create_all` would be the worst possible outcome here: the suite would go
    green while proving nothing about the migration. If Alembic is present its failure is fatal.
    """
    from db.models import Base

    try:
        import alembic  # noqa: F401
    except ImportError:
        Base.metadata.create_all(raw_engine)
    else:
        proc = _alembic("upgrade", "head")
        assert proc.returncode == 0, (
            f"alembic upgrade head failed while preparing the fixture:\n{proc.stderr[-2000:]}")
    yield raw_engine
    Base.metadata.drop_all(raw_engine, checkfirst=True)
    with raw_engine.begin() as c:
        c.exec_driver_sql("DROP TABLE IF EXISTS alembic_version")


@pytest.fixture()
def session(engine):
    from sqlalchemy.orm import Session

    from db.models import Base

    with Session(engine, future=True) as s:
        yield s
        s.rollback()
    with engine.begin() as conn:
        for table in reversed(Base.metadata.sorted_tables):
            conn.exec_driver_sql(f'TRUNCATE TABLE "{table.name}" CASCADE')


# =====================================================================
# helpers
# =====================================================================

def _rejected_by(session, constraint: str, fn):
    """Require the DATABASE to refuse, and require it to be THIS constraint that refused.

    Without the name check a test can pass because of an unrelated NOT NULL and quietly stop
    proving the thing it was written for.
    """
    from sqlalchemy.exc import DBAPIError, IntegrityError

    with pytest.raises((IntegrityError, DBAPIError)) as excinfo:
        fn()
        session.flush()
    message = str(getattr(excinfo.value, "orig", excinfo.value))
    assert constraint in message, (
        f"rejected, but by the wrong rule. expected {constraint!r} in:\n{message[:600]}")
    session.rollback()


def _user(session, email=None):
    from db.models import User

    u = User(email=email or f"u{uuid.uuid4().hex[:8]}@test.local", hashed_password="x")
    session.add(u)
    session.flush()
    return u


def _brand(session, owner=None, domain_key=None):
    from db.models import Brand

    b = Brand(domain_key=domain_key or f"d{uuid.uuid4().hex[:8]}_com",
              owner_id=(owner.id if owner else None))
    session.add(b)
    session.flush()
    return b


def _snapshot(session, brand, version=1):
    from db.models import ProfileSnapshot

    s = ProfileSnapshot(brand_id=brand.id, version=version, profile={"n": version},
                        schema_version=1, created_at=sqlalchemy.func.now())
    session.add(s)
    session.flush()
    return s


def _next_kind_seq(session, brand, kind: str) -> int:
    """Allocate MAX+1 for (brand, kind) while holding the Brand's row lock.

    Every runtime run must carry one (`ck_runs_runtime_runs_allocate_a_sequence`); only an import
    may leave it NULL. Locking the stable parent row serialises the empty-set case too; locking a
    row in `runs` cannot do that when the first Run of a kind does not exist yet. Phase 2C's writer
    must perform this allocation and the Run INSERT in the same transaction.
    """
    from sqlalchemy import text as sql

    brand_id = getattr(brand, "id", brand)
    session.execute(sql("SELECT id FROM brands WHERE id=:b FOR UPDATE"), {"b": brand_id}).scalar_one()
    return session.execute(
        sql("SELECT COALESCE(MAX(kind_seq),0)+1 FROM runs WHERE brand_id=:b AND kind=:k"),
        {"b": brand_id, "k": kind}).scalar_one()


def _run(session, brand, user, kind="poster", status="queued"):
    from db.models import Run

    r = Run(brand_id=brand.id, kind=kind, status=status, created_by_user_id=user.id,
            kind_seq=_next_kind_seq(session, brand, kind),
            created_at=sqlalchemy.func.now())
    session.add(r)
    session.flush()
    return r


def _legacy_run(session, brand, kind="swot", status="succeeded"):
    from db.models import Run

    r = Run(brand_id=brand.id, kind=kind, status=status, origin="legacy_import",
            lineage_complete=False, kind_seq=None, created_by_user_id=None,
            created_at=None, imported_at=sqlalchemy.func.now())
    session.add(r)
    session.flush()
    return r


CURRENT_SWOT_SQL = """
SELECT id FROM runs
 WHERE brand_id=:b AND kind='swot' AND status='succeeded'
 ORDER BY kind_seq DESC NULLS LAST
 LIMIT 1
"""
CURRENT_SWOT_APPROVED_SQL = """
SELECT EXISTS (
    SELECT 1 FROM approvals a
     WHERE a.decision='approved'
       AND a.subject_run_id=(
           SELECT id FROM runs
            WHERE brand_id=:b AND kind='swot' AND status='succeeded'
            ORDER BY kind_seq DESC NULLS LAST
            LIMIT 1
       )
)
"""


def _current_swot_id(session, brand):
    from sqlalchemy import text as sql

    return session.execute(sql(CURRENT_SWOT_SQL), {"b": brand.id}).scalar_one_or_none()


def _current_swot_is_approved(session, brand) -> bool:
    from sqlalchemy import text as sql

    return bool(session.execute(
        sql(CURRENT_SWOT_APPROVED_SQL), {"b": brand.id}).scalar_one())


def _asset(session, brand):
    from db.models import Asset

    a = Asset(brand_id=brand.id, storage_key=f"k{uuid.uuid4().hex}", kind="poster",
              mime="image/png")
    session.add(a)
    session.flush()
    return a


# =====================================================================
# 8 — NEGATIVE: every forbidden row is refused, by the intended rule
# =====================================================================

def test_1_duplicate_domain_key_is_rejected(session):
    _brand(session, domain_key="same_com")
    _rejected_by(session, "uq_brands_domain_key",
                 lambda: _brand(session, domain_key="same_com"))


def test_2_brand_cannot_point_at_another_brands_profile(session):
    a, b = _brand(session), _brand(session)
    foreign = _snapshot(session, b)

    def bad():
        a.current_profile_id = foreign.id
        session.add(a)

    _rejected_by(session, "fk_brands_current_profile", bad)


def test_3_run_cannot_consume_another_brands_profile(session):
    u = _user(session)
    a, b = _brand(session), _brand(session)
    foreign = _snapshot(session, b)
    r = _run(session, a, u, kind="strategy")

    def bad():
        r.input_profile_id = foreign.id
        session.add(r)

    _rejected_by(session, "fk_runs_input_profile", bad)


def test_4_run_cannot_select_another_runs_attempt(session):
    from db.models import Attempt

    u = _user(session)
    brand = _brand(session)
    r1, r2 = _run(session, brand, u), _run(session, brand, u)
    a2 = Attempt(run_id=r2.id, brand_id=brand.id, ordinal=0, status="succeeded",
                 asset_id=_asset(session, brand).id)
    session.add(a2)
    session.flush()

    def bad():
        r1.selected_attempt_id = a2.id
        session.add(r1)

    _rejected_by(session, "fk_runs_selected_attempt", bad)


def test_5_duplicate_profile_version_is_rejected(session):
    brand = _brand(session)
    _snapshot(session, brand, version=1)
    _rejected_by(session, "uq_profile_snapshots_brand_id_version",
                 lambda: _snapshot(session, brand, version=1))


def test_6_duplicate_execution_no_within_one_run_is_rejected(session):
    from db.models import Job

    u = _user(session)
    brand = _brand(session)
    run = _run(session, brand, u)
    session.add(Job(run_id=run.id, brand_id=brand.id, access_mode="user",
                    owner_user_id=u.id, execution_no=1))
    session.flush()
    _rejected_by(session, "uq_jobs_run_id_execution_no", lambda: session.add(
        Job(run_id=run.id, brand_id=brand.id, access_mode="user", owner_user_id=u.id,
            execution_no=1)))


def test_7a_job_cannot_carry_both_principal_forms(session):
    from db.models import Job

    u = _user(session)
    _rejected_by(session, "ck_jobs_exactly_one_principal", lambda: session.add(
        Job(access_mode="user", owner_user_id=u.id, capability_digest="a" * 64)))


def test_7b_job_cannot_carry_neither_principal_form(session):
    from db.models import Job

    _rejected_by(session, "ck_jobs_exactly_one_principal",
                 lambda: session.add(Job(access_mode="user")))


def test_7c_capability_job_may_not_name_a_user(session):
    from db.models import Job

    u = _user(session)
    _rejected_by(session, "ck_jobs_exactly_one_principal", lambda: session.add(
        Job(access_mode="capability", capability_digest="b" * 64, owner_user_id=u.id)))


def test_8_duplicate_calendar_ordinal_is_rejected(session):
    from db.models import CalendarPost

    u = _user(session)
    brand = _brand(session)
    cal = _run(session, brand, u, kind="calendar")
    session.add(CalendarPost(calendar_run_id=cal.id, brand_id=brand.id, ordinal=0))
    session.flush()
    _rejected_by(session, "uq_calendar_posts_calendar_run_id_ordinal", lambda: session.add(
        CalendarPost(calendar_run_id=cal.id, brand_id=brand.id, ordinal=0)))


def test_9a_calendar_post_cannot_hang_off_a_non_calendar_run(session):
    from db.models import CalendarPost

    u = _user(session)
    brand = _brand(session)
    strategy = _run(session, brand, u, kind="strategy")
    _rejected_by(session, "fk_calendar_posts_calendar_run", lambda: session.add(
        CalendarPost(calendar_run_id=strategy.id, brand_id=brand.id, ordinal=0)))


def test_9b_calendar_post_cannot_claim_another_brands_calendar_run(session):
    from db.models import CalendarPost

    u = _user(session)
    a, b = _brand(session), _brand(session)
    cal = _run(session, a, u, kind="calendar")
    _rejected_by(session, "fk_calendar_posts_calendar_run", lambda: session.add(
        CalendarPost(calendar_run_id=cal.id, brand_id=b.id, ordinal=0)))


def test_10_approval_cannot_name_a_non_swot_run(session):
    """The row is otherwise VALID — actor and time supplied — so the FK is what rejects it.

    0002 requires a live approval to name both, and without them the CHECK fires first and this
    test would pass for the wrong reason. The name guard in `_rejected_by` caught exactly that.
    """
    from db.models import Approval

    u = _user(session)
    brand = _brand(session)
    strategy = _run(session, brand, u, kind="strategy")
    _rejected_by(session, "fk_approvals_subject_run", lambda: session.add(
        Approval(brand_id=brand.id, subject_run_id=strategy.id, subject_kind="swot",
                 decision="approved", decided_by_user_id=u.id,
                 decided_at=sqlalchemy.func.now(), origin="live")))


def test_a_live_approval_records_actor_and_decision_time(session):
    from db.models import Approval

    u = _user(session)
    brand = _brand(session, owner=u)
    swot = _run(session, brand, u, kind="swot", status="succeeded")
    row = Approval(brand_id=brand.id, subject_run_id=swot.id, subject_kind="swot",
                   decision="approved", decided_by_user_id=u.id,
                   decided_at=sqlalchemy.func.now(), origin="live", imported_at=None)
    session.add(row)
    session.flush()
    assert row.decided_by_user_id == u.id and row.imported_at is None


@pytest.mark.parametrize("missing", ["actor", "time"])
def test_a_live_approval_cannot_omit_actor_or_decision_time(session, missing):
    from db.models import Approval

    u = _user(session)
    brand = _brand(session, owner=u)
    swot = _run(session, brand, u, kind="swot", status="succeeded")
    _rejected_by(session, "ck_approvals_live_approvals_name_actor_and_time", lambda: session.add(
        Approval(brand_id=brand.id, subject_run_id=swot.id, subject_kind="swot",
                 decision="approved", decided_by_user_id=None if missing == "actor" else u.id,
                 decided_at=None if missing == "time" else sqlalchemy.func.now(),
                 origin="live", imported_at=None)))


def test_an_imported_approval_preserves_only_the_decision_and_import_time(session):
    from db.models import Approval

    brand = _brand(session)
    swot = _legacy_run(session, brand)
    row = Approval(brand_id=brand.id, subject_run_id=swot.id, subject_kind="swot",
                   decision="approved", decided_by_user_id=None, decided_at=None,
                   origin="legacy_import", imported_at=sqlalchemy.func.now())
    session.add(row)
    session.flush()
    assert row.decided_by_user_id is None and row.decided_at is None


@pytest.mark.parametrize("fabricated", ["actor", "time", "missing_import_time"])
def test_an_imported_approval_cannot_fabricate_missing_provenance(session, fabricated):
    from db.models import Approval

    u = _user(session)
    brand = _brand(session, owner=u)
    swot = _legacy_run(session, brand)
    _rejected_by(
        session,
        "ck_approvals_legacy_imports_have_no_fabricated_metadata",
        lambda: session.add(Approval(
            brand_id=brand.id, subject_run_id=swot.id, subject_kind="swot",
            decision="approved", decided_by_user_id=u.id if fabricated == "actor" else None,
            decided_at=sqlalchemy.func.now() if fabricated == "time" else None,
            origin="legacy_import",
            imported_at=None if fabricated == "missing_import_time" else sqlalchemy.func.now())),
    )


def test_only_one_imported_approval_may_exist_per_run(session):
    from db.models import Approval

    brand = _brand(session)
    swot = _legacy_run(session, brand)
    session.add(Approval(
        brand_id=brand.id, subject_run_id=swot.id, subject_kind="swot",
        decision="approved", origin="legacy_import", imported_at=sqlalchemy.func.now()))
    session.flush()
    _rejected_by(session, "uq_approvals_one_import_per_run", lambda: session.add(Approval(
        brand_id=brand.id, subject_run_id=swot.id, subject_kind="swot",
        decision="approved", origin="legacy_import", imported_at=sqlalchemy.func.now())))


def test_11_attempt_cannot_reference_another_brands_asset(session):
    from db.models import Attempt

    u = _user(session)
    a, b = _brand(session), _brand(session)
    foreign_asset = _asset(session, b)
    run = _run(session, a, u)
    _rejected_by(session, "fk_attempts_asset", lambda: session.add(
        Attempt(run_id=run.id, brand_id=a.id, ordinal=0, status="succeeded",
                asset_id=foreign_asset.id)))


def test_12_run_cannot_depend_on_itself(session):
    from db.models import RunDependency

    u = _user(session)
    brand = _brand(session)
    run = _run(session, brand, u, kind="strategy")
    _rejected_by(session, "ck_run_dependencies_no_self_dependency", lambda: session.add(
        RunDependency(run_id=run.id, depends_on_run_id=run.id, brand_id=brand.id,
                      relation="uses_swot")))


def test_13_succeeded_attempt_must_have_an_asset(session):
    from db.models import Attempt

    u = _user(session)
    brand = _brand(session)
    run = _run(session, brand, u)
    _rejected_by(session, "ck_attempts_succeeded_attempts_have_an_asset", lambda: session.add(
        Attempt(run_id=run.id, brand_id=brand.id, ordinal=0, status="succeeded")))


@pytest.mark.parametrize("surface", [
    "MiXeD@Test.Local", " lead@test.local", "trail@test.local ", " both@test.local ",
])
def test_14_non_canonical_emails_are_refused(session, surface):
    """All four surfaces the product normalises away. None may become a second account."""
    _rejected_by(session, "ck_users_email_is_normalised",
                 lambda: _user(session, email=surface))


def test_15_the_canonical_email_is_unique(session):
    _user(session, email="dup@test.local")
    _rejected_by(session, "uq_users_email", lambda: _user(session, email="dup@test.local"))


def test_legacy_user_id_is_unique_and_resolves_to_the_uuid5_identity(session):
    from db.legacy_identity import legacy_user_uuid
    from db.models import User

    legacy_id = "551ece7d16157b81"
    mapped = legacy_user_uuid(legacy_id)
    session.add(User(id=mapped, email="legacy@test.local", hashed_password="x",
                     legacy_user_id=legacy_id))
    session.flush()
    assert session.get(User, mapped).legacy_user_id == legacy_id
    _rejected_by(session, "uq_users_legacy_user_id", lambda: session.add(User(
        email="legacy-duplicate@test.local", hashed_password="x", legacy_user_id=legacy_id)))


def test_legacy_user_id_rejects_non_legacy_surfaces(session):
    from db.models import User

    _rejected_by(session, "ck_users_legacy_user_id_is_16_hex", lambda: session.add(User(
        email="bad-legacy@test.local", hashed_password="x", legacy_user_id="551ECE7D16157B81")))


def test_multiple_post_cutover_users_may_have_no_legacy_id(session):
    assert _user(session, email="new-a@test.local").legacy_user_id is None
    assert _user(session, email="new-b@test.local").legacy_user_id is None


# =====================================================================
# 9 — POSITIVE: legitimate product states must all be storable
# =====================================================================

def test_an_unclaimed_brand_is_accepted(session):
    b = _brand(session, owner=None)
    assert b.owner_id is None


def test_a_brand_may_point_at_its_own_profile(session):
    b = _brand(session)
    s = _snapshot(session, b)
    b.current_profile_id = s.id
    session.add(b)
    session.flush()


def test_a_run_may_select_its_own_attempt(session):
    from db.models import Attempt

    u = _user(session)
    brand = _brand(session)
    run = _run(session, brand, u)
    att = Attempt(run_id=run.id, brand_id=brand.id, ordinal=0, status="succeeded",
                  asset_id=_asset(session, brand).id)
    session.add(att)
    session.flush()
    run.selected_attempt_id = att.id
    session.add(run)
    session.flush()


def test_two_calendar_posts_may_share_a_date(session):
    """The whole reason identity is the ordinal and not the date."""
    import datetime as dt

    from db.models import CalendarPost

    u = _user(session)
    brand = _brand(session)
    cal = _run(session, brand, u, kind="calendar")
    day = dt.date(2026, 9, 1)
    session.add_all([
        CalendarPost(calendar_run_id=cal.id, brand_id=brand.id, ordinal=0,
                     scheduled_date=day, channel="instagram"),
        CalendarPost(calendar_run_id=cal.id, brand_id=brand.id, ordinal=1,
                     scheduled_date=day, channel="facebook"),
    ])
    session.flush()


def test_a_capability_job_stores_a_digest_and_no_user(session):
    from db.models import Job

    j = Job(access_mode="capability", capability_digest="c" * 64,
            requested_domain_key=None)
    session.add(j)
    session.flush()
    assert j.owner_user_id is None


def test_a_user_owned_job_may_exist_before_any_brand_or_run(session):
    """The first-ever-scrape lifecycle: durable Job, no Brand yet, no Run yet."""
    from db.models import Job

    u = _user(session)
    j = Job(access_mode="user", owner_user_id=u.id, requested_domain_key="brand_new_com")
    session.add(j)
    session.flush()
    assert j.run_id is None and j.brand_id is None
    assert j.requested_domain_key == "brand_new_com"


def test_a_legacy_import_may_admit_unknown_lineage(session):
    """Imported history must be able to say "I do not know" without lying."""
    from db.models import Run

    brand = _brand(session)
    r = Run(brand_id=brand.id, kind="strategy", status="succeeded",
            origin="legacy_import", lineage_complete=False,
            input_profile_id=None, created_by_user_id=None,
            created_at=None, imported_at=sqlalchemy.func.now(),
            legacy_source_key="alfouadpharmacies_com.owner-81e6512a9145e591.bak.json")
    session.add(r)
    session.flush()
    assert r.created_at is None, "migration time must not masquerade as creation time"
    assert r.kind_seq is None, "legacy order is unknown and must not be fabricated"


def test_a_live_run_may_not_admit_unknown_lineage(session):
    """The same permission must NOT leak to live rows."""
    from db.models import Run

    u = _user(session)
    brand = _brand(session)
    _rejected_by(session, "ck_runs_live_lineage_is_complete", lambda: session.add(
        Run(brand_id=brand.id, kind="strategy", status="succeeded", origin="live",
            lineage_complete=False, kind_seq=_next_kind_seq(session, brand, "strategy"),
            created_by_user_id=u.id, created_at=sqlalchemy.func.now())))


def test_a_live_run_cannot_skip_kind_sequence_allocation(session):
    from db.models import Run

    u = _user(session)
    brand = _brand(session, owner=u)
    _rejected_by(session, "ck_runs_runtime_runs_allocate_a_sequence", lambda: session.add(
        Run(brand_id=brand.id, kind="swot", status="succeeded", origin="live",
            lineage_complete=True, kind_seq=None, created_by_user_id=u.id,
            created_at=sqlalchemy.func.now())))


def test_kind_sequence_scope_and_unique_backstop_are_brand_and_kind(session):
    from db.models import Run

    u = _user(session)
    brand = _brand(session, owner=u)
    other = _brand(session, owner=u)
    for b, kind in ((brand, "swot"), (brand, "strategy"), (other, "swot")):
        session.add(Run(
            brand_id=b.id, kind=kind, status="succeeded", origin="live",
            lineage_complete=True, kind_seq=1, created_by_user_id=u.id,
            created_at=sqlalchemy.func.now()))
    session.flush()
    _rejected_by(session, "uq_runs_brand_id_kind_kind_seq", lambda: session.add(Run(
        brand_id=brand.id, kind="swot", status="succeeded", origin="live",
        lineage_complete=True, kind_seq=1, created_by_user_id=u.id,
        created_at=sqlalchemy.func.now())))


def test_current_swot_and_approval_follow_run_identity_not_nullable_time(session):
    """Imported approval opens only its SWOT; every successful regeneration starts unapproved."""
    from sqlalchemy import text as sql

    from db.models import Approval

    u = _user(session)
    brand = _brand(session, owner=u)
    imported = _legacy_run(session, brand, kind="swot", status="succeeded")
    session.add(Approval(
        brand_id=brand.id, subject_run_id=imported.id, subject_kind="swot",
        decision="approved", origin="legacy_import", imported_at=sqlalchemy.func.now()))
    session.flush()
    assert _current_swot_id(session, brand) == imported.id
    assert _current_swot_is_approved(session, brand) is True

    live1 = _run(session, brand, u, kind="swot", status="succeeded")
    naive = session.execute(sql(
        "SELECT id FROM runs WHERE brand_id=:b AND kind='swot' AND status='succeeded' "
        "ORDER BY created_at DESC LIMIT 1"), {"b": brand.id}).scalar_one()
    assert naive == imported.id, "RED disappeared: PostgreSQL no longer sorts NULL first on DESC"
    assert _current_swot_id(session, brand) == live1.id
    assert _current_swot_is_approved(session, brand) is False

    session.add(Approval(
        brand_id=brand.id, subject_run_id=live1.id, subject_kind="swot",
        decision="approved", decided_by_user_id=u.id,
        decided_at=sqlalchemy.func.now(), origin="live"))
    session.flush()
    assert _current_swot_is_approved(session, brand) is True

    live2 = _run(session, brand, u, kind="swot", status="succeeded")
    assert _current_swot_id(session, brand) == live2.id
    assert _current_swot_is_approved(session, brand) is False

    _run(session, brand, u, kind="swot", status="failed")
    assert _current_swot_id(session, brand) == live2.id


def test_a_profile_snapshot_keeps_the_whole_profile_beside_its_projections(session):
    """C6-R1: the relational columns are a projection, never the only surviving evidence."""
    from db.models import ProfileSnapshot

    brand = _brand(session)
    full = {"name": {"value": "X"}, "locations": [{"country": "United Kingdom"}],
            "languages": [{"code": "en"}]}
    s = ProfileSnapshot(brand_id=brand.id, version=1, profile=full, schema_version=1,
                        country_canonical="the UK", country_source="location_country",
                        country_raw="United Kingdom",
                        primary_language="en", language_source="legacy_measured_content",
                        created_at=sqlalchemy.func.now())
    session.add(s)
    session.flush()
    session.refresh(s)
    assert s.profile == full, "the immutable snapshot lost the full profile"
    assert (s.country_canonical, s.country_source, s.country_raw) == (
        "the UK", "location_country", "United Kingdom")
    assert s.primary_language == "en" and s.language_source == "legacy_measured_content", (
        "country and language must remain separately sourced, never merged into one locale")


# =====================================================================
# 10 — the profile version race, on two real connections
# =====================================================================

def test_the_profile_version_race_serialises_on_the_brand_row(engine):
    """Two independent transactions, the approved algorithm, no sleeps.

    T2's `FOR UPDATE` must BLOCK until T1 commits, then compute its version from the committed
    state. Blocking is proven by the fact that T2's allocation sees T1's row.
    """
    import threading

    from sqlalchemy import text as sql
    from sqlalchemy.orm import Session

    from db.models import Brand

    with Session(engine, future=True) as s:
        brand = Brand(domain_key=f"ver{uuid.uuid4().hex[:8]}_com")
        s.add(brand)
        s.commit()
        bid = brand.id

    order: list[str] = []
    t1_locked = threading.Event()
    t2_started = threading.Event()
    results: dict[str, int] = {}
    errors: list[BaseException] = []

    def allocate(tag: str, wait_for=None, signal=None):
        try:
            with Session(engine, future=True) as s:
                s.begin()
                if wait_for is not None:
                    assert wait_for.wait(20)
                s.execute(sql("SELECT id FROM brands WHERE id = :i FOR UPDATE"), {"i": bid})
                if signal is not None:
                    signal.set()
                    # Give the other thread every chance to reach its own FOR UPDATE and block.
                    assert t2_started.wait(20)
                nxt = s.execute(
                    sql("SELECT COALESCE(MAX(version),0)+1 FROM profile_snapshots "
                        "WHERE brand_id = :i"), {"i": bid}).scalar()
                s.execute(
                    sql("INSERT INTO profile_snapshots "
                        "(id, brand_id, version, profile, schema_version, origin, created_at) "
                        "VALUES (gen_random_uuid(), :i, :v, '{}'::jsonb, 1, 'live', now())"),
                    {"i": bid, "v": nxt})
                s.commit()
                results[tag] = nxt
                order.append(tag)
        except BaseException as exc:  # pragma: no cover - asserted below
            errors.append(exc)

    t1 = threading.Thread(target=allocate, args=("t1",), kwargs={"signal": t1_locked})
    t2 = threading.Thread(target=allocate, args=("t2",), kwargs={"wait_for": t1_locked})
    t1.start()
    t1_locked.wait(20)
    t2.start()
    # t2 is now racing for the same row lock; release t1.
    t2_started.set()
    t1.join(40)
    t2.join(40)

    assert not errors, f"the race raised: {errors}"
    assert set(results.values()) == {1, 2}, (
        f"versions collided or skipped: {results} (order={order})")
    assert order == ["t1", "t2"], f"commit order was not serialised: {order}"


def test_the_unique_constraint_backstops_an_unlocked_allocator(engine):
    """If a future code path forgets the lock, it gets a violation — never a duplicate."""
    from sqlalchemy import text as sql
    from sqlalchemy.exc import IntegrityError
    from sqlalchemy.orm import Session

    from db.models import Brand

    with Session(engine, future=True) as s:
        brand = Brand(domain_key=f"nolock{uuid.uuid4().hex[:8]}_com")
        s.add(brand)
        s.commit()
        bid = brand.id

    insert = sql("INSERT INTO profile_snapshots "
                 "(id, brand_id, version, profile, schema_version, origin, created_at) "
                 "VALUES (gen_random_uuid(), :i, 1, '{}'::jsonb, 1, 'live', now())")
    with Session(engine, future=True) as s:
        s.execute(insert, {"i": bid})
        s.commit()
    with Session(engine, future=True) as s:
        with pytest.raises(IntegrityError) as e:
            s.execute(insert, {"i": bid})
            s.commit()
        assert "uq_profile_snapshots_brand_id_version" in str(e.value.orig)


def test_run_kind_sequence_allocation_serialises_on_the_brand_row(engine):
    """Two Run writers overlap; the second must observe the first committed sequence."""
    from sqlalchemy import select
    from sqlalchemy.orm import Session

    from db.models import Brand, Run, User

    with Session(engine, future=True) as s:
        user = User(email=f"seq{uuid.uuid4().hex[:8]}@test.local", hashed_password="x")
        brand = Brand(domain_key=f"seqrace{uuid.uuid4().hex[:8]}_com", owner_id=None)
        s.add_all([user, brand])
        s.commit()
        uid, bid = user.id, brand.id

    first_allocated = threading.Event()
    second_entered = threading.Event()

    def insert_first() -> int:
        with Session(engine, future=True) as s:
            seq = _next_kind_seq(s, bid, "swot")
            first_allocated.set()
            assert second_entered.wait(20)
            # Keep the transaction open long enough to make the second writer overlap. If the
            # allocator loses its FOR UPDATE, writer two commits seq=1 and this insert fails.
            time.sleep(0.25)
            s.add(Run(
                brand_id=bid, kind="swot", status="succeeded", origin="live",
                lineage_complete=True, kind_seq=seq, created_by_user_id=uid,
                created_at=sqlalchemy.func.now()))
            s.commit()
            return seq

    def insert_second() -> int:
        assert first_allocated.wait(20)
        second_entered.set()
        with Session(engine, future=True) as s:
            seq = _next_kind_seq(s, bid, "swot")
            s.add(Run(
                brand_id=bid, kind="swot", status="succeeded", origin="live",
                lineage_complete=True, kind_seq=seq, created_by_user_id=uid,
                created_at=sqlalchemy.func.now()))
            s.commit()
            return seq

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(insert_first)
        second = pool.submit(insert_second)
        allocated = {first.result(timeout=40), second.result(timeout=40)}

    with Session(engine, future=True) as s:
        stored = list(s.scalars(
            select(Run.kind_seq).where(Run.brand_id == bid, Run.kind == "swot")
            .order_by(Run.kind_seq)))
    assert allocated == {1, 2} and stored == [1, 2]


# =====================================================================
# 11 — the unclaimed claim race
# =====================================================================

def test_exactly_one_claimant_wins_and_the_loser_persists_nothing(engine):
    """Both see an unclaimed brand. One claims it; the other must leave NO trace."""
    from sqlalchemy import text as sql
    from sqlalchemy.orm import Session

    from db.models import Brand, User

    with Session(engine, future=True) as s:
        a = User(email=f"a{uuid.uuid4().hex[:8]}@test.local", hashed_password="x")
        b = User(email=f"b{uuid.uuid4().hex[:8]}@test.local", hashed_password="x")
        brand = Brand(domain_key=f"race{uuid.uuid4().hex[:8]}_com", owner_id=None)
        s.add_all([a, b, brand])
        s.commit()
        bid, a_id, b_id = brand.id, a.id, b.id

    def claim(user_id) -> bool:
        """The approved final transaction, in full. Returns True if this caller became owner."""
        with Session(engine, future=True) as s:
            s.begin()
            owner = s.execute(sql("SELECT owner_id FROM brands WHERE id = :i FOR UPDATE"),
                              {"i": bid}).scalar()
            if owner is not None and str(owner) != str(user_id):
                s.rollback()
                return False
            claimed = s.execute(
                sql("UPDATE brands SET owner_id = :o WHERE id = :i AND owner_id IS NULL"),
                {"o": user_id, "i": bid}).rowcount
            if claimed == 0:
                s.rollback()
                return False
            snap = s.execute(
                sql("INSERT INTO profile_snapshots "
                    "(id, brand_id, version, profile, schema_version, origin, created_at) "
                    "VALUES (gen_random_uuid(), :i, 1, '{}'::jsonb, 1, 'live', now()) "
                    "RETURNING id"), {"i": bid}).scalar()
            s.execute(sql("UPDATE brands SET current_profile_id = :p WHERE id = :i"),
                      {"p": snap, "i": bid})
            s.execute(
                sql("INSERT INTO runs (id, brand_id, kind, status, input_profile_id, "
                    "requested_config, config_schema_version, origin, lineage_complete, "
                    "kind_seq, created_by_user_id, created_at) VALUES (gen_random_uuid(), :i, "
                    "'scrape', 'succeeded', :p, '{}'::jsonb, 1, 'live', true, "
                    "(SELECT COALESCE(MAX(kind_seq),0)+1 FROM runs "
                    "  WHERE brand_id=:i AND kind='scrape'), :u, now())"),
                {"i": bid, "p": snap, "u": user_id})
            s.commit()
            return True

    first = claim(a_id)
    second = claim(b_id)
    assert [first, second] == [True, False], "more than one claimant succeeded"

    with Session(engine, future=True) as s:
        owner = s.execute(sql("SELECT owner_id FROM brands WHERE id = :i"), {"i": bid}).scalar()
        snaps = s.execute(sql("SELECT count(*) FROM profile_snapshots WHERE brand_id = :i"),
                          {"i": bid}).scalar()
        runs = s.execute(sql("SELECT count(*) FROM runs WHERE brand_id = :i"), {"i": bid}).scalar()
        current = s.execute(sql("SELECT current_profile_id FROM brands WHERE id = :i"),
                            {"i": bid}).scalar()
    assert str(owner) == str(a_id), "the wrong account ended up managing the brand"
    assert snaps == 1, f"the loser left a ProfileSnapshot behind ({snaps})"
    assert runs == 1, f"the loser left a Run behind ({runs})"
    assert current is not None, "the winner's current-profile pointer did not commit"


# =====================================================================
# 12 — the first-create race
# =====================================================================

def test_two_first_ever_scrapes_yield_one_canonical_brand(engine):
    """Nothing exists. Both finish their work and reach the final transaction."""
    from sqlalchemy import text as sql
    from sqlalchemy.orm import Session

    from db.models import User

    dk = f"first{uuid.uuid4().hex[:8]}_com"
    with Session(engine, future=True) as s:
        a = User(email=f"fa{uuid.uuid4().hex[:8]}@test.local", hashed_password="x")
        b = User(email=f"fb{uuid.uuid4().hex[:8]}@test.local", hashed_password="x")
        s.add_all([a, b])
        s.commit()
        a_id, b_id = a.id, b.id

    def create_or_claim(user_id) -> bool:
        with Session(engine, future=True) as s:
            s.begin()
            s.execute(sql("INSERT INTO brands (id, domain_key, owner_id) "
                          "VALUES (gen_random_uuid(), :d, :o) ON CONFLICT (domain_key) DO NOTHING"),
                      {"d": dk, "o": user_id})
            row = s.execute(sql("SELECT id, owner_id FROM brands WHERE domain_key = :d "
                                "FOR UPDATE"), {"d": dk}).one()
            bid, owner = row[0], row[1]
            if owner is not None and str(owner) != str(user_id):
                s.rollback()
                return False
            snap = s.execute(
                sql("INSERT INTO profile_snapshots "
                    "(id, brand_id, version, profile, schema_version, origin, created_at) "
                    "VALUES (gen_random_uuid(), :i, "
                    "(SELECT COALESCE(MAX(version),0)+1 FROM profile_snapshots WHERE brand_id=:i),"
                    " '{}'::jsonb, 1, 'live', now()) RETURNING id"), {"i": bid}).scalar()
            s.execute(sql("UPDATE brands SET current_profile_id = :p WHERE id = :i"),
                      {"p": snap, "i": bid})
            s.commit()
            return True

    assert create_or_claim(a_id) is True
    assert create_or_claim(b_id) is False, "the second request attached to another user's brand"

    with Session(engine, future=True) as s:
        count = s.execute(sql("SELECT count(*) FROM brands WHERE domain_key = :d"),
                          {"d": dk}).scalar()
        owner = s.execute(sql("SELECT owner_id FROM brands WHERE domain_key = :d"),
                          {"d": dk}).scalar()
        snaps = s.execute(
            sql("SELECT count(*) FROM profile_snapshots ps JOIN brands b ON b.id = ps.brand_id "
                "WHERE b.domain_key = :d"), {"d": dk}).scalar()
    assert count == 1, "a duplicate Brand identity was created"
    assert str(owner) == str(a_id)
    assert snaps == 1, "the losing request fabricated profile lineage"


# =====================================================================
# 13 — the two crash classes must be REPRESENTABLE and DETECTABLE
# =====================================================================

# The predicates a future reconciler will use. Written here so they are tested against a real
# server now, and so nothing has to be invented later. DB1B transitions nothing automatically.
CLASS_A_SQL = """
SELECT j.id FROM jobs j JOIN runs r ON r.id = j.run_id
 WHERE j.status IN ('queued','running') AND r.status = 'succeeded'
"""
CLASS_B_SQL = """
SELECT j.id FROM jobs j
 WHERE j.status = 'running' AND j.lease_expires_at < now()
   AND (j.run_id IS NULL
        OR EXISTS (SELECT 1 FROM runs r WHERE r.id = j.run_id AND r.status <> 'succeeded'))
"""


def test_class_a_result_committed_but_job_never_finalised_is_detectable(session):
    """The C3 residual: `to_thread` work committed, the cancelled coroutine never finalised."""
    from sqlalchemy import text as sql

    from db.models import Job

    u = _user(session)
    brand = _brand(session)
    run = _run(session, brand, u, kind="swot", status="succeeded")
    job = Job(run_id=run.id, brand_id=brand.id, access_mode="user", owner_user_id=u.id,
              status="running", claimed_by="worker-1",
              lease_expires_at=sqlalchemy.func.now())
    session.add(job)
    session.flush()

    found = [r[0] for r in session.execute(sql(CLASS_A_SQL))]
    assert job.id in found, "a committed result beside a non-terminal job is not detectable"


def test_class_b_abandoned_lease_with_no_result_is_detectable(session):
    """Process died mid-work: the lease is the only signal that survives an unclean kill."""
    import datetime as dt

    from sqlalchemy import text as sql

    from db.models import Job

    u = _user(session)
    brand = _brand(session)
    run = _run(session, brand, u, kind="scrape", status="running")
    stale = dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=30)
    job = Job(run_id=run.id, brand_id=brand.id, access_mode="user", owner_user_id=u.id,
              status="running", claimed_by="dead-worker", lease_expires_at=stale)
    session.add(job)
    session.flush()

    found = [r[0] for r in session.execute(sql(CLASS_B_SQL))]
    assert job.id in found, "an abandoned lease is not detectable"


def test_a_healthy_running_job_matches_neither_predicate(session):
    """The predicates must not sweep up work that is simply in progress."""
    import datetime as dt

    from sqlalchemy import text as sql

    from db.models import Job

    u = _user(session)
    brand = _brand(session)
    run = _run(session, brand, u, kind="scrape", status="running")
    fresh = dt.datetime.now(dt.timezone.utc) + dt.timedelta(minutes=5)
    job = Job(run_id=run.id, brand_id=brand.id, access_mode="user", owner_user_id=u.id,
              status="running", claimed_by="worker-1", lease_expires_at=fresh)
    session.add(job)
    session.flush()

    assert job.id not in [r[0] for r in session.execute(sql(CLASS_A_SQL))]
    assert job.id not in [r[0] for r in session.execute(sql(CLASS_B_SQL))]


# =====================================================================
# 14 — the terminal-state rule is a transaction predicate, proven as one
# =====================================================================

def test_head_schema_matches_the_orm_by_introspection(engine):
    """THE future-proof parity rule: the schema the FULL CHAIN produces at head == the models.

    This replaces the rule that used to live in test_db_migration_parity.py — "revision 0001 ==
    current models" — which stops being true the day 0002 exists. Here the comparison is against
    whatever the whole chain built, read back out of PostgreSQL, so it keeps working after any
    number of revisions.

    Structured introspection, never SQL string comparison: PostgreSQL legitimately renders a CHECK
    differently from how SQLAlchemy emitted it, and a text diff would report that as a failure.
    """
    from sqlalchemy import inspect

    from db.models import Base

    insp = inspect(engine)
    meta = Base.metadata
    problems: list[str] = []

    def type_signature(value) -> str:
        return " ".join(value.compile(dialect=engine.dialect).lower().split())

    def default_signature(value) -> str | None:
        if value is None:
            return None
        value = getattr(value, "arg", value)
        rendered = " ".join(str(value).strip().lower().split())
        # PostgreSQL annotates inferred string literals on reflection; the ORM literal has the
        # same value without the redundant cast.
        return rendered.replace("::character varying", "").replace("::text", "")

    db_tables = set(insp.get_table_names()) - {"alembic_version"}
    if db_tables != set(meta.tables):
        problems.append(f"tables: db-only={sorted(db_tables - set(meta.tables))} "
                        f"model-only={sorted(set(meta.tables) - db_tables)}")

    for name in sorted(db_tables & set(meta.tables)):
        table = meta.tables[name]

        db_cols = {c["name"]: c for c in insp.get_columns(name)}
        model_cols = {c.name: c for c in table.columns}
        if set(db_cols) != set(model_cols):
            problems.append(f"{name} columns: db-only={sorted(set(db_cols) - set(model_cols))} "
                            f"model-only={sorted(set(model_cols) - set(db_cols))}")
        for col in sorted(set(db_cols) & set(model_cols)):
            if bool(db_cols[col]["nullable"]) != bool(model_cols[col].nullable):
                problems.append(f"{name}.{col} nullability: db={db_cols[col]['nullable']} "
                                f"model={model_cols[col].nullable}")
            db_type = type_signature(db_cols[col]["type"])
            model_type = type_signature(model_cols[col].type)
            if db_type != model_type:
                problems.append(f"{name}.{col} type: db={db_type} model={model_type}")
            db_default = default_signature(db_cols[col].get("default"))
            model_default = default_signature(model_cols[col].server_default)
            if db_default != model_default:
                problems.append(
                    f"{name}.{col} default: db={db_default!r} model={model_default!r}")

        db_pk = tuple(insp.get_pk_constraint(name).get("constrained_columns") or ())
        model_pk = tuple(c.name for c in table.primary_key.columns)
        if set(db_pk) != set(model_pk):
            problems.append(f"{name} PK: db={db_pk} model={model_pk}")

        # FKs compared as (referencing cols, referred table, referred cols) — this is where a
        # composite membership key would silently degrade to a single-column FK.
        db_fks = {(tuple(f["constrained_columns"]), f["referred_table"],
                   tuple(f["referred_columns"])) for f in insp.get_foreign_keys(name)}
        model_fks = set()
        for fk in table.foreign_key_constraints:
            cols = tuple(e.parent.name for e in fk.elements)
            model_fks.add((cols, list(fk.elements)[0].column.table.name,
                           tuple(e.column.name for e in fk.elements)))
        if db_fks != model_fks:
            problems.append(f"{name} FKs: db-only={sorted(db_fks - model_fks)} "
                            f"model-only={sorted(model_fks - db_fks)}")

        db_uq = {frozenset(u["column_names"]) for u in insp.get_unique_constraints(name)}
        model_uq = {frozenset(c.name for c in u.columns)
                    for u in table.constraints
                    if u.__class__.__name__ == "UniqueConstraint"}
        if db_uq != model_uq:
            problems.append(f"{name} UNIQUEs: db-only={sorted(map(sorted, db_uq - model_uq))} "
                            f"model-only={sorted(map(sorted, model_uq - db_uq))}")

        db_ck = {c["name"] for c in insp.get_check_constraints(name)}
        model_ck = {c.name for c in table.constraints
                    if c.__class__.__name__ == "CheckConstraint" and c.name}
        if db_ck != model_ck:
            problems.append(f"{name} CHECKs: db-only={sorted(db_ck - model_ck)} "
                            f"model-only={sorted(model_ck - db_ck)}")

        # PostgreSQL exposes every UNIQUE constraint twice through SQLAlchemy: once from
        # get_unique_constraints(), and again as an index carrying `duplicates_constraint`.
        # Keep the two comparison domains separate so a constraint is not mistaken for an
        # explicit model Index.  Partial unique indexes (such as one legacy import per run) do
        # not carry that marker and remain covered here.
        db_ix = {i["name"]: (bool(i.get("unique")), tuple(i.get("column_names") or ()))
                 for i in insp.get_indexes(name) if not i.get("duplicates_constraint")}
        model_ix = {i.name: (bool(i.unique), tuple(c.name for c in i.columns))
                    for i in table.indexes}
        if db_ix != model_ix:
            problems.append(f"{name} indexes: db={db_ix} model={model_ix}")

    assert not problems, (
        "the migration chain at head and the ORM models disagree:\n  " + "\n  ".join(problems))


# =====================================================================
# 13-16 — the lifecycle transactions, end to end
# =====================================================================

def _final_success(engine, *, job_id, user_id, domain_key):
    """The approved final-success transaction, in one place.

    Returns True if this caller became the brand's manager. Everything — brand identity, the
    claim, the snapshot, the current pointer, the Run, AND the Job's linkage and terminal
    transition — commits together or not at all.
    """
    from sqlalchemy import text as sql
    from sqlalchemy.orm import Session

    with Session(engine, future=True) as s:
        s.begin()
        try:
            s.execute(sql("INSERT INTO brands (id, domain_key, owner_id) "
                          "VALUES (gen_random_uuid(), :d, :o) "
                          "ON CONFLICT (domain_key) DO NOTHING"),
                      {"d": domain_key, "o": user_id})
            bid, owner = s.execute(
                sql("SELECT id, owner_id FROM brands WHERE domain_key = :d FOR UPDATE"),
                {"d": domain_key}).one()

            if owner is None:
                if s.execute(sql("UPDATE brands SET owner_id = :o "
                                 "WHERE id = :i AND owner_id IS NULL"),
                             {"o": user_id, "i": bid}).rowcount == 0:
                    s.rollback()
                    return False
            elif str(owner) != str(user_id):
                s.rollback()
                return False

            snap = s.execute(
                sql("INSERT INTO profile_snapshots (id, brand_id, version, profile, "
                    "schema_version, origin, created_at) VALUES (gen_random_uuid(), :i, "
                    "(SELECT COALESCE(MAX(version),0)+1 FROM profile_snapshots WHERE brand_id=:i),"
                    " '{}'::jsonb, 1, 'live', now()) RETURNING id"), {"i": bid}).scalar()
            s.execute(sql("UPDATE brands SET current_profile_id = :p, updated_at = now() "
                          "WHERE id = :i"), {"p": snap, "i": bid})
            run = s.execute(
                sql("INSERT INTO runs (id, brand_id, kind, status, input_profile_id, "
                    "requested_config, config_schema_version, output, output_schema_version, "
                    "origin, lineage_complete, kind_seq, created_by_user_id, created_at, "
                    "finished_at) "
                    "VALUES (gen_random_uuid(), :i, 'scrape', 'succeeded', :p, '{}'::jsonb, 1, "
                    "'{\"ok\": true}'::jsonb, 1, 'live', true, "
                    "(SELECT COALESCE(MAX(kind_seq),0)+1 FROM runs "
                    "  WHERE brand_id=:i AND kind='scrape'), "
                    ":u, now(), now()) RETURNING id"),
                {"i": bid, "p": snap, "u": user_id}).scalar()
            # THE JOB IS PART OF THE SAME TRANSACTION. Its linkage is written here, not before:
            # until this commits there is no Brand and no Run for it to point at.
            linked = s.execute(
                sql("UPDATE jobs SET brand_id = :b, run_id = :r, status = 'succeeded', "
                    "finished_at = now(), lease_expires_at = NULL "
                    "WHERE id = :j AND status IN ('queued','running')"),
                {"b": bid, "r": run, "j": job_id}).rowcount
            if linked != 1:
                s.rollback()
                return False
            s.commit()
            return True
        except Exception:
            s.rollback()
            raise


def _terminalise_loser(engine, job_id, reason):
    """A SEPARATE transaction. The losing final transaction rolled back — including any job row it
    touched — so the job must be failed on its own, or it stays RUNNING forever."""
    from sqlalchemy import text as sql
    from sqlalchemy.orm import Session

    with Session(engine, future=True) as s:
        s.begin()
        s.execute(sql("UPDATE jobs SET status = 'failed', error = :e, finished_at = now(), "
                      "lease_expires_at = NULL WHERE id = :j AND status IN ('queued','running')"),
                  {"e": reason, "j": job_id})
        s.commit()


def _open_job(engine, user_id, domain_key):
    from sqlalchemy import text as sql
    from sqlalchemy.orm import Session

    with Session(engine, future=True) as s:
        jid = s.execute(
            sql("INSERT INTO jobs (id, access_mode, owner_user_id, status, claimed_by, "
                "lease_expires_at, requested_domain_key) VALUES (gen_random_uuid(), 'user', :u, "
                "'running', 'worker-1', now() + interval '5 minutes', :d) RETURNING id"),
            {"u": user_id, "d": domain_key}).scalar()
        s.commit()
        return jid


def test_first_ever_success_commits_brand_profile_run_and_job_linkage_together(engine):
    """§13. Nothing exists but a running Job that knows only which domain was asked for."""
    from sqlalchemy import text as sql
    from sqlalchemy.orm import Session

    from db.models import User

    dk = f"firstok{uuid.uuid4().hex[:8]}_com"
    with Session(engine, future=True) as s:
        u = User(email=f"fs{uuid.uuid4().hex[:8]}@test.local", hashed_password="x")
        s.add(u)
        s.commit()
        uid = u.id

    jid = _open_job(engine, uid, dk)
    with Session(engine, future=True) as s:
        pre = s.execute(sql("SELECT brand_id, run_id, status, requested_domain_key FROM jobs "
                            "WHERE id = :j"), {"j": jid}).one()
    assert pre[0] is None and pre[1] is None and pre[3] == dk

    assert _final_success(engine, job_id=jid, user_id=uid, domain_key=dk) is True

    with Session(engine, future=True) as s:
        brand = s.execute(sql("SELECT id, owner_id, current_profile_id FROM brands "
                              "WHERE domain_key = :d"), {"d": dk}).one()
        snap = s.execute(sql("SELECT id, version FROM profile_snapshots WHERE brand_id = :b"),
                         {"b": brand[0]}).one()
        run = s.execute(sql("SELECT id, status, input_profile_id FROM runs WHERE brand_id = :b"),
                        {"b": brand[0]}).one()
        job = s.execute(sql("SELECT brand_id, run_id, status FROM jobs WHERE id = :j"),
                        {"j": jid}).one()

    assert str(brand[1]) == str(uid), "the brand was not claimed by the scraping account"
    assert str(brand[2]) == str(snap[0]), "current_profile does not point at the new snapshot"
    assert snap[1] == 1
    assert run[1] == "succeeded" and str(run[2]) == str(snap[0])
    assert str(job[0]) == str(brand[0]), "job.brand_id was not linked at final success"
    assert str(job[1]) == str(run[0]), "job.run_id was not linked at final success"
    assert job[2] == "succeeded"


def test_first_ever_failure_leaves_no_brand_at_all(engine):
    """§14. A failed scrape must not be able to populate the global brand namespace."""
    from sqlalchemy import text as sql
    from sqlalchemy.orm import Session

    from db.models import User

    dk = f"firstfail{uuid.uuid4().hex[:8]}_com"
    with Session(engine, future=True) as s:
        u = User(email=f"ff{uuid.uuid4().hex[:8]}@test.local", hashed_password="x")
        s.add(u)
        s.commit()
        uid = u.id

    jid = _open_job(engine, uid, dk)
    _terminalise_loser(engine, jid, "Scrape processing failed.")   # business failure, no final TX

    with Session(engine, future=True) as s:
        assert s.execute(sql("SELECT count(*) FROM brands WHERE domain_key = :d"),
                         {"d": dk}).scalar() == 0, "a failed scrape created a Brand"
        assert s.execute(sql("SELECT count(*) FROM runs")).scalar() >= 0
        job = s.execute(sql("SELECT status, brand_id, run_id, requested_domain_key FROM jobs "
                            "WHERE id = :j"), {"j": jid}).one()
    assert job[0] == "failed"
    assert job[1] is None and job[2] is None
    assert job[3] == dk, "the failure history lost which domain was requested"


def test_two_first_ever_successes_yield_one_brand_and_a_terminal_loser_job(engine):
    """§15 + §26D. One canonical Brand, and the loser's Job must NOT stay running."""
    from sqlalchemy import text as sql
    from sqlalchemy.orm import Session

    from db.models import User

    dk = f"race2{uuid.uuid4().hex[:8]}_com"
    with Session(engine, future=True) as s:
        a = User(email=f"r2a{uuid.uuid4().hex[:8]}@test.local", hashed_password="x")
        b = User(email=f"r2b{uuid.uuid4().hex[:8]}@test.local", hashed_password="x")
        s.add_all([a, b])
        s.commit()
        a_id, b_id = a.id, b.id

    ja, jb = _open_job(engine, a_id, dk), _open_job(engine, b_id, dk)
    assert _final_success(engine, job_id=ja, user_id=a_id, domain_key=dk) is True
    assert _final_success(engine, job_id=jb, user_id=b_id, domain_key=dk) is False
    _terminalise_loser(engine, jb, "This website is already managed by another account.")

    with Session(engine, future=True) as s:
        assert s.execute(sql("SELECT count(*) FROM brands WHERE domain_key = :d"),
                         {"d": dk}).scalar() == 1, "a duplicate Brand identity was created"
        bid, owner = s.execute(sql("SELECT id, owner_id FROM brands WHERE domain_key = :d"),
                               {"d": dk}).one()
        assert str(owner) == str(a_id)
        assert s.execute(sql("SELECT count(*) FROM profile_snapshots WHERE brand_id = :b"),
                         {"b": bid}).scalar() == 1, "the loser left a snapshot"
        assert s.execute(sql("SELECT count(*) FROM runs WHERE brand_id = :b"),
                         {"b": bid}).scalar() == 1, "the loser left a Run"
        loser = s.execute(sql("SELECT status, brand_id, run_id FROM jobs WHERE id = :j"),
                          {"j": jb}).one()
    assert loser[0] == "failed", f"the losing Job is {loser[0]!r} — it must not stay running"
    assert loser[1] is None and loser[2] is None, "the loser linked itself to the winner's rows"


def test_unclaimed_race_loser_job_also_terminalises(engine):
    """§16 + §26E. Same requirement on the pre-existing-unclaimed path."""
    from sqlalchemy import text as sql
    from sqlalchemy.orm import Session

    from db.models import Brand, User

    dk = f"unc{uuid.uuid4().hex[:8]}_com"
    with Session(engine, future=True) as s:
        a = User(email=f"ua{uuid.uuid4().hex[:8]}@test.local", hashed_password="x")
        b = User(email=f"ub{uuid.uuid4().hex[:8]}@test.local", hashed_password="x")
        s.add_all([a, b, Brand(domain_key=dk, owner_id=None)])
        s.commit()
        a_id, b_id = a.id, b.id

    ja, jb = _open_job(engine, a_id, dk), _open_job(engine, b_id, dk)
    assert _final_success(engine, job_id=ja, user_id=a_id, domain_key=dk) is True
    assert _final_success(engine, job_id=jb, user_id=b_id, domain_key=dk) is False
    _terminalise_loser(engine, jb, "This website is already managed by another account.")

    with Session(engine, future=True) as s:
        bid, owner, current = s.execute(
            sql("SELECT id, owner_id, current_profile_id FROM brands WHERE domain_key = :d"),
            {"d": dk}).one()
        snaps = s.execute(sql("SELECT count(*) FROM profile_snapshots WHERE brand_id = :b"),
                          {"b": bid}).scalar()
        runs = s.execute(sql("SELECT count(*) FROM runs WHERE brand_id = :b"),
                         {"b": bid}).scalar()
        loser = s.execute(sql("SELECT status FROM jobs WHERE id = :j"), {"j": jb}).scalar()
    assert str(owner) == str(a_id) and current is not None
    assert snaps == 1 and runs == 1, "the losing claim left partial business state"
    assert loser == "failed", "the losing Job stayed non-terminal"


def test_the_final_transaction_refuses_a_foreign_owner_even_without_a_preflight(engine):
    """§18. The preflight is cost protection; the final transaction is the authority."""
    from sqlalchemy import text as sql
    from sqlalchemy.orm import Session

    from db.models import Brand, User

    dk = f"foreign{uuid.uuid4().hex[:8]}_com"
    with Session(engine, future=True) as s:
        owner = User(email=f"fo{uuid.uuid4().hex[:8]}@test.local", hashed_password="x")
        other = User(email=f"fx{uuid.uuid4().hex[:8]}@test.local", hashed_password="x")
        s.add_all([owner, other])
        s.flush()
        s.add(Brand(domain_key=dk, owner_id=owner.id))
        s.commit()
        owner_id, other_id = owner.id, other.id

    jid = _open_job(engine, other_id, dk)
    assert _final_success(engine, job_id=jid, user_id=other_id, domain_key=dk) is False
    _terminalise_loser(engine, jid, "This website is already managed by another account.")

    with Session(engine, future=True) as s:
        brand = s.execute(sql("SELECT id, owner_id, current_profile_id FROM brands "
                              "WHERE domain_key = :d"), {"d": dk}).one()
        snaps = s.execute(sql("SELECT count(*) FROM profile_snapshots WHERE brand_id = :b"),
                          {"b": brand[0]}).scalar()
        runs = s.execute(sql("SELECT count(*) FROM runs WHERE brand_id = :b"),
                         {"b": brand[0]}).scalar()
        job = s.execute(sql("SELECT status FROM jobs WHERE id = :j"), {"j": jid}).scalar()
    assert str(brand[1]) == str(owner_id), "ownership transferred"
    assert brand[2] is None, "the brand was mutated by a refused caller"
    assert snaps == 0 and runs == 0, "a refused caller persisted business state"
    assert job == "failed", "a refused scrape reported success"


def test_the_terminal_transition_predicate_refuses_to_reopen_a_finished_job(session):
    """A CHECK cannot see the previous row, so terminal immutability is a WHERE clause.

    No trigger is added: the rule is stated here as the exact predicate the future repository will
    use, and proven to behave.
    """
    from sqlalchemy import text as sql

    from db.models import Job

    u = _user(session)
    brand = _brand(session)
    run = _run(session, brand, u)
    job = Job(run_id=run.id, brand_id=brand.id, access_mode="user", owner_user_id=u.id,
              status="queued")
    session.add(job)
    session.flush()

    finish = sql("UPDATE jobs SET status = :s, finished_at = now(), lease_expires_at = NULL "
                 "WHERE id = :i AND status IN ('queued','running')")

    assert session.execute(finish, {"s": "succeeded", "i": job.id}).rowcount == 1
    # Every later attempt must affect zero rows — SUCCEEDED -> RUNNING and FAILED -> SUCCEEDED
    # both become impossible.
    assert session.execute(finish, {"s": "failed", "i": job.id}).rowcount == 0
    assert session.execute(
        sql("UPDATE jobs SET status='running' WHERE id=:i AND status IN ('queued','running')"),
        {"i": job.id}).rowcount == 0
    session.flush()
    assert session.execute(sql("SELECT status FROM jobs WHERE id = :i"),
                           {"i": job.id}).scalar() == "succeeded"
