"""Small, deterministic primitives shared by the migration commands."""
from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from db.legacy_identity import MIGRATION_NAMESPACE

TOOL_FORMAT_VERSION = "baseera-legacy-migration-plan/v1"
TOOL_VERSION = "2E.1"


def _head_from_migration_files() -> str | None:
    """The head revision, read from the shipped migration scripts. Filesystem only.

    A regex over `revision =` / `down_revision =`, returning the revision nothing points back to.
    Nothing is imported, nothing connects, no migration runs — this decides whether the tool will
    REFUSE a target, so it must not itself be able to change one.
    """
    versions = Path(__file__).resolve().parents[2] / "db" / "migrations" / "versions"
    if not versions.is_dir():
        return None
    revisions: set[str] = set()
    parents: set[str] = set()
    for script in versions.glob("*.py"):
        text = script.read_text(encoding="utf-8", errors="replace")
        found = re.search(r"^revision\s*=\s*[\"']([^\"']+)[\"']", text, re.M)
        parent = re.search(r"^down_revision\s*=\s*[\"']([^\"']+)[\"']", text, re.M)
        if found:
            revisions.add(found.group(1))
        if parent:
            parents.add(parent.group(1))
    heads = revisions - parents
    return heads.pop() if len(heads) == 1 else None


#: The head a migration TARGET must already be at. Derived rather than hand-maintained: as a
#: constant it was a manual step noticed only once it was already wrong, and being wrong here means
#: the tool refuses a correct target (safe) or accepts a stale one (not).
EXPECTED_SCHEMA_HEAD = _head_from_migration_files() or "0005_strategy_approvals"

_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class MigrationError(RuntimeError):
    """A safe, operator-actionable migration refusal."""


class PlanValidationError(MigrationError):
    pass


class SourceChanged(MigrationError):
    pass


class AssetStageError(MigrationError):
    pass


class AssetCollision(AssetStageError):
    pass


class TargetSafetyError(MigrationError):
    pass


class TargetConflict(MigrationError):
    pass


def canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False,
    ).encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path, *, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def content_digest(value: Any) -> str:
    return sha256_bytes(canonical_json_bytes(value))


def deterministic_uuid(entity: str, *identity: Any) -> uuid.UUID:
    """Derive all non-user migration IDs from the already released migration namespace."""
    name = canonical_json_bytes(["legacy-migration", str(entity), *identity]).decode("utf-8")
    return uuid.uuid5(MIGRATION_NAMESPACE, name)


def iso_from_epoch(value: Any) -> str | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        return datetime.fromtimestamp(value, tz=timezone.utc).isoformat()
    except (OverflowError, OSError, ValueError):
        return None


def parse_datetime(value: Any) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc) if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        try:
            return datetime.fromtimestamp(value, tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    text = str(value).strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed.astimezone(timezone.utc) if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def normalise_datetime(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.astimezone(timezone.utc).isoformat()


def plan_digest_basis(plan: dict[str, Any]) -> dict[str, Any]:
    basis = copy.deepcopy(plan)
    basis.pop("plan_digest", None)
    basis.pop("quarantine_manifest_digest", None)
    return basis


def calculate_plan_digest(plan: dict[str, Any]) -> str:
    return content_digest(plan_digest_basis(plan))


def quarantine_manifest_from_plan(plan: dict[str, Any]) -> dict[str, Any]:
    manifest: dict[str, Any] = {
        "format_version": "baseera-quarantine-manifest/v1",
        "plan_digest": plan["plan_digest"],
        "entries": [{**entry, "plan_digest": plan["plan_digest"]}
                    for entry in plan.get("quarantine", [])],
    }
    manifest["quarantine_manifest_digest"] = content_digest(manifest)
    return manifest


def validate_plan(plan: dict[str, Any]) -> None:
    if plan.get("format_version") != TOOL_FORMAT_VERSION:
        raise PlanValidationError("INCOMPATIBLE_PLAN_FORMAT")
    if plan.get("expected_schema_head") != EXPECTED_SCHEMA_HEAD:
        raise PlanValidationError("WRONG_PLAN_SCHEMA_HEAD")
    actual = str(plan.get("plan_digest") or "")
    if not _SHA256.fullmatch(actual) or actual != calculate_plan_digest(plan):
        raise PlanValidationError("CORRUPTED_PLAN_DIGEST")
    quarantine_digest = str(plan.get("quarantine_manifest_digest") or "")
    if (not _SHA256.fullmatch(quarantine_digest)
            or quarantine_digest != quarantine_manifest_from_plan(plan)[
                "quarantine_manifest_digest"]):
        raise PlanValidationError("CORRUPTED_QUARANTINE_DIGEST")


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json_atomic(path: Path, value: Any) -> None:
    """Write a small JSON artifact atomically; media bytes use the stricter asset writer."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        with tmp.open("wb") as handle:
            handle.write(json.dumps(
                value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False,
            ).encode("utf-8"))
            handle.write(b"\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink()


def safe_relative(path: Path, root: Path) -> str:
    try:
        return path.resolve(strict=False).relative_to(root.resolve(strict=True)).as_posix()
    except (OSError, ValueError) as exc:
        raise MigrationError("PATH_OUTSIDE_SOURCE_ROOT") from exc


__all__ = [
    "AssetCollision", "AssetStageError", "EXPECTED_SCHEMA_HEAD", "MigrationError",
    "PlanValidationError", "SourceChanged", "TOOL_FORMAT_VERSION", "TOOL_VERSION",
    "TargetConflict", "TargetSafetyError", "calculate_plan_digest", "canonical_json_bytes",
    "content_digest", "deterministic_uuid", "iso_from_epoch", "load_json",
    "normalise_datetime", "parse_datetime", "sha256_bytes", "sha256_file", "validate_plan",
    "write_json_atomic", "quarantine_manifest_from_plan",
]
