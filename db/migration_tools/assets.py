"""Checksum-bound, restartable staging for managed asset bytes."""
from __future__ import annotations

import os
import re
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any

from db.migration_tools.inventory import SourceLayout, build_source_manifest
from db.migration_tools.plan import _inline_bytes, observed_mime
from db.migration_tools.records import (
    AssetCollision,
    AssetStageError,
    SourceChanged,
    content_digest,
    load_json,
    sha256_file,
    validate_plan,
)

_HISTORY_LOCATOR = re.compile(r"^webapp/history\.json#/(\d+)/image_b64$")


def verify_source_binding(plan: dict[str, Any], source_root: str | Path) -> dict[str, Any]:
    current = build_source_manifest(source_root)
    if current["source_manifest_digest"] != plan.get("source_manifest_digest"):
        raise SourceChanged("SOURCE_CHANGED_AFTER_PLAN")
    return current


def safe_target_path(target_root: str | Path, storage_key: str) -> Path:
    root = Path(target_root).resolve(strict=False)
    key = str(storage_key or "")
    posix = PurePosixPath(key)
    windows = PureWindowsPath(key)
    if (
        not key or "\\" in key or posix.is_absolute() or windows.is_absolute()
        or windows.drive or any(part in ("", ".", "..") for part in posix.parts)
    ):
        raise AssetStageError("UNSAFE_STORAGE_KEY")
    destination = (root / Path(*posix.parts)).resolve(strict=False)
    try:
        destination.relative_to(root)
    except ValueError as exc:
        raise AssetStageError("STORAGE_KEY_ESCAPES_TARGET_ROOT") from exc
    return destination


def _read_local_source(layout: SourceLayout, asset: dict[str, Any]) -> bytes:
    locator = str(asset["source_locator"])
    if not locator.startswith("webapp/generated/"):
        raise AssetStageError("INVALID_LOCAL_ASSET_LOCATOR")
    generated_root = (layout.webapp / "generated").resolve(strict=True)
    source = layout.webapp / locator.removeprefix("webapp/")
    if source.is_symlink():
        raise AssetStageError("LOCAL_ASSET_SYMLINK_NOT_ALLOWED")
    try:
        resolved = source.resolve(strict=True)
        resolved.relative_to(generated_root)
    except (OSError, ValueError) as exc:
        raise AssetStageError("LOCAL_ASSET_OUTSIDE_SOURCE_ROOT") from exc
    if not resolved.is_file():
        raise AssetStageError("REFERENCED_ASSET_BYTES_MISSING")
    return resolved.read_bytes()


def _read_inline_source(
    history: list[Any], asset: dict[str, Any],
) -> bytes:
    matched = _HISTORY_LOCATOR.fullmatch(str(asset["source_locator"]))
    if not matched:
        raise AssetStageError("INVALID_INLINE_ASSET_LOCATOR")
    index = int(matched.group(1))
    try:
        item = history[index]
    except IndexError as exc:
        raise AssetStageError("INLINE_ASSET_SOURCE_MISSING") from exc
    if not isinstance(item, dict):
        raise AssetStageError("INLINE_ASSET_SOURCE_INVALID")
    raw, _declared, error = _inline_bytes(item.get("image_b64"))
    if error or raw is None:
        raise AssetStageError("INLINE_ASSET_DECODE_FAILED")
    return raw


def _validate_bytes(raw: bytes, asset: dict[str, Any]) -> None:
    if len(raw) != int(asset["bytes"]):
        raise AssetStageError("ASSET_SOURCE_LENGTH_MISMATCH")
    import hashlib

    if hashlib.sha256(raw).hexdigest() != asset["checksum_sha256"]:
        raise AssetStageError("ASSET_SOURCE_CHECKSUM_MISMATCH")
    if observed_mime(raw) != asset["observed_mime"]:
        raise AssetStageError("ASSET_SOURCE_MIME_MISMATCH")


def _verify_existing(destination: Path, asset: dict[str, Any]) -> bool:
    if not destination.exists():
        return False
    if not destination.is_file() or destination.is_symlink():
        raise AssetCollision("ASSET_STORAGE_KEY_COLLISION")
    if destination.stat().st_size != int(asset["bytes"]):
        raise AssetCollision("ASSET_STORAGE_KEY_COLLISION")
    if sha256_file(destination) != asset["checksum_sha256"]:
        raise AssetCollision("ASSET_STORAGE_KEY_COLLISION")
    return True


def _atomic_write(destination: Path, raw: bytes, asset: dict[str, Any]) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if _verify_existing(destination, asset):
        return
    tmp = destination.with_name(f".{destination.name}.{os.getpid()}.partial")
    try:
        with tmp.open("xb") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        if tmp.stat().st_size != int(asset["bytes"]) or sha256_file(tmp) != asset["checksum_sha256"]:
            raise AssetStageError("ASSET_TEMPORARY_WRITE_VERIFICATION_FAILED")
        if destination.exists():
            if _verify_existing(destination, asset):
                return
            raise AssetCollision("ASSET_STORAGE_KEY_COLLISION")
        os.replace(tmp, destination)
        if not _verify_existing(destination, asset):  # pragma: no cover - helper either True/raises
            raise AssetStageError("ASSET_FINAL_WRITE_VERIFICATION_FAILED")
    finally:
        if tmp.exists():
            tmp.unlink()


def stage_assets(
    plan: dict[str, Any],
    *,
    source_root: str | Path,
    target_root: str | Path,
    inject_failure_after: int | None = None,
) -> tuple[dict[str, Any], dict[str, int]]:
    validate_plan(plan)
    verify_source_binding(plan, source_root)
    layout = SourceLayout.resolve(source_root)
    root = Path(target_root).resolve(strict=False)
    root.mkdir(parents=True, exist_ok=True)
    history: list[Any] | None = None
    staged = 0
    verified_existing = 0
    entries: list[dict[str, Any]] = []
    for sequence, asset in enumerate(plan["assets"], start=1):
        destination = safe_target_path(root, asset["storage_key"])
        existed = _verify_existing(destination, asset)
        if not existed:
            if asset["source_representation"] == "local_file":
                raw = _read_local_source(layout, asset)
            elif asset["source_representation"] in ("raw_base64", "data_uri"):
                if history is None:
                    loaded = load_json(layout.webapp / "history.json")
                    if not isinstance(loaded, list):
                        raise AssetStageError("INVALID_HISTORY_ROOT")
                    history = loaded
                raw = _read_inline_source(history, asset)
            else:
                raise AssetStageError("UNKNOWN_ASSET_SOURCE_REPRESENTATION")
            _validate_bytes(raw, asset)
            _atomic_write(destination, raw, asset)
            staged += 1
        else:
            verified_existing += 1
        if inject_failure_after is not None and sequence >= inject_failure_after:
            raise AssetStageError("INJECTED_ASSET_STAGE_FAILURE")
        entries.append({
            "logical_id": asset["id"], "storage_key": asset["storage_key"],
            "source_locator": asset["source_locator"],
            "source_classification": asset["source_classification"],
            "sha256": asset["checksum_sha256"], "bytes": asset["bytes"],
            "declared_mime": asset["declared_mime"],
            "observed_mime": asset["observed_mime"],
            "domain_key": asset["domain_key"], "brand_id": asset["brand_id"],
            "relations": asset["relations"], "created_by_user_id": None,
            "migration_status": "STAGED",
        })
    manifest: dict[str, Any] = {
        "format_version": "baseera-asset-manifest/v1",
        "plan_digest": plan["plan_digest"],
        "logical_asset_count": len(entries),
        "physical_blob_count": len(entries),
        "byte_sum": sum(int(entry["bytes"]) for entry in entries),
        "entries": entries,
    }
    manifest["asset_manifest_digest"] = content_digest(manifest)
    return manifest, {"staged": staged, "verified_existing": verified_existing}


def verify_staged_assets(
    plan: dict[str, Any], target_root: str | Path,
) -> dict[str, int]:
    validate_plan(plan)
    count = 0
    total = 0
    for asset in plan["assets"]:
        destination = safe_target_path(target_root, asset["storage_key"])
        if not _verify_existing(destination, asset):
            raise AssetStageError("ASSET_BYTES_NOT_STAGED")
        count += 1
        total += int(asset["bytes"])
    return {"verified_assets": count, "verified_bytes": total}


__all__ = [
    "safe_target_path", "stage_assets", "verify_source_binding", "verify_staged_assets",
]
