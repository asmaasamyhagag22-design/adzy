# -*- coding: utf-8 -*-
"""The cutover safety checks, driven to BOTH verdicts.

A check that has only ever observed the happy path is not a check. Each of the four is made to FAIL
on a deliberately introduced fault and then to PASS once the fault is removed.

The defect that motivated this file: the no-mutation and collision checks used a hand-kept table
list carrying `usage_events` — a name that does not exist — while omitting `run_dependencies` and
`usage_records`. A collision check built on that list could not have noticed a real pre-existing
`usage_records` table. The list is now derived from ORM metadata, and the first test below pins the
three names that were wrong.
"""
from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
_HOSTED = _ROOT / "scratchpad" / "hosted"

# A HARD import, deliberately. `pytest.importorskip` here would skip every test in this file when
# the tooling is missing - including the tests whose whole job is to notice that it is missing -
# and the run would go green having exercised none of Gate 1's proof surface.
if not _HOSTED.is_dir():                                        # pragma: no cover - environment
    raise RuntimeError(
        "the Gate 1 hosted tooling is missing at %s; these tests cannot pass without it, and "
        "skipping them would report a green run over an unexercised cutover" % _HOSTED)
sys.path.insert(0, str(_HOSTED))

import cutover_checks                                                        # noqa: E402


# =====================================================================
# 1. the census is derived, not hand-kept
# =====================================================================

def test_the_table_set_comes_from_orm_metadata_and_carries_the_names_that_were_wrong():
    from db.models import Base

    derived = cutover_checks.application_tables(include_schema_state=False)
    assert derived == tuple(sorted(Base.metadata.tables)), (
        "the census must be exactly the ORM's tables, not a copy that can drift")

    assert "usage_records" in derived
    assert "run_dependencies" in derived
    assert "usage_events" not in derived, (
        "`usage_events` is the stale name that made the old collision check unable to see a real "
        "`usage_records` table")


def test_the_fallback_list_cannot_rot_silently():
    """If `db.models` is unimportable the fallback is used; it must still be the truth."""
    from db.models import Base

    assert cutover_checks._FALLBACK == tuple(sorted(Base.metadata.tables)), (
        "the fallback list has drifted from the ORM — update it or delete it")


def test_schema_state_table_is_included_explicitly_and_can_be_excluded():
    with_state = cutover_checks.application_tables(include_schema_state=True)
    without = cutover_checks.application_tables(include_schema_state=False)

    assert "alembic_version" in with_state
    assert "alembic_version" not in without
    assert set(with_state) - set(without) == {"alembic_version"}


# =====================================================================
# 2. the census diff detects a mutation OUTSIDE jobs
# =====================================================================

def test_census_diff_is_silent_when_nothing_moved():
    before = {"users": 2, "usage_records": 0, "jobs": 0}
    assert cutover_checks.census_diff(before, dict(before)) == {}


def test_census_diff_detects_a_row_added_outside_jobs():
    before = {"users": 2, "usage_records": 0, "jobs": 0}
    after = {"users": 2, "usage_records": 1, "jobs": 0}

    moved = cutover_checks.census_diff(before, after)

    assert moved == {"usage_records": (0, 1)}, (
        "a jobs-only check would have called this clean")


def test_census_diff_detects_a_table_appearing_or_disappearing():
    assert cutover_checks.census_diff({"users": 2}, {"users": 2, "runs": 0}) == {"runs": (None, 0)}
    assert cutover_checks.census_diff({"users": 2, "runs": 0}, {"users": 2}) == {"runs": (0, None)}
    # -1 is "table absent"; a table that appears during a probe must not read as unchanged.
    assert cutover_checks.census_diff({"runs": -1}, {"runs": 0}) == {"runs": (-1, 0)}


# =====================================================================
# 3. the collision check, both directions
# =====================================================================

def test_collision_check_passes_on_a_clean_public_schema():
    assert cutover_checks.collisions([]) == []
    assert cutover_checks.collisions(["some_unrelated_table", "kv_store"]) == []


def test_collision_check_fails_on_a_real_collision():
    assert cutover_checks.collisions(["usage_records"]) == ["usage_records"]
    assert cutover_checks.collisions(["alembic_version"]) == ["alembic_version"]
    assert cutover_checks.collisions(["brands", "runs", "unrelated"]) == ["brands", "runs"]


def test_the_collision_check_would_have_missed_usage_records_under_the_old_list():
    """The regression, stated as a test rather than as a comment."""
    stale_list = ("users", "brands", "usage_events")          # the list that shipped
    assert cutover_checks.collisions(["usage_records"], stale_list) == []
    assert cutover_checks.collisions(["usage_records"]) == ["usage_records"]


# =====================================================================
# 4. the Storage manifest comparison, both directions
# =====================================================================

def _obj(name: str, size: int, updated: str = "2026-08-01T00:00:00Z") -> dict:
    return {"name": name, "updated_at": updated, "metadata": {"size": size}}


def test_manifest_digest_is_stable_for_an_unchanged_listing():
    listing = [_obj("gallery/aa/one.png", 10), _obj("gallery/bb/two.png", 20)]
    first = cutover_checks.manifest_digest(listing)
    # a re-listing may arrive in a different order and with re-serialised timestamps
    second = cutover_checks.manifest_digest(
        [_obj("gallery/bb/two.png", 20, "2026-08-02T11:22:33.456Z"),
         _obj("gallery/aa/one.png", 10, "2026-08-02T11:22:33.456Z")])
    assert first == second, "an inert re-read must not look like a mutation"


def test_manifest_digest_detects_an_added_object():
    before = cutover_checks.manifest_digest([_obj("gallery/aa/one.png", 10)])
    after = cutover_checks.manifest_digest(
        [_obj("gallery/aa/one.png", 10), _obj("gallery/cc/new.png", 33)])
    assert before != after
    assert before[1] == 1 and after[1] == 2


def test_manifest_digest_detects_a_removed_object_and_a_changed_size():
    two = [_obj("gallery/aa/one.png", 10), _obj("gallery/bb/two.png", 20)]
    assert cutover_checks.manifest_digest(two) != cutover_checks.manifest_digest(two[:1])
    resized = [_obj("gallery/aa/one.png", 10), _obj("gallery/bb/two.png", 21)]
    assert cutover_checks.manifest_digest(two) != cutover_checks.manifest_digest(resized)
    assert cutover_checks.manifest_digest(two)[1] == cutover_checks.manifest_digest(resized)[1], (
        "the count is equal, so only the digest can catch this")


# =====================================================================
# 5. the census against a REAL database, driven to both verdicts
# =====================================================================

pg = pytest.mark.skipif(
    not os.environ.get("BASEERA_TEST_DATABASE_URL"),
    reason="BASEERA_TEST_DATABASE_URL is not set — no disposable PostgreSQL to prove against")


@pg
def test_row_census_detects_a_real_insert_into_usage_records(monkeypatch):
    """The end-to-end negative test: insert one row outside jobs, and the comparator must fail."""
    pytest.importorskip("alembic")
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import create_engine, text

    from db.config import database_url
    from db.test_database_safety import require_disposable_test_database

    url = database_url(test=True)
    require_disposable_test_database(url)
    monkeypatch.setenv("BASEERA_DATABASE_URL", url)
    monkeypatch.delenv("PGOPTIONS", raising=False)

    schema = "adzy_census_" + uuid.uuid4().hex[:8]
    engine = create_engine(url, connect_args={"connect_timeout": 5})
    with engine.begin() as conn:
        conn.execute(text('CREATE SCHEMA "%s"' % schema))
    try:
        connection = engine.connect()
        connection.execute(text('SET search_path TO "%s"' % schema))
        connection.commit()

        config = Config(str(_ROOT / "alembic.ini"))
        config.attributes["connection"] = connection
        command.upgrade(config, "head")
        connection.commit()

        tables = cutover_checks.application_tables()
        before = cutover_checks.row_census(connection, tables)
        assert before["usage_records"] == 0
        assert before["alembic_version"] == 1
        assert -1 not in before.values(), "every application table should exist after the migration"

        # --- the deliberate mutation, outside jobs -------------------------
        # `usage_records.brand_id` and `.run_id` are NOT NULL with real foreign keys, so the drill
        # creates the minimum parents the schema demands. That makes the evidence stronger, not
        # weaker: three tables move, and the comparator has to see all three.
        # The schema enforces real invariants on a LIVE run: `ck_runs_live_rows_know_their_
        # creation_time` demands created_at, and `ck_runs_live_runs_know_their_creator` demands
        # created_by_user_id, and `ck_runs_runtime_runs_allocate_a_sequence` demands kind_seq.
        # The probe builds the chain the schema requires rather than working around it, so
        # four tables move and the comparator has to see all four.
        connection.execute(text(
            "INSERT INTO users (email, hashed_password) "
            "VALUES ('census-probe@census-probe.invalid', 'x')"))
        connection.execute(text(
            "INSERT INTO brands (domain_key) VALUES ('census-probe.invalid')"))
        connection.execute(text(
            "INSERT INTO runs (brand_id, kind, created_at, created_by_user_id, kind_seq) "
            "SELECT b.id, 'poster', now(), u.id, 1 FROM brands b, users u "
            "WHERE b.domain_key = 'census-probe.invalid' "
            "AND u.email = 'census-probe@census-probe.invalid'"))
        connection.execute(text(
            "INSERT INTO usage_records (brand_id, run_id, provider, model) "
            "SELECT r.brand_id, r.id, 'census-probe', 'census-probe' FROM runs r "
            "JOIN brands b ON b.id = r.brand_id WHERE b.domain_key = 'census-probe.invalid'"))
        connection.commit()

        during = cutover_checks.row_census(connection, tables)
        moved = cutover_checks.census_diff(before, during)
        assert moved.get("usage_records") == (0, 1), (
            "the comparator failed to see a mutation outside jobs: %s" % moved)
        assert set(moved) == {"users", "brands", "runs", "usage_records"}, (
            "the comparator should report every table that moved, got %s" % sorted(moved))
        assert "jobs" not in moved

        # --- remove them and the comparator must go quiet again ------------
        connection.execute(text("DELETE FROM usage_records WHERE provider = 'census-probe'"))
        connection.execute(text(
            "DELETE FROM runs WHERE brand_id IN "
            "(SELECT id FROM brands WHERE domain_key = 'census-probe.invalid')"))
        connection.execute(text("DELETE FROM brands WHERE domain_key = 'census-probe.invalid'"))
        connection.execute(text(
            "DELETE FROM users WHERE email = 'census-probe@census-probe.invalid'"))
        connection.commit()
        after = cutover_checks.row_census(connection, tables)
        assert cutover_checks.census_diff(before, after) == {}, (
            "the comparator still reports a difference after the test row was removed")
        connection.close()
    finally:
        with engine.begin() as conn:
            conn.execute(text('DROP SCHEMA IF EXISTS "%s" CASCADE' % schema))
        engine.dispose()


@pg
def test_collision_check_against_a_real_table(monkeypatch):
    """Create a genuine `usage_records` where the cutover would create one, and require a catch."""
    from sqlalchemy import create_engine, text

    from db.config import database_url
    from db.test_database_safety import require_disposable_test_database

    url = database_url(test=True)
    require_disposable_test_database(url)

    schema = "adzy_collide_" + uuid.uuid4().hex[:8]
    engine = create_engine(url, connect_args={"connect_timeout": 5})
    try:
        with engine.begin() as conn:
            conn.execute(text('CREATE SCHEMA "%s"' % schema))

        def listing() -> list[str]:
            with engine.connect() as conn:
                return [r[0] for r in conn.execute(text(
                    "SELECT tablename FROM pg_tables WHERE schemaname = :s"), {"s": schema}).all()]

        assert cutover_checks.collisions(listing()) == [], "a clean schema must not collide"

        with engine.begin() as conn:
            conn.execute(text('CREATE TABLE "%s".usage_records (id int PRIMARY KEY)' % schema))
        assert cutover_checks.collisions(listing()) == ["usage_records"], (
            "the collision check did not see a real application table already in place")

        with engine.begin() as conn:
            conn.execute(text('DROP TABLE "%s".usage_records' % schema))
        assert cutover_checks.collisions(listing()) == []
    finally:
        with engine.begin() as conn:
            conn.execute(text('DROP SCHEMA IF EXISTS "%s" CASCADE' % schema))
        engine.dispose()


# =====================================================================
# 6. recursive object collection: folder rows are not objects
# =====================================================================

def _folder(name: str) -> dict:
    """Supabase returns a sub-folder as a pseudo-row with a null id and null metadata."""
    return {"name": name, "id": None, "updated_at": None, "metadata": None}


def _stored(name: str, size: int) -> dict:
    return {"name": name, "id": "00000000-0000-0000-0000-%012d" % size,
            "updated_at": "2026-08-01T00:00:00Z", "metadata": {"size": size}}


def _fake_bucket(tree: dict):
    """tree maps a prefix to the page it returns; fetch_page(prefix, offset) reads it."""
    def fetch_page(prefix: str, offset: int) -> list:
        return tree.get(prefix, [])[offset:offset + 100]
    return fetch_page


def test_folder_rows_are_not_objects():
    assert cutover_checks.is_object(_stored("a.png", 10)) is True
    assert cutover_checks.is_object(_folder("gallery")) is False
    # a row with an id but no metadata is not a usable object either
    assert cutover_checks.is_object({"name": "x", "id": "abc", "metadata": None}) is False


def test_collect_objects_recurses_into_nested_prefixes():
    """The real gallery layout: gallery/<2-hex>/<sha>.png — two levels below the root."""
    tree = {
        "": [_folder("gallery")],
        "gallery": [_folder("ab"), _folder("cd")],
        "gallery/ab": [_stored("aaaa.png", 111)],
        "gallery/cd": [_stored("cccc.png", 222), _stored("dddd.png", 333)],
    }

    objects = cutover_checks.collect_objects(_fake_bucket(tree))

    assert [o["name"] for o in objects] == [
        "gallery/ab/aaaa.png", "gallery/cd/cccc.png", "gallery/cd/dddd.png"]
    assert cutover_checks.manifest_digest(objects)[1] == 3, (
        "the count must be ACTUAL objects, not folder rows")


def test_a_single_level_listing_would_have_found_no_object_at_all():
    """Why this matters: the previous drill listed only prefix '' and never reached a real object."""
    tree = {
        "": [_folder("gallery")],
        "gallery": [_folder("ab")],
        "gallery/ab": [_stored("aaaa.png", 111)],
    }
    shallow = tree[""]                                  # what the old code treated as the manifest
    assert cutover_checks.manifest_digest(shallow)[1] == 1, "it counted one FOLDER as one object"
    assert [o["name"] for o in cutover_checks.collect_objects(_fake_bucket(tree))] == [
        "gallery/ab/aaaa.png"]


def test_collect_objects_detects_a_probe_added_deep_in_a_prefix():
    before_tree = {
        "": [_folder("gallery")],
        "gallery": [_folder("ab")],
        "gallery/ab": [_stored("aaaa.png", 111)],
    }
    after_tree = {
        "": [_folder("gallery"), _folder("zero-mutation-drill")],
        "gallery": [_folder("ab")],
        "gallery/ab": [_stored("aaaa.png", 111)],
        "zero-mutation-drill": [_stored("probe.txt", 19)],
    }

    before = cutover_checks.manifest_digest(cutover_checks.collect_objects(_fake_bucket(before_tree)))
    after = cutover_checks.manifest_digest(cutover_checks.collect_objects(_fake_bucket(after_tree)))

    assert before != after
    assert before[1] == 1 and after[1] == 2

    # and removing it must restore the EXACT baseline, not merely the count
    restored = cutover_checks.manifest_digest(
        cutover_checks.collect_objects(_fake_bucket(before_tree)))
    assert restored == before


def test_collect_objects_paginates_within_a_prefix():
    page = [_stored("f%03d.png" % i, i) for i in range(250)]

    def fetch_page(prefix: str, offset: int) -> list:
        return page[offset:offset + 100] if prefix == "" else []

    objects = cutover_checks.collect_objects(fetch_page)
    assert len(objects) == 250, "pagination stopped early"


# =====================================================================
# 6. the leak detector, driven adversarially
#
# Every synthetic credential below is ASSEMBLED FROM PARTS rather than written as one literal, so
# this file cannot itself trip a secret scanner and cannot be mistaken for a real key.
#
# The defect this pins: the redaction in each probe script used to be a private per-file regex that
# knew only the JWT shape. Supabase's new API keys are `sb_secret_...` — opaque, NOT JWTs — so a
# per-file JWT regex would have printed one verbatim. One shared detector, tested here, is the fix.
# =====================================================================

_JWT = ("eyJ" + "hbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9"
        + ".eyJyb2xlIjoic2VydmljZV9yb2xlIn0" + ".c3ludGhldGljLXNpZ25hdHVyZQ")
_SB_SECRET = "sb_" + "secret_" + "F7k2Qm9Xr4Tv6Yb1Nc8Ld3Zp5Ws0Hj"
_SB_PUBLISHABLE = "sb_" + "publishable_" + "A1b2C3d4E5f6G7h8J9k0"
_OPAQUE = "Zg7Q2mVx8Lp4Rt6Yn1Bc"          # no credential SHAPE at all
_DSN = "postgresql+psycopg://postgres.abcdefgh:" + "Tr0ub4dor-and-3" + "@pooler.example.com:5432/postgres"


@pytest.fixture()
def leak_audit(monkeypatch, tmp_path):
    """The shared auditor, pointed at a synthetic hosted.env instead of the real one."""
    module = pytest.importorskip("leak_audit")
    env_file = tmp_path / "hosted.env"
    env_file.write_text(
        "SUPABASE_SERVICE_ROLE_KEY=%s\n"
        "BASEERA_AUTH_SECRET=%s\n"
        "BASEERA_DATABASE_URL=%s\n"
        "SUPABASE_URL=https://project.supabase.co\n" % (_SB_SECRET, _OPAQUE, _DSN),
        encoding="utf-8")
    monkeypatch.setattr(module, "HOSTED_ENV", str(env_file))
    return module


def _env():
    return {"SUPABASE_SERVICE_ROLE_KEY": _SB_SECRET, "BASEERA_AUTH_SECRET": _OPAQUE,
            "BASEERA_DATABASE_URL": _DSN}


def test_a_synthetic_jwt_shaped_credential_is_detected_and_redaction_removes_it():
    log = "connection refused; apikey=%s was rejected" % _JWT

    report = cutover_checks.audit_text(log, {})
    assert report["shapes"].get("jwt") == 1, "a JWT-shaped credential must be seen by SHAPES alone"
    assert not cutover_checks.is_clean(report)

    cleaned = cutover_checks.redact(log)
    assert _JWT not in cleaned
    assert "<jwt-redacted>" in cleaned
    assert cutover_checks.is_clean(cutover_checks.audit_text(cleaned, {}))


def test_a_synthetic_sb_secret_key_is_detected_where_a_jwt_only_regex_would_have_missed_it():
    log = "storage list failed; apikey=%s" % _SB_SECRET

    # The regression, stated as an assertion: the JWT pattern does NOT match this key. Supabase's
    # new keys are opaque, not JWTs, so a per-file JWT-only redactor would have logged this
    # credential verbatim.
    assert not cutover_checks.CREDENTIAL_PATTERNS["jwt"].search(log)

    report = cutover_checks.audit_text(log, {})
    assert report["shapes"].get("supabase_secret_key") == 1
    assert not cutover_checks.is_clean(report)

    cleaned = cutover_checks.redact(log)
    assert _SB_SECRET not in cleaned
    assert "<supabase-secret-key-redacted>" in cleaned
    assert cutover_checks.is_clean(cutover_checks.audit_text(cleaned, {}))


def test_the_same_key_inside_a_bearer_header_is_still_removed_exactly_once():
    """Two patterns overlap here. Redaction is a single left-to-right pass, so the leftmost match
    consumes the whole run: the label is the header's, and the key is gone either way."""
    log = "storage list failed; Authorization: Bearer %s" % _SB_SECRET

    cleaned = cutover_checks.redact(log)
    assert _SB_SECRET not in cleaned
    assert cleaned.count("-redacted>") == 1, (
        "a second pattern must not re-match the placeholder the first one wrote")
    assert cutover_checks.redact(cleaned) == cleaned, "redaction must be idempotent"
    assert cutover_checks.is_clean(cutover_checks.audit_text(cleaned, {}))


def test_every_credential_pattern_is_driven_to_a_positive_match():
    """A pattern that has never matched anything is not a detector.

    This pins a defect that shipped: `aws_access_key_id` was written with two literal 0x08 bytes
    where `\b` was meant, so it could only have matched text containing a backspace character. It
    was never noticed because nothing ever fed it a real key shape.
    """
    assert set(cutover_checks.CREDENTIAL_SAMPLES) == set(cutover_checks.CREDENTIAL_PATTERNS), (
        "every pattern needs a sample, or it can ship unmatched")

    for label, sample in cutover_checks.CREDENTIAL_SAMPLES.items():
        assert cutover_checks.CREDENTIAL_PATTERNS[label].search(sample), (
            "%s matches nothing — check its escapes" % label)
        cleaned = cutover_checks.redact(sample)
        assert sample not in cleaned, "%s survives redaction" % label
        assert cutover_checks.redact(cleaned) == cleaned, "%s redaction is not idempotent" % label
        assert cutover_checks.is_clean(cutover_checks.audit_text(cleaned, {}))

    for pattern in cutover_checks.CREDENTIAL_PATTERNS.values():
        assert chr(8) not in pattern.pattern, (
            "a literal backspace in a pattern means an escape was interpreted when the file "
            "was written")


def test_the_publishable_key_and_a_dsn_password_are_recognised_too():
    assert cutover_checks.CREDENTIAL_PATTERNS["supabase_publishable_key"].search(_SB_PUBLISHABLE)
    assert _SB_PUBLISHABLE not in cutover_checks.redact(_SB_PUBLISHABLE)

    report = cutover_checks.audit_text("could not connect to %s" % _DSN, _env())
    assert report["shapes"].get("dsn_with_credentials") == 1
    # The password is watched on its own, because a driver error usually leaks PART of a DSN.
    assert "BASEERA_DATABASE_URL (password component)" in report["values"]


def test_a_literal_env_secret_with_no_credential_shape_is_caught_by_the_values_layer():
    """The two layers are not redundant, and this is the case that proves it."""
    log = "auth secret fingerprint check used %s" % _OPAQUE

    shapes_only = cutover_checks.audit_text(log, {})
    assert shapes_only["shapes"] == {}, "this value deliberately has no credential shape"
    assert cutover_checks.is_clean(shapes_only), "SHAPES alone cannot see it — that is the point"

    with_env = cutover_checks.audit_text(log, _env())
    assert with_env["values"] == ["BASEERA_AUTH_SECRET"]
    assert not cutover_checks.is_clean(with_env)

    # Honest limitation, pinned: redaction is shape-based, so it does NOT clear an opaque value.
    # Only the VALUES layer stops this one, which is why a missing hosted.env must never read SAFE.
    assert _OPAQUE in cutover_checks.redact(log)


def test_a_sanitized_log_is_reported_safe_to_share(leak_audit, tmp_path, capsys):
    artefact = tmp_path / "phase_x.log"
    artefact.write_text(cutover_checks.redact(
        "[CONFIRMED] X1: connected as postgres.abcdefgh to aws-1-eu-west-1.pooler.supabase.com\n"
        "[CONFIRMED] X2: apikey=%s accepted\n"
        "[CONFIRMED] X3: dsn=%s\n" % (_SB_SECRET, _DSN)), encoding="utf-8")

    monkey = pytest.MonkeyPatch()
    monkey.setattr(sys, "argv", ["leak_audit.py", str(artefact)])
    try:
        code = leak_audit.main()
    finally:
        monkey.undo()

    out = capsys.readouterr().out
    assert code == 0, out
    assert "VERDICT: SAFE TO SHARE" in out
    assert "CLEAN" in out


def test_an_unsanitized_log_is_reported_do_not_share(leak_audit, tmp_path, capsys):
    artefact = tmp_path / "phase_y.log"
    artefact.write_text("[FAILED] Y1: apikey=%s\n" % _SB_SECRET, encoding="utf-8")

    monkey = pytest.MonkeyPatch()
    monkey.setattr(sys, "argv", ["leak_audit.py", str(artefact)])
    try:
        code = leak_audit.main()
    finally:
        monkey.undo()

    out = capsys.readouterr().out
    assert code == 1, out
    assert "VERDICT: DO NOT SHARE" in out
    assert _SB_SECRET not in out, "the auditor must name the artefact, never echo the credential"


def test_a_missing_artefact_is_incomplete_and_never_safe(leak_audit, tmp_path, capsys):
    """Absence read as success is the failure this exit code exists to prevent."""
    monkey = pytest.MonkeyPatch()
    monkey.setattr(sys, "argv", ["leak_audit.py", str(tmp_path / "never_produced.log")])
    try:
        code = leak_audit.main()
    finally:
        monkey.undo()

    out = capsys.readouterr().out
    assert code == 2, out
    assert "INCOMPLETE" in out
    assert "SAFE TO SHARE" not in out


def test_every_hosted_probe_redacts_through_the_shared_detector():
    """No probe keeps a private regex. A second definition is how one tool misses a new key shape."""
    hosted = _ROOT / "scratchpad" / "hosted"
    probes = sorted(p for p in hosted.glob("phase_*.py"))
    assert len(probes) >= 7, "expected the full set of hosted phase probes"

    for probe in probes:
        source = probe.read_text(encoding="utf-8")
        assert "cutover_checks" in source, "%s does not reach the shared detector" % probe.name
        assert "eyJ" not in source, (
            "%s still carries its own JWT literal — that is a second detector" % probe.name)


def test_phase_e_phase_h_and_the_storage_drill_share_one_recursive_collector():
    """A single-level listing of gallery/<2-hex>/<sha256> reaches no object at all."""
    hosted = _ROOT / "scratchpad" / "hosted"
    for name in ("phase_e_precutover_check.py", "phase_h_bridge_proof.py",
                 "phase_s_storage_drill.py"):
        source = (hosted / name).read_text(encoding="utf-8")
        assert "collect_objects" in source, "%s does not use the shared collector" % name
        assert "manifest_digest" in source, "%s does not use the shared digest" % name


# =====================================================================
# 7. the Storage drill's bucket guard, driven to a REFUSAL
#
# `NEVER mutate baseera-gallery` is the standing rule the drill exists to honour. The guard was
# nested inside main(), where it could only ever be observed on the happy path of a live run
# against the real project. It is module-level now, so it can be made to refuse here.
# =====================================================================

import phase_s_storage_drill as drill                                         # noqa: E402

_PRODUCTION = "baseera-gallery"
_REHEARSAL = "baseera-gallery-rehearsal"


class _RecordingClient:
    """Stands in for httpx and records every request that is actually issued."""

    def __init__(self):
        self.requests = []

    def request(self, method, url, **kwargs):
        self.requests.append((method, url))
        return "response"


@pytest.fixture(autouse=True)
def _reset_counter():
    drill.MUTATING_CALLS.clear()
    yield
    drill.MUTATING_CALLS.clear()


def test_the_guard_refuses_the_production_bucket_by_name():
    with pytest.raises(PermissionError) as raised:
        drill.guard(_PRODUCTION, _PRODUCTION)
    assert _PRODUCTION in str(raised.value)

    assert drill.guard(_REHEARSAL, _PRODUCTION) == _REHEARSAL, (
        "the rehearsal bucket must still be allowed, or the drill proves nothing")


def test_a_mutation_aimed_at_production_never_becomes_a_request():
    client = _RecordingClient()

    with pytest.raises(PermissionError):
        drill.mutate(client, "POST", "https://p.supabase.co", _PRODUCTION, "probe.txt",
                     _PRODUCTION, content=b"x")

    assert client.requests == [], "a request was issued for the production bucket"
    assert drill.MUTATING_CALLS.get(_PRODUCTION, 0) == 0, (
        "a refused call must not be counted as issued")


def test_the_mutation_counter_is_live_and_not_a_constant():
    """`production = 0` is only evidence if the same counter can reach a non-zero number."""
    client = _RecordingClient()

    drill.mutate(client, "POST", "https://p.supabase.co", _REHEARSAL, "a.txt", _PRODUCTION)
    drill.mutate(client, "DELETE", "https://p.supabase.co", _REHEARSAL, "a.txt", _PRODUCTION)

    assert drill.MUTATING_CALLS == {_REHEARSAL: 2}
    assert len(client.requests) == 2
    assert all(_PRODUCTION + "/" not in url for _method, url in client.requests)
    assert drill.MUTATING_CALLS.get(_PRODUCTION, 0) == 0


def test_the_drill_addresses_the_bucket_it_was_told_to_address():
    client = _RecordingClient()
    drill.mutate(client, "POST", "https://p.supabase.co", _REHEARSAL, "dir/probe.txt", _PRODUCTION)

    method, url = client.requests[0]
    assert method == "POST"
    assert url == "https://p.supabase.co/storage/v1/object/%s/dir/probe.txt" % _REHEARSAL



# =====================================================================
# 8. a failed listing is not an empty bucket
#
# Each script used to end its own fetcher with `return page if isinstance(page, list) else []`, so
# a 401 or a rate-limit arrived as an EMPTY listing. Two empty listings compare equal, which would
# have let Phase H's `K_no_gallery_mutation` — the whole-state no-mutation proof — CONFIRM having
# measured nothing at all.
# =====================================================================

class _Response:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


class _StubClient:
    def __init__(self, *responses):
        self._responses = list(responses)
        self.calls = []

    def post(self, url, **kwargs):
        self.calls.append((url, kwargs.get("json", {})))
        return self._responses.pop(0) if self._responses else _Response(200, [])


def _object(name, size=1):
    return {"name": name, "id": uuid.uuid4().hex, "metadata": {"size": size}}


def _folder(name):
    return {"name": name, "id": None, "metadata": None}


def test_an_unauthorised_listing_raises_instead_of_reporting_an_empty_bucket():
    client = _StubClient(_Response(401, {"message": "Invalid JWT"}))
    fetch = cutover_checks.storage_page_fetcher(client, "https://p.supabase.co", "b", {})

    with pytest.raises(cutover_checks.StorageListingFailed) as raised:
        cutover_checks.collect_objects(fetch)
    assert "401" in str(raised.value)


def test_a_non_list_body_raises_instead_of_reporting_an_empty_bucket():
    client = _StubClient(_Response(200, {"error": "rate limited"}))
    fetch = cutover_checks.storage_page_fetcher(client, "https://p.supabase.co", "b", {})

    with pytest.raises(cutover_checks.StorageListingFailed):
        cutover_checks.collect_objects(fetch)


def test_an_error_can_never_be_mistaken_for_no_mutation():
    """The exact scenario: before and after both fail, and the proof must NOT come out equal."""
    def inventory(client):
        fetch = cutover_checks.storage_page_fetcher(client, "https://p.supabase.co", "b", {})
        return cutover_checks.manifest_digest(cutover_checks.collect_objects(fetch))

    working = _StubClient(_Response(200, [_object("gallery/ab/one.png")]), _Response(200, []))
    before = inventory(working)
    assert before[1] == 1

    broken = _StubClient(_Response(401, {"message": "Invalid JWT"}))
    with pytest.raises(cutover_checks.StorageListingFailed):
        inventory(broken)
    # Before this fetcher existed, `inventory(broken)` returned the digest of zero objects — and
    # comparing that to itself across a probe read as "the gallery was untouched".


def test_an_empty_bucket_is_still_reported_as_empty():
    """The fix must not turn a genuinely empty bucket into an error."""
    client = _StubClient(_Response(200, []))
    fetch = cutover_checks.storage_page_fetcher(client, "https://p.supabase.co", "b", {})

    digest, count = cutover_checks.manifest_digest(cutover_checks.collect_objects(fetch))
    assert count == 0 and digest


def test_the_collector_recurses_into_folder_rows_and_keeps_only_objects():
    client = _StubClient(
        _Response(200, [_folder("gallery")]),
        _Response(200, [_folder("ab")]),
        _Response(200, [_object("one.png", 11), _object("two.png", 22)]),
    )
    fetch = cutover_checks.storage_page_fetcher(client, "https://p.supabase.co", "b", {})

    objects = cutover_checks.collect_objects(fetch)
    assert [o["name"] for o in objects] == ["gallery/ab/one.png", "gallery/ab/two.png"], (
        "a single-level listing would have returned one FOLDER and no object")
    assert cutover_checks.manifest_digest(objects)[1] == 2
