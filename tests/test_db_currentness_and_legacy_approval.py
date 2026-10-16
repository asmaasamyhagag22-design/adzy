# -*- coding: utf-8 -*-
"""Which SWOT is current, and what a migrated approval may say.

Two rules that only became answerable once real legacy data was measured.

CURRENTNESS. The obvious query — "newest succeeded run by created_at" — is wrong here in two
different ways, and the first one is silent:

  * an imported run truthfully carries `created_at = NULL`, because no historical execution time
    exists, and PostgreSQL sorts NULLS **FIRST** on DESC. A legacy SWOT with no timestamp therefore
    outranks every live SWOT ever generated. The RED test below reproduces exactly that.
  * between two live runs, `created_at` is transaction-START time, which can invert against commit
    order — so it cannot settle a race either.

`runs.kind_seq` is allocated per (brand, kind) under the brand row lock, the same serialisation
point that already allocates profile versions. NULL means "predates the sequence" and only an
import may claim it, so `ORDER BY kind_seq DESC NULLS LAST` reads correctly in every case.

APPROVAL. Approval belongs to one immutable SWOT run, so regenerating the SWOT produces a new run
with no approval and the strategy gate closes again — no boolean carries forward. A migrated
approval preserves the FACT while admitting that the actor and the moment were never recorded.

Real PostgreSQL only. These are claims about NULL ordering, row locks and CHECK constraints, none
of which a mock can demonstrate.
"""
from __future__ import annotations

import os
import uuid

import pytest

pytestmark = pytest.mark.skipif(
    not os.environ.get("BASEERA_TEST_DATABASE_URL"),
    reason="BASEERA_TEST_DATABASE_URL is not set — no disposable PostgreSQL to prove against",
)

pytest.importorskip("sqlalchemy", reason="SQLAlchemy is not installed")

# The query the service will use. Kept here as the single written form of the rule.
CURRENT_SWOT = (
    "SELECT id FROM runs WHERE brand_id = :b AND kind = 'swot' AND status = 'succeeded' "
    "ORDER BY kind_seq DESC NULLS LAST LIMIT 1")
NAIVE_SWOT = (
    "SELECT id FROM runs WHERE brand_id = :b AND kind = 'swot' AND status = 'succeeded' "
    "ORDER BY created_at DESC LIMIT 1")
GATE = (
    "SELECT EXISTS (SELECT 1 FROM approvals a WHERE a.decision = 'approved' "
    " AND a.subject_run_id = (" + CURRENT_SWOT + "))")


@pytest.fixture(scope="module")
def _schema():
    """Bring the disposable database to HEAD **through the migration**, never `create_all`.

    `create_all` would build the thirteen tables and NOT `alembic_version`, leaving the database in
    a state no migration produced. Another module that checks for `alembic_version` to decide
    whether to migrate would then find it missing, run `upgrade head`, and hit DuplicateTable on
    `users`. Shared-database fixtures have to agree on one definition of "ready", and the only
    honest one is the migration chain.
    """
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import create_engine, text as sql

    from db.config import database_url

    dsn = database_url(test=True)
    engine = create_engine(dsn, future=True)
    with engine.connect() as conn:
        at_head = conn.execute(
            sql("SELECT to_regclass('alembic_version') IS NOT NULL")).scalar_one()
    if not at_head:
        previous = os.environ.get("BASEERA_DATABASE_URL")
        os.environ["BASEERA_DATABASE_URL"] = dsn
        try:
            command.upgrade(Config(str(__import__("pathlib").Path(__file__).resolve()
                                       .parents[1] / "alembic.ini")), "head")
        finally:
            if previous is None:
                os.environ.pop("BASEERA_DATABASE_URL", None)
            else:
                os.environ["BASEERA_DATABASE_URL"] = previous
    yield engine
    engine.dispose()


@pytest.fixture()
def db(_schema):
    """A session that is rolled back after every test, so no row outlives it."""
    from sqlalchemy.orm import Session

    with Session(_schema, future=True) as s:
        yield s
        s.rollback()


def _seed(s, tag):
    from sqlalchemy import text as sql

    uid, bid = uuid.uuid4(), uuid.uuid4()
    s.execute(sql("INSERT INTO users (id,email,hashed_password) VALUES (:i,:e,'x')"),
              {"i": uid, "e": f"{tag}{uuid.uuid4().hex[:6]}@t.local"})
    s.execute(sql("INSERT INTO brands (id,domain_key,owner_id) VALUES (:i,:d,:o)"),
              {"i": bid, "d": f"{tag}{uuid.uuid4().hex[:6]}_com", "o": uid})
    return uid, bid


def _run(s, bid, uid, *, origin="live", seq=None, created=None, status="succeeded", kind="swot"):
    from sqlalchemy import text as sql

    rid = uuid.uuid4()
    s.execute(sql(
        "INSERT INTO runs (id,brand_id,kind,status,requested_config,config_schema_version,"
        "origin,lineage_complete,kind_seq,created_by_user_id,created_at,imported_at) "
        "VALUES (:i,:b,:k,:st,'{}'::jsonb,1,:o,:lc,:sq,:u,:ca,:ia)"),
        {"i": rid, "b": bid, "k": kind, "st": status, "o": origin,
         "lc": origin != "legacy_import", "sq": seq,
         "u": uid if origin != "legacy_import" else None, "ca": created,
         "ia": "now()" if origin == "legacy_import" else None})
    return rid


def _approve(s, bid, rid, uid=None, *, legacy=False):
    from sqlalchemy import text as sql

    s.execute(sql(
        "INSERT INTO approvals (brand_id,subject_run_id,subject_kind,decision,decided_by_user_id,"
        "decided_at,origin,imported_at) VALUES (:b,:r,'swot','approved',:u,:d,:o,:ia)"),
        {"b": bid, "r": rid, "u": None if legacy else uid,
         "d": None if legacy else "now()",
         "o": "legacy_import" if legacy else "live",
         "ia": "now()" if legacy else None})


def _current(s, bid):
    from sqlalchemy import text as sql
    return s.execute(sql(CURRENT_SWOT), {"b": bid}).scalar()


def _gate_open(s, bid) -> bool:
    from sqlalchemy import text as sql
    return bool(s.execute(sql(GATE), {"b": bid}).scalar())


# =====================================================================
# the RED that motivated kind_seq
# =====================================================================

def test_ordering_by_created_at_picks_the_legacy_run_over_a_newer_live_one(db):
    """The bug, reproduced. This is why the currentness rule is not a timestamp sort."""
    from sqlalchemy import text as sql

    uid, bid = _seed(db, "red")
    legacy = _run(db, bid, uid, origin="legacy_import", seq=None, created=None)
    live = _run(db, bid, uid, origin="live", seq=1, created="2026-08-16 10:00:00+00")

    assert db.execute(sql(NAIVE_SWOT), {"b": bid}).scalar() == legacy, (
        "the naive query no longer prefers the NULL-timestamp legacy row — re-derive this rule "
        "before trusting the fix")
    assert _current(db, bid) == live, "kind_seq ordering must prefer the live run"


# =====================================================================
# the currentness matrix
# =====================================================================

def test_a_legacy_swot_alone_is_current_and_its_import_opens_the_gate(db):
    uid, bid = _seed(db, "ca")
    legacy = _run(db, bid, uid, origin="legacy_import")
    assert _current(db, bid) == legacy
    assert _gate_open(db, bid) is False, "no approval yet"
    _approve(db, bid, legacy, legacy=True)
    assert _gate_open(db, bid) is True, "the imported approval must keep the gate open"


def test_a_new_live_swot_supersedes_the_legacy_one_and_closes_the_gate(db):
    uid, bid = _seed(db, "cb")
    legacy = _run(db, bid, uid, origin="legacy_import")
    _approve(db, bid, legacy, legacy=True)
    live1 = _run(db, bid, uid, origin="live", seq=1, created="2026-08-16 11:00+00")

    assert _current(db, bid) == live1
    assert _gate_open(db, bid) is False, (
        "the old approval authorised the NEW swot — approval must not carry forward")


def test_approving_the_current_live_swot_opens_the_gate_again(db):
    uid, bid = _seed(db, "cc")
    _run(db, bid, uid, origin="legacy_import")
    live1 = _run(db, bid, uid, origin="live", seq=1, created="2026-08-16 11:00+00")
    _approve(db, bid, live1, uid)
    assert _gate_open(db, bid) is True


def test_regenerating_again_closes_the_gate_again(db):
    uid, bid = _seed(db, "cd")
    live1 = _run(db, bid, uid, origin="live", seq=1, created="2026-08-16 11:00+00")
    _approve(db, bid, live1, uid)
    live2 = _run(db, bid, uid, origin="live", seq=2, created="2026-08-16 12:00+00")

    assert _current(db, bid) == live2
    assert _gate_open(db, bid) is False


def test_a_failed_newer_run_does_not_become_current(db):
    uid, bid = _seed(db, "ce")
    live1 = _run(db, bid, uid, origin="live", seq=1, created="2026-08-16 11:00+00")
    _run(db, bid, uid, origin="live", seq=2, created="2026-08-16 13:00+00", status="failed")
    assert _current(db, bid) == live1


def test_identical_timestamps_still_resolve_deterministically(db):
    """Two runs committed in one order can carry the same transaction-start clock."""
    uid, bid = _seed(db, "cf")
    same = "2026-08-16 14:00:00+00"
    _run(db, bid, uid, origin="live", seq=1, created=same)
    newer = _run(db, bid, uid, origin="live", seq=2, created=same)
    assert _current(db, bid) == newer


def test_the_sequence_is_unique_per_brand_and_kind(db):
    """The backstop behind the brand row lock: a forgotten lock becomes a violation."""
    from sqlalchemy.exc import IntegrityError

    uid, bid = _seed(db, "cg")
    _run(db, bid, uid, origin="live", seq=1, created="2026-08-16 10:00+00")
    with pytest.raises(IntegrityError) as e:
        _run(db, bid, uid, origin="live", seq=1, created="2026-08-16 10:05+00")
        db.flush()
    assert "uq_runs_brand_id_kind_kind_seq" in str(e.value.orig)
    db.rollback()


def test_a_sequence_is_per_kind_not_per_brand(db):
    """A strategy run and a swot run may both be number 1 for the same brand."""
    uid, bid = _seed(db, "ch")
    _run(db, bid, uid, origin="live", seq=1, created="2026-08-16 10:00+00", kind="swot")
    _run(db, bid, uid, origin="live", seq=1, created="2026-08-16 10:01+00", kind="strategy")
    db.flush()


# =====================================================================
# what a migrated approval may and may not say
# =====================================================================

def test_a_migrated_approval_keeps_the_fact_and_admits_the_unknowns(db):
    from sqlalchemy import text as sql

    uid, bid = _seed(db, "ap")
    legacy = _run(db, bid, uid, origin="legacy_import")
    _approve(db, bid, legacy, legacy=True)
    db.flush()

    row = db.execute(sql(
        "SELECT decision, origin, decided_at, decided_by_user_id, imported_at IS NOT NULL "
        "FROM approvals WHERE subject_run_id = :r"), {"r": legacy}).one()
    assert row[0] == "approved", "the approval FACT must survive the migration"
    assert row[1] == "legacy_import"
    assert row[2] is None, "a decision time was invented"
    assert row[3] is None, "an approver was invented"
    assert row[4] is True, "the import must record when it arrived"


def test_a_live_approval_must_name_both_its_actor_and_its_moment(db):
    from sqlalchemy.exc import IntegrityError

    uid, bid = _seed(db, "lv")
    run = _run(db, bid, uid, origin="live", seq=1, created="2026-08-16 10:00+00")
    from sqlalchemy import text as sql
    with pytest.raises(IntegrityError) as e:
        db.execute(sql("INSERT INTO approvals (brand_id,subject_run_id,subject_kind,decision,"
                       "decided_by_user_id,decided_at,origin) VALUES (:b,:r,'swot','approved',"
                       ":u,NULL,'live')"), {"b": bid, "r": run, "u": uid})
        db.flush()
    assert "ck_approvals_live_approvals_name_actor_and_time" in str(e.value.orig)
    db.rollback()


def test_a_second_import_for_one_run_is_refused(db):
    """Migration idempotency: re-running must not double a historical signature."""
    from sqlalchemy.exc import IntegrityError

    uid, bid = _seed(db, "id")
    legacy = _run(db, bid, uid, origin="legacy_import")
    _approve(db, bid, legacy, legacy=True)
    db.flush()
    with pytest.raises(IntegrityError) as e:
        _approve(db, bid, legacy, legacy=True)
        db.flush()
    assert "uq_approvals_one_import_per_run" in str(e.value.orig)
    db.rollback()


def test_live_approvals_remain_an_append_only_log(db):
    """A person may approve, and approve again later; that history is not collapsed."""
    from sqlalchemy import text as sql

    uid, bid = _seed(db, "lg")
    run = _run(db, bid, uid, origin="live", seq=1, created="2026-08-16 10:00+00")
    for moment in ("2026-08-16 10:30+00", "2026-08-16 10:31+00"):
        db.execute(sql("INSERT INTO approvals (brand_id,subject_run_id,subject_kind,decision,"
                       "decided_by_user_id,decided_at,origin) VALUES (:b,:r,'swot','approved',"
                       ":u,:d,'live')"), {"b": bid, "r": run, "u": uid, "d": moment})
    db.flush()
    assert db.execute(sql("SELECT count(*) FROM approvals WHERE subject_run_id = :r"),
                      {"r": run}).scalar() == 2
