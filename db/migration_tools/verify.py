"""Post-apply reconciliation, referential checks, and PostgreSQL-only read parity."""
from __future__ import annotations

from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy import create_engine, func, select, text
from sqlalchemy.orm import Session

from db.legacy_identity import legacy_user_uuid
from db.migration_tools.apply import (
    _guarded_engine,
    assert_target_has_no_unrelated_data,
    inspect_target,
)
from db.migration_tools.assets import verify_staged_assets
from db.migration_tools.records import MigrationError, content_digest, validate_plan
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

_COUNT_MODELS = {
    "users": User, "brands": Brand, "profile_snapshots": ProfileSnapshot,
    "runs": Run, "calendar_posts": CalendarPost, "assets": Asset, "attempts": Attempt,
    "approvals": Approval, "brand_owner_inputs": BrandOwnerInputs,
    "brand_competitors": BrandCompetitor, "jobs": Job, "usage_records": UsageRecord,
    "run_dependencies": RunDependency,
}


def _scalar_count(session: Session, sql_text: str) -> int:
    return int(session.execute(text(sql_text)).scalar_one())


def _referential_checks(session: Session) -> dict[str, int]:
    checks = {
        "orphan_brand_owners": """
            SELECT count(*) FROM brands b LEFT JOIN users u ON u.id=b.owner_id
            WHERE b.owner_id IS NOT NULL AND u.id IS NULL
        """,
        "duplicate_domain_identity": """
            SELECT count(*) FROM (SELECT domain_key FROM brands GROUP BY domain_key HAVING count(*)>1) x
        """,
        "current_profile_cross_brand": """
            SELECT count(*) FROM brands b LEFT JOIN profile_snapshots p ON p.id=b.current_profile_id
            WHERE b.current_profile_id IS NOT NULL AND (p.id IS NULL OR p.brand_id<>b.id)
        """,
        "run_input_profile_cross_brand": """
            SELECT count(*) FROM runs r LEFT JOIN profile_snapshots p ON p.id=r.input_profile_id
            WHERE r.input_profile_id IS NOT NULL AND (p.id IS NULL OR p.brand_id<>r.brand_id)
        """,
        "selected_attempt_cross_run": """
            SELECT count(*) FROM runs r LEFT JOIN attempts a ON a.id=r.selected_attempt_id
            WHERE r.selected_attempt_id IS NOT NULL AND (a.id IS NULL OR a.run_id<>r.id)
        """,
        "calendar_post_wrong_parent": """
            SELECT count(*) FROM calendar_posts p LEFT JOIN runs r ON r.id=p.calendar_run_id
            WHERE r.id IS NULL OR r.brand_id<>p.brand_id OR r.kind<>'calendar'
        """,
        "attempt_or_asset_cross_brand": """
            SELECT count(*) FROM attempts a LEFT JOIN runs r ON r.id=a.run_id
            LEFT JOIN assets s ON s.id=a.asset_id
            WHERE r.id IS NULL OR r.brand_id<>a.brand_id
               OR (a.asset_id IS NOT NULL AND (s.id IS NULL OR s.brand_id<>a.brand_id))
        """,
        "approval_subject_invalid": """
            SELECT count(*) FROM approvals a LEFT JOIN runs r ON r.id=a.subject_run_id
            WHERE r.id IS NULL OR r.brand_id<>a.brand_id OR r.kind<>a.subject_kind
        """,
        "imported_run_marked_live": """
            SELECT count(*) FROM runs WHERE origin='legacy_import'
             AND (kind_seq IS NOT NULL OR lineage_complete OR created_by_user_id IS NOT NULL)
        """,
        "fabricated_imported_run_created_at": """
            SELECT count(*) FROM runs WHERE origin='legacy_import' AND created_at IS NOT NULL
        """,
        "invented_run_dependencies": "SELECT count(*) FROM run_dependencies",
        "orphan_owner_inputs": """
            SELECT count(*) FROM brand_owner_inputs i LEFT JOIN brands b ON b.id=i.brand_id
            WHERE b.id IS NULL
        """,
        "unexpected_historical_jobs": "SELECT count(*) FROM jobs",
        "unexpected_historical_usage": "SELECT count(*) FROM usage_records",
    }
    return {name: _scalar_count(session, statement) for name, statement in checks.items()}


def _planned_counts(plan: dict[str, Any]) -> dict[str, int]:
    return {
        "users": len(plan["users"]), "brands": len(plan["brands"]),
        "profile_snapshots": len(plan["profiles"]), "runs": len(plan["runs"]),
        "calendar_posts": len(plan["calendar_posts"]), "assets": len(plan["assets"]),
        "attempts": len(plan["attempts"]), "approvals": len(plan["approvals"]),
        "brand_owner_inputs": len(plan["owner_inputs"]),
        "brand_competitors": len(plan["competitors"]), "jobs": 0, "usage_records": 0,
        "run_dependencies": 0,
    }


def _read_parity(session: Session, plan: dict[str, Any]) -> dict[str, bool]:
    expected_users = {
        (row["id"], row["legacy_user_id"], row["email"]) for row in plan["users"]
    }
    actual_users = {(str(row.id), row.legacy_user_id, row.email)
                    for row in session.scalars(select(User))}
    user_mapping = expected_users == actual_users and all(
        str(legacy_user_uuid(legacy_id)) == user_id
        for user_id, legacy_id, _email in actual_users if legacy_id is not None)

    expected_brands = {
        (row["id"], row["domain_key"], row["owner_id"], row["current_profile_id"])
        for row in plan["brands"]
    }
    actual_brands = {(str(row.id), row.domain_key,
                      str(row.owner_id) if row.owner_id else None,
                      str(row.current_profile_id) if row.current_profile_id else None)
                     for row in session.scalars(select(Brand))}

    expected_profiles = {row["id"]: row for row in plan["profiles"]}
    actual_profiles = {str(row.id): row for row in session.scalars(select(ProfileSnapshot))}
    profile_parity = set(expected_profiles) == set(actual_profiles) and all(
        actual_profiles[key].profile == expected["profile"]
        and actual_profiles[key].country_canonical == expected["country_canonical"]
        and actual_profiles[key].country_source == expected["country_source"]
        and actual_profiles[key].country_raw == expected["country_raw"]
        and actual_profiles[key].primary_language == expected["primary_language"]
        and actual_profiles[key].language_source == expected["language_source"]
        for key, expected in expected_profiles.items())

    expected_posts = {(row["calendar_run_id"], row["ordinal"]): row["payload"]
                      for row in plan["calendar_posts"]}
    actual_posts = {(str(row.calendar_run_id), row.ordinal): row.payload
                    for row in session.scalars(select(CalendarPost))}

    expected_approvals = {(row["subject_run_id"], row["decision"])
                          for row in plan["approvals"]}
    actual_approvals = {(str(row.subject_run_id), row.decision)
                        for row in session.scalars(select(Approval))}
    approval_truth = expected_approvals == actual_approvals and all(
        row.origin == "legacy_import" and row.decided_by_user_id is None
        and row.decided_at is None and row.imported_at is not None
        for row in session.scalars(select(Approval)))

    expected_inputs = {row["brand_id"]: row["payload"] for row in plan["owner_inputs"]}
    actual_inputs = {str(row.brand_id): row.payload
                     for row in session.scalars(select(BrandOwnerInputs))}
    expected_competitors = {(row["brand_id"], row["key"]): row["payload"]
                            for row in plan["competitors"]}
    actual_competitors = {(str(row.brand_id), row.key): row.payload
                          for row in session.scalars(select(BrandCompetitor))}

    expected_assets = {row["id"]: (row["storage_key"], row["checksum_sha256"], row["bytes"])
                       for row in plan["assets"]}
    actual_assets = {str(row.id): (row.storage_key, row.checksum_sha256, row.bytes)
                     for row in session.scalars(select(Asset))}
    history_ids = {str(row["logical_asset_id"]) for row in plan["history"]
                   if row.get("logical_asset_id")}

    current_run_truth = True
    grouped: dict[tuple[str, str], list[str]] = {}
    for row in plan["runs"]:
        grouped.setdefault((row["brand_id"], row["kind"]), []).append(row["id"])
    for (brand_id, kind), expected_ids in grouped.items():
        rows = list(session.scalars(select(Run).where(
            Run.brand_id == UUID(brand_id), Run.kind == kind, Run.status == "succeeded"
        ).order_by(Run.kind_seq.desc().nullslast(), Run.id)))
        if len(expected_ids) != 1 or len(rows) != 1 or str(rows[0].id) != expected_ids[0]:
            current_run_truth = False
            break

    return {
        "user_legacy_uuid_mapping": user_mapping,
        "brand_visibility_ownership": expected_brands == actual_brands,
        "current_profiles_and_provenance": profile_parity,
        "current_run_uniqueness": current_run_truth,
        "swot_approval_subject_truth": approval_truth,
        "calendar_order_and_same_date_multiplicity": expected_posts == actual_posts,
        "owner_inputs": expected_inputs == actual_inputs,
        "real_competitor_registry_relationships": expected_competitors == actual_competitors,
        "managed_assets": expected_assets == actual_assets,
        "migrated_history_assets": history_ids <= set(actual_assets),
    }


def verify_database(
    plan: dict[str, Any], *, asset_root: str | Path, dsn: str,
    production_target: str = "",
) -> dict[str, Any]:
    validate_plan(plan)
    staged = verify_staged_assets(plan, asset_root)
    engine = _guarded_engine(dsn, production_target=production_target)
    target = inspect_target(engine)
    try:
        with Session(engine, future=True) as session:
            assert_target_has_no_unrelated_data(session, plan)
            actual_counts = {
                name: int(session.scalar(select(func.count()).select_from(model)) or 0)
                for name, model in _COUNT_MODELS.items()
            }
            planned_counts = _planned_counts(plan)
            if actual_counts != planned_counts:
                raise MigrationError("DB_ROW_COUNT_RECONCILIATION_FAILED")
            referential = _referential_checks(session)
            invalid_legacy_mapping = sum(
                str(legacy_user_uuid(row.legacy_user_id)) != str(row.id)
                for row in session.scalars(select(User).where(User.legacy_user_id.is_not(None)))
            )
            referential["invalid_legacy_user_id_mapping"] = invalid_legacy_mapping
            if any(referential.values()):
                failed = ",".join(sorted(key for key, value in referential.items() if value))
                raise MigrationError(f"POSTGRES_REFERENTIAL_VALIDATION_FAILED:{failed}")
            parity = _read_parity(session, plan)
            if not all(parity.values()):
                failed = ",".join(sorted(key for key, value in parity.items() if not value))
                raise MigrationError(f"DB_ONLY_READ_PARITY_FAILED:{failed}")
    finally:
        engine.dispose()

    equations = plan["reconciliation"]
    unexplained = {}
    for entity, row in equations.items():
        remainder = int(row["source"]) - sum(int(row[key]) for key in (
            "migrated", "quarantined", "non_authoritative", "ignored_by_policy"))
        unexplained[entity] = remainder
    if any(unexplained.values()):
        raise MigrationError("SOURCE_COUNT_RECONCILIATION_FAILED")
    byte_plan = plan["byte_reconciliation"]
    if (
        int(byte_plan["source_referenced_byte_sum"])
        != int(byte_plan["planned_byte_sum"])
        + int(byte_plan["quarantined_or_missing_byte_sum"])
        or staged["verified_bytes"] != int(byte_plan["planned_byte_sum"])
    ):
        raise MigrationError("ASSET_BYTE_RECONCILIATION_FAILED")
    report: dict[str, Any] = {
        "format_version": "baseera-reconciliation/v1",
        "plan_digest": plan["plan_digest"], "target": target,
        "planned_row_counts": planned_counts, "actual_row_counts": actual_counts,
        "source_entity_equations": equations, "unexplained_items": unexplained,
        "byte_reconciliation": byte_plan, "staged_assets": staged,
        "referential_zero_checks": referential, "db_only_read_parity": parity,
    }
    report["reconciliation_digest"] = content_digest(report)
    return report


__all__ = ["verify_database"]
