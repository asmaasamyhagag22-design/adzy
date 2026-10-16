# -*- coding: utf-8 -*-
"""Phase 2E unit/security proof.  Every write targets pytest-owned temporary roots."""
from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from competitor.registry import registry_path
from db.migration_tools.assets import safe_target_path, stage_assets, verify_source_binding
from db.migration_tools.inventory import (
    ACTIVE,
    CONFLICTING_OWNER,
    HISTORY_REFERENCED,
    ORPHAN_UNREFERENCED,
    build_source_manifest,
)
from db.migration_tools.plan import build_migration_plan, observed_mime
from db.migration_tools.records import (
    AssetCollision,
    AssetStageError,
    PlanValidationError,
    SourceChanged,
    calculate_plan_digest,
    validate_plan,
    write_json_atomic,
)

_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42Y"
    "AAAAASUVORK5CYII="
)
_USER = "0123456789abcdef"
_OTHER = "fedcba9876543210"
_PASSWORD_HASH = "pbkdf2_sha256$200000$not-a-real-salt$not-a-real-derived-key"
_AUTH_SECRET = "test-auth-secret-that-must-never-be-printed"


def _write(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def _brand(*, owner: str | None = _USER, calendar: list | None = None) -> dict:
    return {
        "domain_key": "example_com", "source_url": "https://example.com/",
        "organization_name": "Example", "owner_id": owner,
        "created_at": 1700000000, "updated_at": 1700000010,
        "_profile": {
            "schema_version": "0.2.0", "source_url": "https://example.com/",
            "locations": [{"country": "Qatar"}], "languages": [{"code": "en"}],
            "extraction_meta": {"extracted_at": "2024-01-01T00:00:00Z"},
        },
        "has_swot": True, "swot_approved": True,
        "swot": {"strengths": [], "weaknesses": [], "opportunities": [], "threats": []},
        "has_strategy": True,
        "marketing_strategy": {"strategic_posture": "focus", "goals": []},
        "has_plan": True, "marketing_plan": {"objective": "test"},
        "content_calendar": {"calendar": calendar or [
            {"date": "2024-02-01", "platform": "web", "topic": "A"},
            {"date": "2024-02-01", "platform": "email", "topic": "B"},
        ]},
        "owner_inputs": {"constraints": ["truth"]},
        "ai_influencer": {"has_portrait": False, "has_poster": False},
    }


def _source(tmp_path: Path, *, history: list | None = None, second_user: bool = False) -> Path:
    root = tmp_path / "source"
    webapp = root / "webapp"
    users = {
        "owner@example.test": {
            "id": _USER, "email": "owner@example.test", "full_name": "Owner",
            "password_hash": _PASSWORD_HASH, "created_at": 1699999900,
        }
    }
    if second_user:
        users["other@example.test"] = {
            "id": _OTHER, "email": "other@example.test", "full_name": "",
            "password_hash": _PASSWORD_HASH + "-other", "created_at": 1699999950,
        }
    _write(webapp / "users.json", users)
    _write(webapp / "history.json", history or [])
    _write(webapp / "brands" / "example_com.json", _brand())
    (webapp / "generated").mkdir(parents=True)
    (webapp / ".auth_secret").write_text(_AUTH_SECRET, encoding="utf-8")
    reg_path = registry_path("https://example.com/", store_dir=str(root / "registry"))
    _write(reg_path, {
        "brand_ref": "https://example.com/",
        "entries": [{"key": "rival.test", "name": "Rival", "website": "https://rival.test",
                     "source": "user", "added_at": "2024-01-02T00:00:00+00:00"}],
    })
    return root


def _history_item(index: int, payload: str | None = None, **extra) -> dict:
    row = {
        "domain_key": "example_com", "owner_id": _USER,
        "filename": f"example_{index}.png", "content_type": "social_post",
        "created_at": 1700000100 + index,
    }
    if payload is not None:
        row["image_b64"] = payload
    row.update(extra)
    return row


def test_inventory_uses_brandstore_authority_not_mtime_size_or_candidate_count(tmp_path):
    root = _source(tmp_path, second_user=True)
    conflict = _brand(owner=_OTHER)
    conflict["ai_influencer"] = {
        "has_portrait": True,
        "candidates": {str(i): {"url": f"/api/generated/conflict-{i}.png"}
                       for i in range(20)},
        "padding": "x" * 10000,
    }
    backup = root / "webapp" / "brands" / "example_com.owner-fedcba.bak.json"
    _write(backup, conflict)
    os.utime(backup, (2_000_000_000, 2_000_000_000))
    manifest = build_source_manifest(root)
    rows = {row["path"]: row for row in manifest["files"]}
    assert rows["webapp/brands/example_com.json"]["classification"] == ACTIVE
    assert rows[f"webapp/brands/{backup.name}"]["classification"] == CONFLICTING_OWNER
    plan, _source_manifest, _quarantine = build_migration_plan(root)
    assert len(plan["brands"]) == 1 and plan["brands"][0]["owner_legacy_id"] == _USER
    assert all("conflict-" not in json.dumps(row) for row in plan["assets"])
    assert [q["reason"] for q in plan["quarantine"]].count(
        "CONFLICTING_OWNER_NONCANONICAL_BRAND") == 1


def test_plan_is_deterministic_small_and_contains_no_raw_secrets_or_media(tmp_path):
    encoded = base64.b64encode(_PNG).decode("ascii")
    root = _source(tmp_path, history=[_history_item(0, encoded)])
    first, source_a, quarantine_a = build_migration_plan(root)
    second, source_b, quarantine_b = build_migration_plan(root)
    assert first == second and source_a == source_b and quarantine_a == quarantine_b
    assert first["plan_digest"] == calculate_plan_digest(first)
    serialised = json.dumps(first, ensure_ascii=False)
    assert len(serialised) < 1_000_000
    for secret in (_PASSWORD_HASH, _AUTH_SECRET, encoded):
        assert secret not in serialised
    assert str(root.resolve()) not in serialised
    assert first["profiles"][0]["language_source"].startswith("migration_derived_")
    assert first["profiles"][0]["country_source"] == "location_country"
    for key in ("users", "brands", "profiles", "runs", "calendar_posts", "assets",
                "approvals", "owner_inputs", "competitors"):
        assert all(row.get("migration_classification") and row.get("provenance")
                   and row.get("source_locator") for row in first[key])


def test_null_empty_and_missing_are_not_collapsed_across_source_fields(tmp_path):
    root = _source(tmp_path)
    users_path = root / "webapp" / "users.json"
    users = json.loads(users_path.read_text(encoding="utf-8"))
    users["owner@example.test"]["full_name"] = ""
    _write(users_path, users)
    brand_path = root / "webapp" / "brands" / "example_com.json"
    brand = json.loads(brand_path.read_text(encoding="utf-8"))
    brand["organization_name"] = ""
    brand["owner_inputs"] = {}
    _write(brand_path, brand)
    plan, _, _ = build_migration_plan(root)
    assert plan["users"][0]["full_name"] == ""
    assert plan["brands"][0]["display_name"] == ""
    assert plan["owner_inputs"][0]["payload"] == {}
    del brand["owner_inputs"]
    _write(brand_path, brand)
    plan_without, _, _ = build_migration_plan(root)
    assert plan_without["owner_inputs"] == []


def test_history_representation_validation_quarantines_without_guessing(tmp_path):
    valid = base64.b64encode(_PNG).decode("ascii")
    rows = [
        _history_item(0, valid),
        _history_item(1, valid),
        _history_item(2, "%%%not-base64%%%"),
        _history_item(3, base64.b64encode(_PNG[:16]).decode("ascii")),
        _history_item(4, ""),
        _history_item(5, "data:image/jpeg;base64," + valid),
        _history_item(6, None, image_url="https://cdn.example.test/image.png"),
        _history_item(7, None, image_url="/api/generated/missing.png"),
    ]
    root = _source(tmp_path, history=rows)
    plan, manifest, _ = build_migration_plan(root)
    assert len(plan["history"]) == 8
    assert len([a for a in plan["assets"] if a.get("history_index") is not None]) == 2
    reasons = [row["reason"] for row in plan["quarantine"]]
    assert reasons.count("INLINE_ASSET_DECODE_FAILED") == 2
    assert reasons.count("REFERENCED_ASSET_BYTES_MISSING") == 1
    assert reasons.count("UNREPRESENTABLE_SOURCE_FACT") == 3
    assert plan["history"][6]["representation"] == "external_url"
    assert manifest["generated_references"]["missing"][0]["name"] == "missing.png"


def test_local_history_file_is_checked_and_becomes_a_managed_asset(tmp_path):
    root = _source(tmp_path, history=[
        _history_item(0, None, image_url="/api/generated/from-history.png")])
    path = root / "webapp" / "generated" / "from-history.png"
    path.write_bytes(_PNG)
    manifest = build_source_manifest(root)
    row = next(r for r in manifest["files"] if r["path"].endswith("from-history.png"))
    assert row["classification"] == HISTORY_REFERENCED
    plan, _, _ = build_migration_plan(root)
    asset = next(a for a in plan["assets"] if a.get("history_index") == 0)
    assert asset["source_representation"] == "local_file"
    assert asset["bytes"] == len(_PNG) and asset["observed_mime"] == "image/png"


def test_orphan_generated_file_is_accounted_but_never_imported(tmp_path):
    root = _source(tmp_path)
    orphan = root / "webapp" / "generated" / "orphan.png"
    orphan.write_bytes(_PNG)
    plan, manifest, _ = build_migration_plan(root)
    row = next(r for r in manifest["files"] if r["path"].endswith("orphan.png"))
    assert row["classification"] == ORPHAN_UNREFERENCED
    assert not plan["assets"]
    assert any(r["path"].endswith("orphan.png") for r in plan["ignored_files"])


def test_nested_generated_same_basename_cannot_satisfy_a_root_reference(tmp_path):
    root = _source(tmp_path)
    brand_path = root / "webapp" / "brands" / "example_com.json"
    brand = json.loads(brand_path.read_text(encoding="utf-8"))
    brand["ai_influencer"] = {
        "has_portrait": True, "has_poster": False,
        "portrait_url": "/api/generated/shared.png",
    }
    _write(brand_path, brand)
    nested = root / "webapp" / "generated" / "nested" / "shared.png"
    nested.parent.mkdir(parents=True)
    nested.write_bytes(_PNG)
    plan, manifest, _ = build_migration_plan(root)
    row = next(r for r in manifest["files"] if r["path"].endswith("nested/shared.png"))
    assert row["classification"] == ORPHAN_UNREFERENCED
    assert manifest["generated_references"]["missing"][0]["name"] == "shared.png"
    assert not plan["assets"]
    assert "REFERENCED_ASSET_BYTES_MISSING" in {q["reason"] for q in plan["quarantine"]}


def test_multiple_current_poster_outputs_are_hard_quarantined_not_sequenced(tmp_path):
    calendar = [
        {"date": "2024-02-01", "poster": {"url": "/api/generated/one.png"}},
        {"date": "2024-02-02", "poster": {"url": "/api/generated/two.png"}},
    ]
    root = _source(tmp_path)
    _write(root / "webapp" / "brands" / "example_com.json", _brand(calendar=calendar))
    for name in ("one.png", "two.png"):
        (root / "webapp" / "generated" / name).write_bytes(_PNG)
    plan, _, _ = build_migration_plan(root)
    assert not [run for run in plan["runs"] if run["kind"] == "poster"]
    rows = [q for q in plan["quarantine"]
            if q["reason"] == "AMBIGUOUS_LEGACY_CURRENT_RUN"]
    assert len(rows) == 2
    assert all(row["cutover_can_continue_after_ack"] is False for row in rows)
    assert plan["reconciliation"]["output_blocks"] == {
        "source": 5, "migrated": 3, "quarantined": 2,
        "non_authoritative": 0, "ignored_by_policy": 0,
    }


def test_archived_unclaimed_brand_requires_product_decision_and_no_owner_guess(tmp_path):
    root = _source(tmp_path)
    brand = _brand(owner=None)
    brand["archived"] = True
    _write(root / "webapp" / "brands" / "example_com.json", brand)
    plan, _, _ = build_migration_plan(root)
    assert plan["brands"] == []
    row = next(q for q in plan["quarantine"]
               if q["reason"] == "CUTOVER_PRODUCT_DECISION_REQUIRED_ARCHIVED_UNCLAIMED")
    assert row["cutover_can_continue_after_ack"] is False
    assert plan["reconciliation"]["brand_files"] == {
        "source": 1, "migrated": 0, "quarantined": 1,
        "non_authoritative": 0, "ignored_by_policy": 0,
    }


def test_stage_is_restartable_and_keeps_equal_bytes_as_distinct_logical_events(tmp_path):
    encoded = base64.b64encode(_PNG).decode("ascii")
    root = _source(tmp_path, history=[_history_item(0, encoded), _history_item(1, encoded)])
    plan, _, _ = build_migration_plan(root)
    target = tmp_path / "staged"
    with pytest.raises(AssetStageError, match="INJECTED_ASSET_STAGE_FAILURE"):
        stage_assets(plan, source_root=root, target_root=target, inject_failure_after=1)
    assert len(list(target.rglob("*.png"))) == 1
    manifest, stats = stage_assets(plan, source_root=root, target_root=target)
    assert stats == {"staged": 1, "verified_existing": 1}
    assert manifest["logical_asset_count"] == manifest["physical_blob_count"] == 2
    assert len({row["logical_id"] for row in manifest["entries"]}) == 2


def test_destination_collision_never_overwrites_and_equal_bytes_reuse(tmp_path):
    encoded = base64.b64encode(_PNG).decode("ascii")
    root = _source(tmp_path, history=[_history_item(0, encoded)])
    plan, _, _ = build_migration_plan(root)
    target = tmp_path / "staged"
    asset = plan["assets"][0]
    destination = safe_target_path(target, asset["storage_key"])
    destination.parent.mkdir(parents=True)
    destination.write_bytes(b"wrong")
    with pytest.raises(AssetCollision, match="ASSET_STORAGE_KEY_COLLISION"):
        stage_assets(plan, source_root=root, target_root=target)
    assert destination.read_bytes() == b"wrong"
    destination.write_bytes(_PNG)
    _manifest, stats = stage_assets(plan, source_root=root, target_root=target)
    assert stats == {"staged": 0, "verified_existing": 1}


@pytest.mark.parametrize("key", [
    "../escape.png", "/absolute.png", "C:/drive.png", r"C:\drive.png",
    r"legacy\..\escape.png", "legacy/a/../../escape.png",
])
def test_storage_key_path_traversal_is_rejected(tmp_path, key):
    with pytest.raises(AssetStageError):
        safe_target_path(tmp_path / "target", key)


def test_source_change_is_bound_to_plan_and_restore_recovers(tmp_path):
    root = _source(tmp_path)
    plan, _, _ = build_migration_plan(root)
    users = root / "webapp" / "users.json"
    original = users.read_bytes()
    users.write_bytes(original + b"\n")
    with pytest.raises(SourceChanged, match="SOURCE_CHANGED_AFTER_PLAN"):
        verify_source_binding(plan, root)
    users.write_bytes(original)
    assert verify_source_binding(plan, root)["source_manifest_digest"] == plan[
        "source_manifest_digest"]


def test_corrupt_or_incompatible_plan_is_refused(tmp_path):
    root = _source(tmp_path)
    plan, _, _ = build_migration_plan(root)
    corrupt = json.loads(json.dumps(plan))
    corrupt["brands"][0]["domain_key"] = "tampered"
    with pytest.raises(PlanValidationError, match="CORRUPTED_PLAN_DIGEST"):
        validate_plan(corrupt)
    incompatible = json.loads(json.dumps(plan))
    incompatible["format_version"] = "future/v99"
    with pytest.raises(PlanValidationError, match="INCOMPATIBLE_PLAN_FORMAT"):
        validate_plan(incompatible)
    forged_ack = json.loads(json.dumps(plan))
    forged_ack["quarantine_manifest_digest"] = "0" * 64
    with pytest.raises(PlanValidationError, match="CORRUPTED_QUARANTINE_DIGEST"):
        validate_plan(forged_ack)


def test_png_recognition_rejects_truncation():
    assert observed_mime(_PNG) == "image/png"
    assert observed_mime(_PNG[:16]) is None
    assert observed_mime(b"") is None


def test_calendar_identity_is_ordinal_not_date_and_same_date_survives(tmp_path):
    root = _source(tmp_path)
    plan, _, _ = build_migration_plan(root)
    posts = plan["calendar_posts"]
    assert [row["ordinal"] for row in posts] == [0, 1]
    assert [row["scheduled_date"] for row in posts] == ["2024-02-01", "2024-02-01"]
    assert len({row["id"] for row in posts}) == 2


def test_cli_plan_is_noninteractive_and_logs_neither_secrets_nor_inline_bytes(tmp_path):
    encoded = base64.b64encode(_PNG).decode("ascii")
    root = _source(tmp_path, history=[_history_item(0, encoded)])
    output = tmp_path / "artifacts"
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1", "PYTHONIOENCODING": "utf-8"}
    proc = subprocess.run(
        [sys.executable, "-m", "db.migration_tools.cli", "PLAN",
         "--source-root", str(root), "--output-dir", str(output)],
        cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True,
        timeout=60, env=env,
    )
    assert proc.returncode == 0, proc.stderr
    combined = proc.stdout + proc.stderr
    artifacts = "".join(path.read_text(encoding="utf-8") for path in output.glob("*.json"))
    for secret in (_PASSWORD_HASH, _AUTH_SECRET, encoded):
        assert secret not in combined
        assert secret not in artifacts
    assert "plan_digest" in proc.stdout


def test_normal_api_source_never_imports_migration_tooling():
    api_root = Path(__file__).resolve().parents[1] / "api"
    leaking = []
    for path in api_root.rglob("*.py"):
        source = path.read_text(encoding="utf-8")
        if "db.migration_tools" in source:
            leaking.append(path.relative_to(api_root).as_posix())
    assert leaking == []


def test_plan_and_artifact_writes_never_use_raw_untrusted_filename(tmp_path):
    root = _source(tmp_path, history=[
        _history_item(0, base64.b64encode(_PNG).decode("ascii"), filename="../escape.png")])
    plan, _, _ = build_migration_plan(root)
    asset = plan["assets"][0]
    assert "escape" not in asset["storage_key"]
    destination = safe_target_path(tmp_path / "target", asset["storage_key"])
    destination.relative_to((tmp_path / "target").resolve())


def test_json_artifact_writer_is_atomic_and_round_trips(tmp_path):
    path = tmp_path / "artifact.json"
    write_json_atomic(path, {"b": 2, "a": 1})
    assert json.loads(path.read_text(encoding="utf-8")) == {"a": 1, "b": 2}
    assert not list(tmp_path.glob("*.tmp"))
