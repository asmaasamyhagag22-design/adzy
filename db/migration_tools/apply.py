"""Constraint-safe, one-transaction application of a reviewed migration plan."""
from __future__ import annotations

import hashlib
import sys as _sys
from collections import defaultdict
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable
from uuid import UUID

from sqlalchemy import create_engine, inspect, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from db.config import connect_timeout_seconds, safe_dsn
# `assets` and `inventory` are imported where they are USED, not here. Both reach the legacy
# source-corpus reader, which imports `competitor.registry`, whose package `__init__` pulls in the
# whole competitor domain — Places client, discovery, SWOT, TOWS. Importing this module at all
# therefore used to require that entire tree, including for callers that only want the target
# guard: MEASURED 2026-08-23, reading `_REQUIRED_CONSTRAINTS` from a standalone db bundle died with
# `ModuleNotFoundError: No module named 'competitor'`. Nothing about deciding whether a DSN may be
# written to depends on the competitor registry. Both helpers are used inside function bodies only,
# so the coupling was import-time and nothing else.
from db.migration_tools.records import (
    EXPECTED_SCHEMA_HEAD,
    MigrationError,
    TargetConflict,
    TargetSafetyError,
    normalise_datetime,
    parse_datetime,
    validate_plan,
)
from db.models import (
    Approval,
    Asset,
    Attempt,
    Brand,
    BrandCompetitor,
    BrandOwnerInputs,
    CalendarPost,
    Job,
    ProfileSnapshot,
    Run,
    RunDependency,
    UsageRecord,
    User,
)

_DISPOSABLE_NAMES = ("test", "tmp", "scratch", "ci", "disposable")
_REQUIRED_CONSTRAINTS = {
    "uq_users_legacy_user_id", "ck_users_legacy_user_id_is_16_hex",
    "uq_brands_domain_key", "fk_brands_current_profile",
    "uq_profile_snapshots_brand_id_version", "fk_runs_input_profile",
    "uq_runs_brand_id_kind_kind_seq", "fk_calendar_posts_calendar_run",
    "uq_calendar_posts_calendar_run_id_ordinal", "uq_assets_storage_key",
    "fk_attempts_asset", "uq_attempts_run_id_ordinal", "fk_approvals_subject_run",
    "ck_approvals_legacy_imports_have_no_fabricated_metadata",
}
_REQUIRED_INDEXES = {"uq_approvals_one_import_per_run"}


def _guarded_engine(dsn: str, *, production_target: str = ""):
    """The target guard. DENY is the default and stays the default.

    A database whose name does not look disposable is refused, because the overwhelmingly common
    case for this tool is a rehearsal and the overwhelmingly costly mistake is pointing it at
    something real. The owner-attended cutover needs a way through, so `production_target` is the
    one door — and it is deliberately a bad door to walk through by accident:

    * absent -> unchanged behaviour, refuse anything not named disposable;
    * present -> allowed ONLY when it EXACTLY equals the target's database name. Naming the wrong
      database is refused, so "I meant the other one" cannot pass. There is no wildcard, no
      substring match and no environment variable that enables this.

    The operator therefore has to write the production database name out in full, on the command
    line, in the same invocation. The DSN itself is never echoed — only the database name the
    operator already typed.
    """
    try:
        url = make_url(str(dsn or ""))
    except Exception as exc:  # noqa: BLE001 - the raw DSN must never enter the safe error
        raise TargetSafetyError("INVALID_POSTGRESQL_DSN") from exc
    if not url.drivername.startswith("postgresql"):
        raise TargetSafetyError("TARGET_IS_NOT_POSTGRESQL")
    database = str(url.database or "").lower()
    declared = str(production_target or "").strip()
    if declared:
        if declared.lower() != database:
            raise TargetSafetyError("PRODUCTION_TARGET_DOES_NOT_MATCH_THE_DSN_DATABASE")
        print(f"*** PRODUCTION TARGET CONFIRMED: {database} — this is not a rehearsal. ***",
              file=_sys.stderr, flush=True)
    elif not any(token in database for token in _DISPOSABLE_NAMES):
        raise TargetSafetyError("TARGET_DATABASE_IS_NOT_DECLARED_DISPOSABLE")
    try:
        # Bounded, for the same reason `db/session.py` is: libpq waits forever by default, so an
        # unreachable target would make APPLY and VERIFY hang instead of refusing. This one runs
        # during the owner-attended cutover against a REMOTE database, where a migration that
        # neither starts nor refuses is the worst answer the step can give.
        return create_engine(dsn, future=True, pool_pre_ping=True,
                             connect_args={"connect_timeout": connect_timeout_seconds()})
    except Exception as exc:  # pragma: no cover - create_engine is lazy for valid dialect strings
        raise TargetSafetyError(f"CANNOT_CREATE_TARGET_ENGINE:{safe_dsn(str(dsn))}") from exc


def inspect_target(engine) -> dict[str, Any]:
    try:
        with engine.connect() as connection:
            version = str(connection.execute(text("SELECT version()" )).scalar_one())
            head = connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
            constraint_rows = connection.execute(text(
                "SELECT conname, convalidated, condeferrable, condeferred FROM pg_constraint "
                "WHERE connamespace = current_schema()::regnamespace"
            )).all()
            indexes = set(connection.execute(text(
                "SELECT indexname FROM pg_indexes WHERE schemaname = current_schema()"
            )).scalars())
    except Exception as exc:  # noqa: BLE001 - redact connection details from driver errors
        raise TargetSafetyError("TARGET_DATABASE_INSPECTION_FAILED") from exc
    if "PostgreSQL" not in version:
        raise TargetSafetyError("TARGET_IS_NOT_REAL_POSTGRESQL")
    if head != EXPECTED_SCHEMA_HEAD:
        raise TargetSafetyError("TARGET_SCHEMA_HEAD_MISMATCH")
    constraints = {row[0] for row in constraint_rows}
    missing = sorted(_REQUIRED_CONSTRAINTS - constraints)
    if missing or not _REQUIRED_INDEXES <= indexes:
        raise TargetSafetyError("TARGET_REQUIRED_CONSTRAINTS_MISSING")
    disabled = sorted(row[0] for row in constraint_rows if not row[1])
    if disabled:
        raise TargetSafetyError("TARGET_HAS_UNVALIDATED_CONSTRAINTS")
    deferred = sorted(
        row[0] for row in constraint_rows
        if row[0] in _REQUIRED_CONSTRAINTS and (row[2] or row[3]))
    if deferred:
        raise TargetSafetyError("TARGET_HAS_DEFERRABLE_REQUIRED_CONSTRAINTS")
    return {
        "server_version": version,
        "schema_head": head,
        "required_constraints_present": len(_REQUIRED_CONSTRAINTS),
        "required_indexes_present": len(_REQUIRED_INDEXES),
        "unvalidated_constraints": disabled,
        "deferrable_required_constraints": deferred,
    }


def _planned_id_sets(plan: dict[str, Any]) -> dict[type, set[Any]]:
    return {
        User: {UUID(row["id"]) for row in plan["users"]},
        Brand: {UUID(row["id"]) for row in plan["brands"]},
        ProfileSnapshot: {UUID(row["id"]) for row in plan["profiles"]},
        Run: {UUID(row["id"]) for row in plan["runs"]},
        CalendarPost: {UUID(row["id"]) for row in plan["calendar_posts"]},
        Asset: {UUID(row["id"]) for row in plan["assets"]},
        Attempt: {UUID(row["id"]) for row in plan["attempts"]},
        Approval: {UUID(row["id"]) for row in plan["approvals"]},
        BrandCompetitor: {UUID(row["id"]) for row in plan["competitors"]},
        BrandOwnerInputs: {UUID(row["brand_id"]) for row in plan["owner_inputs"]},
    }


def assert_target_has_no_unrelated_data(session: Session, plan: dict[str, Any]) -> None:
    for model, planned in _planned_id_sets(plan).items():
        column = model.brand_id if model is BrandOwnerInputs else model.id
        existing = set(session.scalars(select(column)))
        if not existing <= planned:
            raise TargetSafetyError(f"TARGET_CONTAINS_UNRELATED_{model.__tablename__.upper()}")
    for model in (Job, UsageRecord, RunDependency):
        if session.scalar(select(model).limit(1)) is not None:
            raise TargetSafetyError(f"TARGET_CONTAINS_UNEXPECTED_{model.__tablename__.upper()}")


def _canonical(value: Any) -> Any:
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, datetime):
        return normalise_datetime(value)
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, dict):
        return {key: _canonical(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_canonical(item) for item in value]
    return value


def _ensure_row(
    session: Session,
    model: type,
    identity: UUID,
    values: dict[str, Any],
    compare_fields: tuple[str, ...],
    counters: dict[str, dict[str, int]],
    after_insert: Callable[[], None],
) -> Any:
    row = session.get(model, identity)
    entity = model.__tablename__
    if row is None:
        row = model(id=identity, **values)
        session.add(row)
        session.flush()
        counters[entity]["created"] += 1
        after_insert()
        return row
    for field in compare_fields:
        if _canonical(getattr(row, field)) != _canonical(values[field]):
            raise TargetConflict(f"TARGET_IDENTITY_CONFLICT:{entity}:{identity}:{field}")
    counters[entity]["verified_existing"] += 1
    return row


def _source_passwords(source_root: str | Path, plan: dict[str, Any]) -> dict[str, str]:
    import json

    from db.migration_tools.inventory import SourceLayout

    layout = SourceLayout.resolve(source_root)
    data = json.loads((layout.webapp / "users.json").read_text(encoding="utf-8"))
    by_legacy = {
        str(value.get("id") or ""): str(value.get("password_hash") or "")
        for value in data.values() if isinstance(value, dict)
    }
    out: dict[str, str] = {}
    for row in plan["users"]:
        raw = by_legacy.get(row["legacy_user_id"], "")
        if not raw or hashlib.sha256(raw.encode("utf-8")).hexdigest() != row["password_hash_sha256"]:
            raise MigrationError("SOURCE_USER_SECRET_FACT_MISMATCH")
        out[row["legacy_user_id"]] = raw
    return out


def _constraint_conflict(exc: IntegrityError) -> TargetConflict:
    original = getattr(exc, "orig", None)
    state = getattr(original, "sqlstate", None) or getattr(original, "pgcode", None) or "unknown"
    diagnostic = getattr(original, "diag", None)
    constraint = getattr(diagnostic, "constraint_name", None) or "unknown"
    return TargetConflict(f"DATABASE_CONSTRAINT_CONFLICT:{state}:{constraint}")


def apply_plan(
    plan: dict[str, Any],
    *,
    source_root: str | Path,
    asset_root: str | Path,
    dsn: str,
    quarantine_ack_digest: str | None,
    inject_failure_after: int | None = None,
    production_target: str = "",
) -> dict[str, Any]:
    validate_plan(plan)
    if any(not entry.get("cutover_can_continue_after_ack", False)
           for entry in plan["quarantine"]):
        raise TargetSafetyError("HARD_QUARANTINE_BLOCKS_APPLY")
    if plan["quarantine"] and quarantine_ack_digest != plan["quarantine_manifest_digest"]:
        raise TargetSafetyError("QUARANTINE_DIGEST_ACKNOWLEDGEMENT_REQUIRED")
    from db.migration_tools.assets import verify_source_binding, verify_staged_assets

    verify_source_binding(plan, source_root)
    verified_assets = verify_staged_assets(plan, asset_root)
    passwords = _source_passwords(source_root, plan)
    engine = _guarded_engine(dsn, production_target=production_target)
    target = inspect_target(engine)
    counters: dict[str, dict[str, int]] = defaultdict(
        lambda: {"created": 0, "verified_existing": 0})
    inserted = 0

    def after_insert() -> None:
        nonlocal inserted
        inserted += 1
        if inject_failure_after is not None and inserted >= inject_failure_after:
            raise MigrationError("INJECTED_DATABASE_APPLY_FAILURE")

    imported_at = datetime.now(timezone.utc)
    try:
        with Session(engine, future=True, expire_on_commit=False) as session:
            with session.begin():
                assert_target_has_no_unrelated_data(session, plan)
                for item in plan["users"]:
                    created_at = parse_datetime(item["created_at"]) or imported_at
                    values = {
                        "email": item["email"],
                        "hashed_password": passwords[item["legacy_user_id"]],
                        "full_name": item["full_name"], "is_active": item["is_active"],
                        "legacy_user_id": item["legacy_user_id"], "created_at": created_at,
                        "updated_at": created_at,
                    }
                    _ensure_row(
                        session, User, UUID(item["id"]), values,
                        ("email", "hashed_password", "full_name", "is_active", "legacy_user_id",
                         "created_at"), counters, after_insert)

                brand_models: dict[str, Brand] = {}
                for item in plan["brands"]:
                    created_at = parse_datetime(item["created_at"])
                    updated_at = parse_datetime(item["updated_at"])
                    if created_at is None or updated_at is None:
                        raise MigrationError("BRAND_SOURCE_TIMESTAMP_MISSING")
                    values = {
                        "domain_key": item["domain_key"],
                        "owner_id": UUID(item["owner_id"]) if item["owner_id"] else None,
                        "current_profile_id": None,
                        "display_name": item["display_name"], "source_url": item["source_url"],
                        "deleted_at": None, "created_at": created_at, "updated_at": updated_at,
                    }
                    identity = UUID(item["id"])
                    # Serialize imported SWOT visibility with live generation/approval too.
                    existing = session.get(Brand, identity, with_for_update=True)
                    if existing is None:
                        existing = Brand(id=identity, **values)
                        session.add(existing)
                        session.flush()
                        counters[Brand.__tablename__]["created"] += 1
                        after_insert()
                    else:
                        for field in ("domain_key", "owner_id", "display_name", "source_url",
                                      "deleted_at", "created_at", "updated_at"):
                            if _canonical(getattr(existing, field)) != _canonical(values[field]):
                                raise TargetConflict(
                                    f"TARGET_IDENTITY_CONFLICT:brands:{identity}:{field}")
                        expected_profile = UUID(item["current_profile_id"])
                        if existing.current_profile_id not in (None, expected_profile):
                            raise TargetConflict(
                                f"TARGET_IDENTITY_CONFLICT:brands:{identity}:current_profile_id")
                        counters[Brand.__tablename__]["verified_existing"] += 1
                    brand_models[item["domain_key"]] = existing

                for item in plan["profiles"]:
                    values = {
                        "brand_id": UUID(item["brand_id"]), "version": item["version"],
                        "profile": item["profile"], "schema_version": item["schema_version"],
                        "country_canonical": item["country_canonical"],
                        "country_source": item["country_source"], "country_raw": item["country_raw"],
                        "primary_language": item["primary_language"],
                        "language_source": item["language_source"], "origin": "legacy_import",
                        "created_at": parse_datetime(item["created_at"]),
                        "imported_at": imported_at,
                    }
                    row = _ensure_row(
                        session, ProfileSnapshot, UUID(item["id"]), values,
                        ("brand_id", "version", "profile", "schema_version", "country_canonical",
                         "country_source", "country_raw", "primary_language", "language_source",
                         "origin", "created_at"), counters, after_insert)
                    if row.imported_at is None:
                        raise TargetConflict(
                            f"TARGET_IDENTITY_CONFLICT:profile_snapshots:{item['id']}:imported_at")

                for item in plan["brands"]:
                    brand = brand_models[item["domain_key"]]
                    expected = UUID(item["current_profile_id"])
                    if brand.current_profile_id is None:
                        brand.current_profile_id = expected
                        session.flush()
                    elif brand.current_profile_id != expected:
                        raise TargetConflict(
                            f"TARGET_IDENTITY_CONFLICT:brands:{item['id']}:current_profile_id")

                def insert_run(item: dict[str, Any]) -> None:
                    values = {
                        "brand_id": UUID(item["brand_id"]), "kind": item["kind"],
                        "status": item["status"],
                        "input_profile_id": UUID(item["input_profile_id"])
                        if item["input_profile_id"] else None,
                        "calendar_post_id": UUID(item["calendar_post_id"])
                        if item["calendar_post_id"] else None,
                        "selected_attempt_id": None,
                        "requested_config": item["requested_config"],
                        "effective_config": item["effective_config"],
                        "config_schema_version": item["config_schema_version"],
                        "output": item["output"],
                        "output_schema_version": item["output_schema_version"],
                        "error": item["error"], "kind_seq": None, "origin": "legacy_import",
                        "lineage_complete": False,
                        "legacy_source_key": item["legacy_source_key"],
                        "created_by_user_id": None,
                        "created_at": parse_datetime(item["created_at"]),
                        "imported_at": imported_at,
                        "finished_at": parse_datetime(item["finished_at"]),
                    }
                    row = _ensure_row(
                        session, Run, UUID(item["id"]), values,
                        ("brand_id", "kind", "status", "input_profile_id", "calendar_post_id",
                         "selected_attempt_id", "requested_config", "effective_config",
                         "config_schema_version", "output", "output_schema_version", "error",
                         "kind_seq", "origin", "lineage_complete", "legacy_source_key",
                         "created_by_user_id", "created_at", "finished_at"), counters, after_insert)
                    if row.imported_at is None:
                        raise TargetConflict(
                            f"TARGET_IDENTITY_CONFLICT:runs:{item['id']}:imported_at")

                base_runs = [item for item in plan["runs"] if item["calendar_post_id"] is None]
                targeted_runs = [item for item in plan["runs"] if item["calendar_post_id"] is not None]
                for item in base_runs:
                    insert_run(item)

                for item in plan["calendar_posts"]:
                    values = {
                        "calendar_run_id": UUID(item["calendar_run_id"]),
                        "brand_id": UUID(item["brand_id"]), "calendar_run_kind": "calendar",
                        "ordinal": item["ordinal"],
                        "scheduled_date": date.fromisoformat(item["scheduled_date"])
                        if item["scheduled_date"] else None,
                        "channel": item["channel"], "content_type": item["content_type"],
                        "funnel_stage": item["funnel_stage"], "topic": item["topic"],
                        "hook": item["hook"], "cta": item["cta"], "payload": item["payload"],
                        "schema_version": item["schema_version"], "created_at": imported_at,
                    }
                    _ensure_row(
                        session, CalendarPost, UUID(item["id"]), values,
                        ("calendar_run_id", "brand_id", "calendar_run_kind", "ordinal",
                         "scheduled_date", "channel", "content_type", "funnel_stage", "topic",
                         "hook", "cta", "payload", "schema_version"), counters, after_insert)
                for item in targeted_runs:
                    insert_run(item)

                for item in plan["assets"]:
                    values = {
                        "brand_id": UUID(item["brand_id"]), "storage_key": item["storage_key"],
                        "kind": item["kind"], "mime": item["mime"], "bytes": item["bytes"],
                        "checksum_sha256": item["checksum_sha256"], "width": item["width"],
                        "height": item["height"], "created_by_user_id": None,
                        "created_at": parse_datetime(item["source_created_at"]) or imported_at,
                    }
                    _ensure_row(
                        session, Asset, UUID(item["id"]), values,
                        ("brand_id", "storage_key", "kind", "mime", "bytes", "checksum_sha256",
                         "width", "height", "created_by_user_id"), counters, after_insert)

                for item in plan["attempts"]:
                    values = {
                        "run_id": UUID(item["run_id"]), "brand_id": UUID(item["brand_id"]),
                        "ordinal": item["ordinal"], "status": item["status"],
                        "asset_id": UUID(item["asset_id"]) if item["asset_id"] else None,
                        "qa": item["qa"], "qa_passed": item["qa_passed"], "error": item["error"],
                        "created_at": parse_datetime(item["source_created_at"]) or imported_at,
                        "finished_at": None,
                    }
                    _ensure_row(
                        session, Attempt, UUID(item["id"]), values,
                        ("run_id", "brand_id", "ordinal", "status", "asset_id", "qa",
                         "qa_passed", "error"), counters, after_insert)

                for item in plan["approvals"]:
                    values = {
                        "brand_id": UUID(item["brand_id"]),
                        "subject_run_id": UUID(item["subject_run_id"]),
                        "subject_kind": "swot", "decision": item["decision"],
                        "decided_by_user_id": None, "decided_at": None,
                        "origin": "legacy_import", "imported_at": imported_at, "note": item["note"],
                    }
                    row = _ensure_row(
                        session, Approval, UUID(item["id"]), values,
                        ("brand_id", "subject_run_id", "subject_kind", "decision",
                         "decided_by_user_id", "decided_at", "origin", "note"),
                        counters, after_insert)
                    if row.imported_at is None:
                        raise TargetConflict(
                            f"TARGET_IDENTITY_CONFLICT:approvals:{item['id']}:imported_at")

                for item in plan["owner_inputs"]:
                    identity = UUID(item["brand_id"])
                    existing = session.get(BrandOwnerInputs, identity)
                    if existing is None:
                        session.add(BrandOwnerInputs(
                            brand_id=identity, payload=item["payload"],
                            schema_version=item["schema_version"], updated_at=imported_at))
                        session.flush()
                        counters[BrandOwnerInputs.__tablename__]["created"] += 1
                        after_insert()
                    elif (existing.payload != item["payload"]
                          or existing.schema_version != item["schema_version"]):
                        raise TargetConflict(
                            f"TARGET_IDENTITY_CONFLICT:brand_owner_inputs:{identity}:payload")
                    else:
                        counters[BrandOwnerInputs.__tablename__]["verified_existing"] += 1

                for item in plan["competitors"]:
                    values = {
                        "brand_id": UUID(item["brand_id"]), "key": item["key"],
                        "payload": item["payload"],
                        "created_at": parse_datetime(item["source_created_at"]) or imported_at,
                    }
                    _ensure_row(
                        session, BrandCompetitor, UUID(item["id"]), values,
                        ("brand_id", "key", "payload"), counters, after_insert)
    except IntegrityError as exc:
        raise _constraint_conflict(exc) from exc
    finally:
        engine.dispose()

    planned_tables = {
        "users": len(plan["users"]), "brands": len(plan["brands"]),
        "profile_snapshots": len(plan["profiles"]), "runs": len(plan["runs"]),
        "calendar_posts": len(plan["calendar_posts"]), "assets": len(plan["assets"]),
        "attempts": len(plan["attempts"]), "approvals": len(plan["approvals"]),
        "brand_owner_inputs": len(plan["owner_inputs"]),
        "brand_competitors": len(plan["competitors"]),
    }
    result_counts = {
        table: {**counters[table], "planned": planned}
        for table, planned in planned_tables.items()
    }
    for table, result in result_counts.items():
        if result["created"] + result["verified_existing"] != result["planned"]:
            raise MigrationError(f"APPLY_ROW_RECONCILIATION_FAILED:{table}")
    return {
        "plan_digest": plan["plan_digest"], "target": target,
        "assets": verified_assets, "rows": result_counts,
        "inserted_total": sum(row["created"] for row in result_counts.values()),
        "verified_existing_total": sum(
            row["verified_existing"] for row in result_counts.values()),
    }


__all__ = [
    "apply_plan", "assert_target_has_no_unrelated_data", "inspect_target",
]
