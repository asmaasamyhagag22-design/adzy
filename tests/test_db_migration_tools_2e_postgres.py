# -*- coding: utf-8 -*-
"""Phase 2E against a byte-exact source copy and real disposable PostgreSQL."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID, uuid4

import pytest

sqlalchemy = pytest.importorskip("sqlalchemy", reason="SQLAlchemy is required for PostgreSQL proof")
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.orm import Session

from db.migration_tools.apply import apply_plan, inspect_target
from db.migration_tools.assets import safe_target_path, stage_assets
from db.migration_tools.inventory import SourceLayout, build_source_manifest
from db.migration_tools.plan import build_migration_plan
from db.migration_tools.records import EXPECTED_SCHEMA_HEAD
from db.migration_tools.records import (
    AssetCollision,
    AssetStageError,
    MigrationError,
    SourceChanged,
    TargetConflict,
    TargetSafetyError,
    calculate_plan_digest,
    quarantine_manifest_from_plan,
)
from db.migration_tools.verify import verify_database
from db.models import Approval, Asset, Attempt, Brand, BrandOwnerInputs, ProfileSnapshot, Run, User
from db.test_database_safety import require_disposable_test_database

_ROOT = Path(__file__).resolve().parents[1]
_DSN = os.environ.get("BASEERA_TEST_DATABASE_URL", "")

pytestmark = pytest.mark.skipif(
    not _DSN, reason="BASEERA_TEST_DATABASE_URL is required for real PostgreSQL migration proof")

_BUSINESS_TABLES = (
    "approvals", "usage_records", "jobs", "run_dependencies", "attempts", "assets",
    "calendar_posts", "runs", "profile_snapshots", "brand_competitors", "brand_owner_inputs",
    "brands", "users",
)


def _guarded_dsn() -> str:
    require_disposable_test_database(_DSN)
    return _DSN


@pytest.fixture(scope="module")
def source_snapshot(tmp_path_factory) -> Path:
    """Copy every inventoried source byte with copy2 and verify content + file metadata."""
    root = tmp_path_factory.mktemp("phase2e-byte-exact-source")
    shutil.copytree(_ROOT / "outputs" / "webapp", root / "webapp", copy_function=shutil.copy2)
    shutil.copytree(_ROOT / "registry", root / "registry", copy_function=shutil.copy2)
    original_manifest = build_source_manifest(_ROOT)
    copied_manifest = build_source_manifest(root)
    assert copied_manifest == original_manifest
    original_layout = SourceLayout.resolve(_ROOT)
    copied_layout = SourceLayout.resolve(root)
    for row in original_manifest["files"]:
        label = row["path"]
        if label.startswith("webapp/"):
            original = original_layout.webapp / label.removeprefix("webapp/")
            copied = copied_layout.webapp / label.removeprefix("webapp/")
        else:
            original = original_layout.registry / label.removeprefix("registry/")
            copied = copied_layout.registry / label.removeprefix("registry/")
        assert copied.stat().st_size == original.stat().st_size
        assert copied.stat().st_mtime_ns == original.stat().st_mtime_ns
    return root


@pytest.fixture(scope="module")
def real_plan(source_snapshot):
    first = build_migration_plan(source_snapshot)
    second = build_migration_plan(source_snapshot)
    assert first == second
    plan, source_manifest, quarantine = first
    assert {key: len(plan[key]) for key in (
        "users", "brands", "profiles", "runs", "calendar_posts", "attempts", "assets",
        "approvals", "owner_inputs", "competitors", "history", "quarantine",
    )} == {
        "users": 2, "brands": 7, "profiles": 7, "runs": 15, "calendar_posts": 27,
        "attempts": 2, "assets": 16, "approvals": 3, "owner_inputs": 1,
        "competitors": 20, "history": 28, "quarantine": 17,
    }
    # 208, not 209. The higher number was frozen while the live `generated/` directory still held
    # `b_com_influencer_poster_119ea2b19f.png` — a file a TEST wrote into the authoritative archive,
    # because `webapp_dir()` falls back to `outputs/webapp` whenever `BASEERA_WEBAPP_DIR` is unset.
    # DB2B.5 closed that hole (autouse isolation in tests/conftest.py) and quarantined the leaked
    # file, restoring the archive to the 215 files / 208 orphans the DB2B census independently
    # measured. This constant tracked contamination, so it moved with the cleanup.
    assert source_manifest["counts_by_classification"]["ORPHAN_UNREFERENCED"] == 208
    assert len([h for h in plan["history"] if h["classification"] == "QUARANTINED"]) == 16
    assert [q["reason"] for q in plan["quarantine"]].count(
        "CONFLICTING_OWNER_NONCANONICAL_BRAND") == 1
    for key in ("users", "brands", "profiles", "runs", "calendar_posts", "attempts",
                "assets", "approvals", "owner_inputs", "competitors"):
        assert plan[key], key
        assert all(row.get("migration_classification") and row.get("provenance")
                   and row.get("source_locator") for row in plan[key])
    return plan, source_manifest, quarantine


@pytest.fixture(scope="module")
def staged_root(tmp_path_factory, source_snapshot, real_plan):
    target = tmp_path_factory.mktemp("phase2e-staged-assets")
    manifest, stats = stage_assets(real_plan[0], source_root=source_snapshot, target_root=target)
    assert stats == {"staged": 16, "verified_existing": 0}
    assert manifest["logical_asset_count"] == 16
    assert manifest["byte_sum"] == 45_133_808
    return target


@pytest.fixture(scope="module")
def engine():
    from alembic import command
    from alembic.config import Config

    engine = create_engine(_guarded_dsn(), future=True)
    with engine.connect() as connection:
        has_head = connection.execute(text(
            "SELECT to_regclass('alembic_version') IS NOT NULL")).scalar_one()
    if not has_head:
        old_dsn = os.environ.get("BASEERA_DATABASE_URL")
        os.environ["BASEERA_DATABASE_URL"] = _guarded_dsn()
        try:
            command.upgrade(Config(str(_ROOT / "alembic.ini")), "head")
        finally:
            if old_dsn is None:
                os.environ.pop("BASEERA_DATABASE_URL", None)
            else:
                os.environ["BASEERA_DATABASE_URL"] = old_dsn
    assert inspect_target(engine)["schema_head"] == EXPECTED_SCHEMA_HEAD
    yield engine
    engine.dispose()


def _truncate(engine) -> None:
    with engine.begin() as connection:
        connection.exec_driver_sql("TRUNCATE TABLE " + ", ".join(_BUSINESS_TABLES) + " CASCADE")


@pytest.fixture(autouse=True)
def clean_disposable_database(engine):
    _truncate(engine)
    yield
    _truncate(engine)


def _apply(plan, source_snapshot, staged_root, **kwargs):
    return apply_plan(
        plan, source_root=source_snapshot, asset_root=staged_root, dsn=_guarded_dsn(),
        quarantine_ack_digest=plan["quarantine_manifest_digest"], **kwargs)


def _import_execution_times(engine) -> dict[str, dict[str, str | None]]:
    with Session(engine) as session:
        return {
            "profiles": {str(row.id): row.imported_at.isoformat()
                         for row in session.scalars(select(ProfileSnapshot))},
            "runs": {str(row.id): row.imported_at.isoformat()
                     for row in session.scalars(select(Run))},
            "approvals": {str(row.id): row.imported_at.isoformat()
                          for row in session.scalars(select(Approval))},
            "assets": {str(row.id): row.created_at.isoformat()
                       for row in session.scalars(select(Asset))},
            "attempts": {str(row.id): row.created_at.isoformat()
                         for row in session.scalars(select(Attempt))},
            "owner_inputs": {str(row.brand_id): row.updated_at.isoformat()
                             for row in session.scalars(select(BrandOwnerInputs))},
        }


def test_first_apply_second_apply_and_postgres_only_verify_are_exact_and_idempotent(
    engine, source_snapshot, real_plan, staged_root,
):
    plan = real_plan[0]
    first = _apply(plan, source_snapshot, staged_root)
    assert first["inserted_total"] == 100
    assert first["verified_existing_total"] == 0
    timestamps = _import_execution_times(engine)
    second = _apply(plan, source_snapshot, staged_root)
    assert second["inserted_total"] == 0
    assert second["verified_existing_total"] == 100
    assert _import_execution_times(engine) == timestamps
    report = verify_database(plan, asset_root=staged_root, dsn=_guarded_dsn())
    assert set(report["referential_zero_checks"].values()) == {0}
    assert all(report["db_only_read_parity"].values())
    assert set(report["unexplained_items"].values()) == {0}
    assert report["target"]["unvalidated_constraints"] == []
    assert report["target"]["deferrable_required_constraints"] == []


def test_quarantine_digest_is_required_before_any_database_mutation(
    engine, source_snapshot, real_plan, staged_root,
):
    plan = real_plan[0]
    with pytest.raises(TargetSafetyError, match="QUARANTINE_DIGEST_ACKNOWLEDGEMENT_REQUIRED"):
        apply_plan(
            plan, source_root=source_snapshot, asset_root=staged_root, dsn=_guarded_dsn(),
            quarantine_ack_digest=None)
    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(User)) == 0


def test_hard_quarantine_refuses_even_with_exact_ack_before_database_mutation(
    engine, source_snapshot, real_plan, staged_root,
):
    plan = json.loads(json.dumps(real_plan[0]))
    plan["quarantine"][0]["cutover_can_continue_after_ack"] = False
    plan["plan_digest"] = calculate_plan_digest(plan)
    plan["quarantine_manifest_digest"] = quarantine_manifest_from_plan(plan)[
        "quarantine_manifest_digest"]
    with pytest.raises(TargetSafetyError, match="HARD_QUARANTINE_BLOCKS_APPLY"):
        _apply(plan, source_snapshot, staged_root)
    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(User)) == 0


def test_apply_refuses_missing_or_wrong_asset_bytes_before_database_mutation(
    engine, source_snapshot, real_plan, tmp_path,
):
    plan = real_plan[0]
    empty = tmp_path / "missing-assets"
    empty.mkdir()
    with pytest.raises(AssetStageError, match="ASSET_BYTES_NOT_STAGED"):
        apply_plan(
            plan, source_root=source_snapshot, asset_root=empty, dsn=_guarded_dsn(),
            quarantine_ack_digest=plan["quarantine_manifest_digest"])
    wrong = tmp_path / "wrong-assets"
    destination = safe_target_path(wrong, plan["assets"][0]["storage_key"])
    destination.parent.mkdir(parents=True)
    destination.write_bytes(b"wrong")
    with pytest.raises(AssetCollision, match="ASSET_STORAGE_KEY_COLLISION"):
        apply_plan(
            plan, source_root=source_snapshot, asset_root=wrong, dsn=_guarded_dsn(),
            quarantine_ack_digest=plan["quarantine_manifest_digest"])
    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(User)) == 0


def test_database_halfway_failure_rolls_back_and_restart_succeeds(
    engine, source_snapshot, real_plan, staged_root,
):
    plan = real_plan[0]
    with pytest.raises(MigrationError, match="INJECTED_DATABASE_APPLY_FAILURE"):
        _apply(plan, source_snapshot, staged_root, inject_failure_after=25)
    with Session(engine) as session:
        for model in (User, Brand, ProfileSnapshot, Run, Asset, Attempt, Approval):
            assert session.scalar(select(func.count()).select_from(model)) == 0
    result = _apply(plan, source_snapshot, staged_root)
    assert result["inserted_total"] == 100


def test_source_byte_change_refuses_before_database_mutation_then_restore_succeeds(
    engine, source_snapshot, real_plan, staged_root,
):
    plan = real_plan[0]
    users = source_snapshot / "webapp" / "users.json"
    original = users.read_bytes()
    try:
        users.write_bytes(original + b"\n")
        with pytest.raises(SourceChanged, match="SOURCE_CHANGED_AFTER_PLAN"):
            _apply(plan, source_snapshot, staged_root)
        with Session(engine) as session:
            assert session.scalar(select(func.count()).select_from(User)) == 0
    finally:
        users.write_bytes(original)
    assert _apply(plan, source_snapshot, staged_root)["inserted_total"] == 100


def test_same_deterministic_uuid_with_conflicting_payload_stops_without_overwrite(
    engine, source_snapshot, real_plan, staged_root,
):
    plan = real_plan[0]
    expected = plan["users"][0]
    with Session(engine) as session, session.begin():
        session.add(User(
            id=UUID(expected["id"]), email="conflict@example.test", hashed_password="x",
            legacy_user_id=expected["legacy_user_id"], created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        ))
    with pytest.raises(TargetConflict, match="TARGET_IDENTITY_CONFLICT:users"):
        _apply(plan, source_snapshot, staged_root)
    with Session(engine) as session:
        assert session.get(User, UUID(expected["id"])).email == "conflict@example.test"
        assert session.scalar(select(func.count()).select_from(Brand)) == 0


def test_unrelated_nonempty_target_database_is_refused(
    engine, source_snapshot, real_plan, staged_root,
):
    with Session(engine) as session, session.begin():
        session.add(User(
            id=uuid4(), email="unrelated@example.test", hashed_password="x",
            created_at=datetime.now(timezone.utc), updated_at=datetime.now(timezone.utc)))
    with pytest.raises(TargetSafetyError, match="TARGET_CONTAINS_UNRELATED_USERS"):
        _apply(real_plan[0], source_snapshot, staged_root)


def test_wrong_database_migration_head_is_refused_and_restored(engine):
    with engine.begin() as connection:
        connection.execute(text("UPDATE alembic_version SET version_num='0001_initial_schema'"))
    try:
        with pytest.raises(TargetSafetyError, match="TARGET_SCHEMA_HEAD_MISMATCH"):
            inspect_target(engine)
    finally:
        with engine.begin() as connection:
            connection.execute(text("UPDATE alembic_version SET version_num=:head"),
                               {"head": EXPECTED_SCHEMA_HEAD})
    assert inspect_target(engine)["schema_head"] == EXPECTED_SCHEMA_HEAD


def test_imported_approval_is_bound_to_old_swot_not_a_new_live_swot(
    engine, source_snapshot, real_plan, staged_root,
):
    plan = real_plan[0]
    _apply(plan, source_snapshot, staged_root)
    approved = next(row for row in plan["approvals"]
                    if next(b for b in plan["brands"] if b["id"] == row["brand_id"])["owner_id"])
    owner_id = next(b["owner_id"] for b in plan["brands"] if b["id"] == approved["brand_id"])
    new_id = uuid4()
    with Session(engine) as session:
        transaction = session.begin()
        session.add(Run(
            id=new_id, brand_id=UUID(approved["brand_id"]), kind="swot", status="succeeded",
            requested_config={}, effective_config=None, config_schema_version=1,
            output={"new": True}, output_schema_version=1, kind_seq=1, origin="live",
            lineage_complete=True, created_by_user_id=UUID(owner_id),
            created_at=datetime.now(timezone.utc), imported_at=None,
        ))
        session.flush()
        assert session.scalar(select(func.count()).select_from(Approval).where(
            Approval.subject_run_id == new_id)) == 0
        current = session.scalar(select(Run).where(
            Run.brand_id == UUID(approved["brand_id"]), Run.kind == "swot",
            Run.status == "succeeded").order_by(Run.kind_seq.desc().nullslast(), Run.id))
        assert current.id == new_id
        transaction.rollback()


def test_true_cli_end_to_end_emits_all_safe_artifacts(
    engine, source_snapshot, tmp_path, real_plan,
):
    output = tmp_path / "cli-artifacts"
    assets = tmp_path / "cli-assets"
    plan_path = output / "migration_plan.json"
    env = {**os.environ, "BASEERA_TEST_DATABASE_URL": _guarded_dsn(),
           "PYTHONDONTWRITEBYTECODE": "1", "PYTHONIOENCODING": "utf-8"}

    def invoke(*args: str) -> subprocess.CompletedProcess:
        proc = subprocess.run(
            [sys.executable, "-m", "db.migration_tools.cli", *args], cwd=_ROOT,
            capture_output=True, text=True, timeout=180, env=env)
        assert proc.returncode == 0, proc.stderr
        return proc

    logs = ""
    logs += invoke("PLAN", "--source-root", str(source_snapshot),
                   "--output-dir", str(output)).stdout
    written_plan = json.loads(plan_path.read_text(encoding="utf-8"))
    assert written_plan["plan_digest"] == real_plan[0]["plan_digest"]
    logs += invoke("STAGE-ASSETS", "--source-root", str(source_snapshot), "--plan", str(plan_path),
                   "--target-asset-root", str(assets), "--output-dir", str(output)).stdout
    ack = written_plan["quarantine_manifest_digest"]
    common = ("--source-root", str(source_snapshot), "--plan", str(plan_path),
              "--target-asset-root", str(assets), "--output-dir", str(output),
              "--dsn-env", "BASEERA_TEST_DATABASE_URL", "--ack-quarantine-digest", ack)
    logs += invoke("APPLY", *common).stdout
    logs += invoke("APPLY", *common).stdout
    logs += invoke("VERIFY", "--plan", str(plan_path), "--target-asset-root", str(assets),
                   "--output-dir", str(output), "--dsn-env",
                   "BASEERA_TEST_DATABASE_URL").stdout
    expected = {
        "source_manifest.json", "migration_plan.json", "asset_manifest.json",
        "quarantine_manifest.json", "reconciliation.json", "execution_manifest.json",
    }
    assert expected <= {path.name for path in output.iterdir()}
    password = _guarded_dsn().split("@", 1)[0].rsplit(":", 1)[-1]
    source_users = json.loads((source_snapshot / "webapp" / "users.json").read_text("utf-8"))
    forbidden = [password] + [row["password_hash"] for row in source_users.values()]
    assert all(secret not in logs for secret in forbidden if secret)
