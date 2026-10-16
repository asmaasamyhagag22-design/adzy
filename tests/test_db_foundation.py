# -*- coding: utf-8 -*-
"""DB PHASE 1A — the schema foundation, proven as far as this environment allows.

TWO CLASSES OF EVIDENCE, kept apart on purpose.

  STATIC   — the checks in this file. They read the generated DDL and the model metadata. They
             prove the CONSTRAINTS EXIST and are shaped correctly.
  RUNTIME  — `tests/test_db_postgres_integration.py`. It proves PostgreSQL actually REJECTS the
             rows those constraints forbid, and it skips unless a disposable test database is
             configured.

A constraint that compiles is not a constraint that was enforced. Nothing here claims row-lock
behaviour, and nothing here runs on SQLite: SQLite would happily pass a suite full of guarantees it
does not provide, which is worse than no test at all.

Nothing in this file connects to anything.
"""
from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
_DB = _ROOT / "db"
_SCHEMA_SQL = _DB / "schema.sql"


# =====================================================================
# layering — always runs, needs no driver (the C7 lesson, applied early)
# =====================================================================

def _imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names.add(node.module)
    return names


def test_the_db_layer_never_imports_the_application():
    """Persistence sits BELOW orchestration. If this fails, the cycle C7 removed is forming again."""
    offenders = {}
    for path in _DB.rglob("*.py"):
        bad = sorted(m for m in _imported_modules(path)
                     if m == "api" or m.startswith(("api.", "poster.", "reel.", "scraper.")))
        if bad:
            offenders[str(path.relative_to(_ROOT))] = bad
    assert not offenders, f"the db layer imports upward: {offenders}"


#: The ONE application module allowed to know the db layer exists.
_PERSISTENCE_SEAM = "api/persistence.py"


def test_only_the_persistence_provider_depends_on_the_db_layer():
    """The db layer is reachable from the application through exactly ONE module.

    Until DB2G this test said NO api module may import `db` — correct for a foundation phase, and
    deliberately overtaken by the backend switch, which has to import it somewhere. The replacement
    keeps the property that was actually load-bearing: a single seam. If `db.` imports spread into
    routes and services, "which store is authoritative" stops being one decision made in one place
    and becomes a per-call-site accident — which is the failure the whole cutover design exists to
    prevent. Widening this list is a design change, not a test fix.
    """
    offenders = {}
    for path in (_ROOT / "api").rglob("*.py"):
        relative = path.relative_to(_ROOT).as_posix()
        if relative == _PERSISTENCE_SEAM:
            continue
        bad = sorted(m for m in _imported_modules(path) if m == "db" or m.startswith("db."))
        if bad:
            offenders[relative] = bad
    assert not offenders, (
        f"the db layer is reached outside {_PERSISTENCE_SEAM}, so authority is no longer decided "
        f"in one place: {offenders}")


def test_the_persistence_seam_exists_and_is_the_one_that_imports_the_db_layer():
    """Guards the test above from passing vacuously if the seam is renamed or emptied."""
    seam = _ROOT / _PERSISTENCE_SEAM
    assert seam.is_file(), f"{_PERSISTENCE_SEAM} is missing — the exemption above now hides nothing"
    imported = {m for m in _imported_modules(seam) if m == "db" or m.startswith("db.")}
    assert imported, f"{_PERSISTENCE_SEAM} imports no db module — the exemption is unused"


def test_importing_the_db_package_opens_no_connection():
    """A fresh process, no DSN set. Import must be free, and must not crash for the lack of one."""
    proc = subprocess.run(
        [sys.executable, "-s", "-c",
         "import os;"
         "os.environ.pop('BASEERA_DATABASE_URL', None);"
         "import db, db.config;"
         "print('imported-clean')"],
        cwd=str(_ROOT), capture_output=True, text=True, timeout=180,
        env={"PATH": __import__("os").environ.get("PATH", ""), "PYTHONPATH": str(_ROOT),
             "SYSTEMROOT": __import__("os").environ.get("SYSTEMROOT", "")},
    )
    assert proc.returncode == 0, proc.stderr.strip()[-1200:]
    assert "imported-clean" in proc.stdout


def test_a_missing_dsn_raises_instead_of_defaulting_to_localhost(monkeypatch):
    from db.config import DatabaseNotConfigured, database_url

    monkeypatch.delenv("BASEERA_DATABASE_URL", raising=False)
    with pytest.raises(DatabaseNotConfigured):
        database_url()


def test_sqlite_is_refused_as_a_dsn(monkeypatch):
    """SQLite cannot demonstrate row locks or composite-FK behaviour under concurrency."""
    from db.config import DatabaseNotConfigured, database_url

    monkeypatch.setenv("BASEERA_DATABASE_URL", "sqlite:///./x.db")
    with pytest.raises(DatabaseNotConfigured, match="PostgreSQL"):
        database_url()


def test_a_dsn_never_reaches_a_log_with_its_password():
    from db.config import safe_dsn

    masked = safe_dsn("postgresql+psycopg://baseera:hunter2@db.internal:5432/baseera")
    assert "hunter2" not in masked
    assert "baseera:***@db.internal" in masked


# =====================================================================
# the generated DDL — always runs, reads plain SQL
# =====================================================================

@pytest.fixture(scope="module")
def ddl() -> str:
    assert _SCHEMA_SQL.is_file(), "db/schema.sql is missing — regenerate it from db/models.py"
    return _SCHEMA_SQL.read_text(encoding="utf-8")


def test_every_expected_table_is_created(ddl):
    expected = {
        "users", "brands", "profile_snapshots", "runs", "run_dependencies", "calendar_posts",
        "attempts", "assets", "jobs", "usage_records", "approvals", "brand_owner_inputs",
        "brand_competitors",
        # v1.3 — durable job events, and the gallery split into physical bytes + logical entries.
        "job_events", "gallery_blobs", "gallery_entries",
        # v1.4 — the durable result of a Job that intentionally has NO Run. `/api/run` is a
        # standalone capability-owned job that produces a BusinessProfile and names no brand, so
        # `runs.output` cannot be its authority and its result had nowhere durable to live.
        "job_results",
    }
    for name in expected:
        assert f"CREATE TABLE {name} (" in ddl, f"{name} is not created"
    # No speculative extras.
    created = {line.split()[2] for line in ddl.splitlines() if line.startswith("CREATE TABLE ")}
    assert created == expected, f"unexpected tables: {created ^ expected}"


@pytest.mark.parametrize("name,cols,ref", [
    # every pointer that could otherwise cross its parent
    ("fk_brands_current_profile", "current_profile_id, id", "profile_snapshots (id, brand_id)"),
    ("fk_runs_input_profile", "input_profile_id, brand_id", "profile_snapshots (id, brand_id)"),
    ("fk_runs_selected_attempt", "selected_attempt_id, id", "attempts (id, run_id)"),
    ("fk_runs_calendar_post", "calendar_post_id, brand_id", "calendar_posts (id, brand_id)"),
    ("fk_attempts_run", "run_id, brand_id", "runs (id, brand_id)"),
    ("fk_attempts_asset", "asset_id, brand_id", "assets (id, brand_id)"),
    ("fk_run_dependencies_run", "run_id, brand_id", "runs (id, brand_id)"),
    ("fk_run_dependencies_depends_on", "depends_on_run_id, brand_id", "runs (id, brand_id)"),
    ("fk_usage_records_run", "run_id, brand_id", "runs (id, brand_id)"),
    ("fk_usage_records_attempt", "attempt_id, run_id", "attempts (id, run_id)"),
    ("fk_calendar_posts_calendar_run", "calendar_run_id, brand_id, calendar_run_kind",
     "runs (id, brand_id, kind)"),
    ("fk_approvals_subject_run", "subject_run_id, brand_id, subject_kind",
     "runs (id, brand_id, kind)"),
])
def test_membership_is_enforced_by_a_composite_foreign_key(ddl, name, cols, ref):
    """Each of these makes a cross-parent pointer UNREPRESENTABLE, not merely discouraged."""
    assert f"CONSTRAINT {name} FOREIGN KEY({cols}) REFERENCES {ref}" in ddl, (
        f"{name} is missing or no longer composite over ({cols}) -> {ref}")


@pytest.mark.parametrize("referenced", [
    "uq_profile_snapshots_id_brand_id", "uq_runs_id_brand_id", "uq_runs_id_brand_id_kind",
    "uq_attempts_id_run_id", "uq_attempts_id_brand_id", "uq_assets_id_brand_id",
    "uq_calendar_posts_id_brand_id",
])
def test_every_composite_fk_has_a_matching_unique_key(ddl, referenced):
    """PostgreSQL refuses a composite FK unless the referenced column set is UNIQUE."""
    assert f"CONSTRAINT {referenced} UNIQUE" in ddl, f"{referenced} is missing"


@pytest.mark.parametrize("constraint", [
    "uq_brands_domain_key",                                  # one brand per domain, globally
    "uq_profile_snapshots_brand_id_version",                 # version race backstop
    "uq_attempts_run_id_ordinal",
    "uq_calendar_posts_calendar_run_id_ordinal",             # post identity is NOT the date
    "uq_jobs_run_id_execution_no",                           # retry ordinal
    "uq_assets_storage_key",
    "uq_users_email",
    "uq_users_legacy_user_id",
    "uq_runs_brand_id_kind_kind_seq",
    "uq_brand_competitors_brand_id_key",
])
def test_identity_and_race_backstops_exist(ddl, constraint):
    assert f"CONSTRAINT {constraint} UNIQUE" in ddl, f"{constraint} is missing"


@pytest.mark.parametrize("check", [
    "ck_users_email_is_normalised",
    "ck_jobs_exactly_one_principal",
    "ck_jobs_running_jobs_name_their_claimant",
    "ck_run_dependencies_no_self_dependency",
    "ck_calendar_posts_parent_is_a_calendar_run",
    "ck_attempts_succeeded_attempts_have_an_asset",
    "ck_runs_only_creative_runs_target_a_post",
    "ck_runs_live_lineage_is_complete",
    "ck_runs_live_runs_know_their_creator",
    "ck_runs_live_rows_know_their_creation_time",
    "ck_profile_snapshots_country_source_is_known",
    "ck_users_legacy_user_id_is_16_hex",
    "ck_runs_runtime_runs_allocate_a_sequence",
    "ck_runs_kind_seq_starts_at_one",
    "ck_approvals_origin_is_known",
    "ck_approvals_live_approvals_name_actor_and_time",
    "ck_approvals_legacy_imports_have_no_fabricated_metadata",
])
def test_the_domain_rules_are_check_constraints(ddl, check):
    assert check in ddl, f"{check} is missing"


def test_schema_object_names_fit_postgresql_identifier_limit():
    """Prevent PostgreSQL's silent 63-byte truncation from weakening exact-name proofs."""
    models = _models()
    names = []
    for table in models.Base.metadata.tables.values():
        names.extend(c.name for c in table.constraints if c.name)
        names.extend(i.name for i in table.indexes if i.name)

    too_long = sorted(name for name in names if len(name.encode("utf-8")) > 63)
    assert not too_long, f"PostgreSQL would truncate these identifiers: {too_long}"


def test_unclaimed_brands_are_representable(ddl):
    """C2: a NULL owner is a real live state, so the column must not be NOT NULL.

    This is the delta from Schema v1.1, which was reviewed against a tree where an unclaimed brand
    refused a re-scrape. The recovered contract claims it instead.
    """
    brands = ddl.split("CREATE TABLE brands (")[1].split(");")[0]
    owner_line = [ln for ln in brands.splitlines() if ln.strip().startswith("owner_id ")]
    assert owner_line, "brands.owner_id is missing"
    assert "NOT NULL" not in owner_line[0], (
        "brands.owner_id is NOT NULL — unclaimed brands become unrepresentable and the export's "
        "incompatible `owner_id NOT NULL` has been re-inherited")


def test_the_email_check_folds_case_AND_whitespace(ddl):
    """Both halves of UserStore's `.strip().lower()`.

    `lower()` alone would still admit " user@example.com " and "user@example.com " as separate
    rows — the same duplicate-account bug wearing different whitespace.
    """
    assert "CHECK (email = btrim(lower(email)))" in ddl, (
        "the email canonicalisation check does not cover whitespace")


def test_legacy_user_identity_is_uuid5_of_the_legacy_id_not_email():
    from db.legacy_identity import legacy_user_uuid

    assert str(legacy_user_uuid("551ece7d16157b81")) == (
        "fb0529d6-b05c-5690-a25d-deb48f5597da")
    assert str(legacy_user_uuid("81e6512a9145e591")) == (
        "bd09714c-29aa-5cc4-9453-a137409b9538")
    assert legacy_user_uuid("551ece7d16157b81") == legacy_user_uuid("551ece7d16157b81")


@pytest.mark.parametrize("invalid", ["", "551ece7d16157b8", "551ECE7D16157B81", "not-a-legacy-id"])
def test_legacy_user_identity_rejects_surfaces_the_old_store_never_issued(invalid):
    from db.legacy_identity import legacy_user_uuid

    with pytest.raises(ValueError, match="16 lowercase hexadecimal"):
        legacy_user_uuid(invalid)


def test_the_first_ever_scrape_lifecycle_is_representable(ddl):
    """A Run cannot exist before its Brand, so a first-ever scrape must be Job-first.

    Asserted as a SHAPE, not a policy: `runs.brand_id` stays NOT NULL (weakening it would turn
    every brand-carrying composite FK into a no-op whenever brand_id were NULL), while a Job can
    exist with no Run, no Brand, and still say which domain was requested.
    """
    runs = ddl.split("CREATE TABLE runs (")[1].split(");")[0]
    brand_line = [ln for ln in runs.splitlines() if ln.strip().startswith("brand_id ")][0]
    assert "NOT NULL" in brand_line, (
        "runs.brand_id became nullable — every composite FK carrying brand_id silently stops "
        "enforcing membership for those rows under MATCH SIMPLE")

    jobs = ddl.split("CREATE TABLE jobs (")[1].split(");")[0]
    for col in ("run_id", "brand_id"):
        line = [ln for ln in jobs.splitlines() if ln.strip().startswith(f"{col} ")][0]
        assert "NOT NULL" not in line, (
            f"jobs.{col} is NOT NULL — a first-ever scrape could not have a durable job before "
            f"its Brand exists")
    assert "requested_domain_key" in jobs, (
        "a failed first-ever scrape would be a Job that cannot say what it was for")


def test_country_provenance_survives_to_the_database(ddl):
    """C6-R1: a bare canonical string cannot record HOW the country was known."""
    snap = ddl.split("CREATE TABLE profile_snapshots (")[1].split(");")[0]
    for col in ("country_canonical", "country_source", "country_raw",
                "primary_language", "language_source"):
        assert col in snap, f"profile_snapshots.{col} is missing"


def test_the_capability_column_stores_a_digest_not_a_token(ddl):
    jobs = ddl.split("CREATE TABLE jobs (")[1].split(");")[0]
    assert "capability_digest VARCHAR(64)" in jobs, (
        "the capability column must be a fixed-width sha256 digest, never a raw bearer token")
    assert "capability_token" not in ddl


def test_delete_actions_default_to_restrict_not_cascade(ddl):
    """History must not be erasable as a side effect of deleting a parent."""
    cascades = [ln.strip() for ln in ddl.splitlines() if "ON DELETE CASCADE" in ln]
    # The only ones permitted: rows that are pure 1:N extensions of their parent and carry no audit
    # value of their own. `job_events` joined the list in v1.3 for the same reason as the other two —
    # an event has no meaning without its job, and jobs are never hard-deleted by the product
    # (archival is a flag). Anything that is EVIDENCE — profiles, runs, approvals, assets, gallery
    # entries — stays RESTRICT, so history cannot be erased as a side effect of deleting a parent.
    permitted = ("brand_owner_inputs", "brand_competitors", "job_events")
    assert len(cascades) == 3, f"unexpected CASCADE: {cascades}"
    assert all(any(name in c for name in permitted) for c in cascades), cascades
    assert "ON DELETE RESTRICT" in ddl


# =====================================================================
# model metadata — needs SQLAlchemy, skipped cleanly without it
# =====================================================================

def _models():
    pytest.importorskip(
        "sqlalchemy",
        reason="SQLAlchemy is not installed in this environment; the DDL checks above still ran")
    from db import models
    return models


def test_the_metadata_matches_the_generated_ddl():
    models = _models()
    names = set(models.Base.metadata.tables)
    created = {ln.split()[2] for ln in _SCHEMA_SQL.read_text(encoding="utf-8").splitlines()
               if ln.startswith("CREATE TABLE ")}
    assert names == created, (
        f"db/schema.sql is stale — regenerate it. metadata-only={names - created}, "
        f"sql-only={created - names}")


def test_the_circular_pointers_are_deferred_to_alter_statements():
    """brands<->profile_snapshots and runs<->attempts reference each other.

    Both must be emitted as ALTER after the tables exist, or creation order decides whether the
    schema can be built at all.
    """
    _models()
    sql = _SCHEMA_SQL.read_text(encoding="utf-8")
    for name in ("fk_brands_current_profile", "fk_runs_selected_attempt",
                 "fk_runs_calendar_post"):
        assert f"ALTER TABLE" in sql and name in sql
        line = [ln for ln in sql.splitlines() if name in ln][0]
        assert line.startswith("ALTER TABLE"), f"{name} is inline; creation order would decide"
