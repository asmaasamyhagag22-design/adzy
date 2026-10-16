"""Non-interactive operational CLI for Phase 2E and the later 2F rehearsal."""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from db.migration_tools.inventory import build_source_manifest
from db.migration_tools.plan import build_migration_plan
from db.migration_tools.records import MigrationError, load_json, write_json_atomic


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m db.migration_tools.cli")
    commands = parser.add_subparsers(dest="mode", required=True)

    inventory = commands.add_parser("INVENTORY")
    inventory.add_argument("--source-root", required=True)
    inventory.add_argument("--output-dir", required=True)

    plan = commands.add_parser("PLAN")
    plan.add_argument("--source-root", required=True)
    plan.add_argument("--output-dir", required=True)

    stage = commands.add_parser("STAGE-ASSETS")
    stage.add_argument("--source-root", required=True)
    stage.add_argument("--plan", required=True)
    stage.add_argument("--target-asset-root", required=True)
    stage.add_argument("--output-dir", required=True)

    apply = commands.add_parser("APPLY")
    apply.add_argument("--source-root", required=True)
    apply.add_argument("--plan", required=True)
    apply.add_argument("--target-asset-root", required=True)
    apply.add_argument("--output-dir", required=True)
    apply.add_argument("--ack-quarantine-digest")
    # THE ONE DOOR TO A NON-DISPOSABLE TARGET. Absent means refuse, which is the default and the
    # right default. Present, it must name the target database EXACTLY — so the operator writes the
    # production name out in full, in the same command, and a near-miss is refused rather than
    # accepted. See ACTUAL_DB_CUTOVER_RUNBOOK.md.
    apply.add_argument("--production-target", default="", metavar="EXACT_DATABASE_NAME",
                       help="confirm a non-disposable target by naming its database exactly")
    apply_dsn = apply.add_mutually_exclusive_group(required=True)
    apply_dsn.add_argument("--dsn")
    apply_dsn.add_argument("--dsn-env")

    verify = commands.add_parser("VERIFY")
    verify.add_argument("--plan", required=True)
    verify.add_argument("--target-asset-root", required=True)
    verify.add_argument("--output-dir", required=True)
    verify.add_argument("--production-target", default="", metavar="EXACT_DATABASE_NAME",
                        help="confirm a non-disposable target by naming its database exactly")
    verify_dsn = verify.add_mutually_exclusive_group(required=True)
    verify_dsn.add_argument("--dsn")
    verify_dsn.add_argument("--dsn-env")
    return parser


def _dsn(args: argparse.Namespace) -> str:
    if getattr(args, "dsn", None):
        return str(args.dsn)
    name = str(getattr(args, "dsn_env", "") or "")
    value = os.environ.get(name, "") if name else ""
    if not value:
        raise MigrationError("EXPLICIT_DSN_ENVIRONMENT_VARIABLE_IS_EMPTY")
    return value


def _event(output_dir: Path, mode: str, plan_digest: str | None, result: dict[str, Any]) -> None:
    path = output_dir / "execution_manifest.json"
    existing: dict[str, Any] = {}
    if path.is_file():
        try:
            loaded = load_json(path)
            existing = loaded if isinstance(loaded, dict) else {}
        except (OSError, ValueError):
            existing = {}
    events = existing.get("events") if existing.get("plan_digest") == plan_digest else None
    events = list(events) if isinstance(events, list) else []
    events.append({
        "mode": mode, "completed_at": datetime.now(timezone.utc).isoformat(), "result": result,
    })
    write_json_atomic(path, {
        "format_version": "baseera-migration-execution/v1",
        "plan_digest": plan_digest, "events": events,
    })


def _summary(**values: Any) -> None:
    print(json.dumps(values, ensure_ascii=False, sort_keys=True))


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    output = Path(args.output_dir).resolve(strict=False)
    output.mkdir(parents=True, exist_ok=True)
    if args.mode == "INVENTORY":
        manifest = build_source_manifest(args.source_root)
        write_json_atomic(output / "source_manifest.json", manifest)
        result = {"source_manifest_digest": manifest["source_manifest_digest"],
                  "file_count": len(manifest["files"])}
        _event(output, args.mode, None, result)
        _summary(ok=True, mode=args.mode, **result)
        return 0
    if args.mode == "PLAN":
        plan, source, quarantine = build_migration_plan(args.source_root)
        write_json_atomic(output / "source_manifest.json", source)
        write_json_atomic(output / "migration_plan.json", plan)
        write_json_atomic(output / "quarantine_manifest.json", quarantine)
        result = {"plan_digest": plan["plan_digest"],
                  "quarantine_manifest_digest": plan["quarantine_manifest_digest"],
                  "quarantine_count": len(plan["quarantine"])}
        _event(output, args.mode, plan["plan_digest"], result)
        _summary(ok=True, mode=args.mode, **result)
        return 0

    plan = load_json(Path(args.plan))
    if not isinstance(plan, dict):
        raise MigrationError("PLAN_ROOT_IS_NOT_AN_OBJECT")
    if args.mode == "STAGE-ASSETS":
        from db.migration_tools.assets import stage_assets

        manifest, stats = stage_assets(
            plan, source_root=args.source_root, target_root=args.target_asset_root)
        write_json_atomic(output / "asset_manifest.json", manifest)
        result = {"asset_manifest_digest": manifest["asset_manifest_digest"], **stats}
        _event(output, args.mode, plan.get("plan_digest"), result)
        _summary(ok=True, mode=args.mode, plan_digest=plan.get("plan_digest"), **result)
        return 0
    if args.mode == "APPLY":
        from db.migration_tools.apply import apply_plan

        result = apply_plan(
            plan, source_root=args.source_root, asset_root=args.target_asset_root,
            dsn=_dsn(args), quarantine_ack_digest=args.ack_quarantine_digest,
            production_target=args.production_target)
        _event(output, args.mode, plan.get("plan_digest"), result)
        _summary(ok=True, mode=args.mode, plan_digest=plan.get("plan_digest"),
                 inserted_total=result["inserted_total"],
                 verified_existing_total=result["verified_existing_total"])
        return 0
    if args.mode == "VERIFY":
        from db.migration_tools.verify import verify_database

        result = verify_database(plan, asset_root=args.target_asset_root, dsn=_dsn(args),
                                 production_target=args.production_target)
        write_json_atomic(output / "reconciliation.json", result)
        event_result = {"reconciliation_digest": result["reconciliation_digest"],
                        "unexplained_items": sum(abs(v) for v in result["unexplained_items"].values())}
        _event(output, args.mode, plan.get("plan_digest"), event_result)
        _summary(ok=True, mode=args.mode, plan_digest=plan.get("plan_digest"), **event_result)
        return 0
    raise MigrationError("UNKNOWN_MIGRATION_MODE")


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except MigrationError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, sort_keys=True), file=sys.stderr)
        raise SystemExit(2)
