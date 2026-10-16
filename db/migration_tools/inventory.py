"""Forensic inventory and legacy authority classification."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from competitor.registry import brand_ref_from_profile, registry_path

from db.migration_tools.records import MigrationError, content_digest, sha256_file
from db.migration_tools.runtime_semantics import brand_store_safe_key, domain_key_from_url

ACTIVE = "ACTIVE"
NON_AUTHORITATIVE_BACKUP = "NON_AUTHORITATIVE_BACKUP"
CONFLICTING_OWNER = "CONFLICTING_OWNER"
INVALID = "INVALID"
TEMPORARY = "TEMPORARY"
UNKNOWN_REQUIRES_REVIEW = "UNKNOWN_REQUIRES_REVIEW"
IGNORED_BY_POLICY = "IGNORED_BY_POLICY"

ACTIVE_BRAND_REFERENCED = "ACTIVE_BRAND_REFERENCED"
HISTORY_REFERENCED = "HISTORY_REFERENCED"
CONFLICTING_BRAND_ONLY = "CONFLICTING_BRAND_ONLY"
ORPHAN_UNREFERENCED = "ORPHAN_UNREFERENCED"
TEST_ARTIFACT_IF_PROVEN = "TEST_ARTIFACT_IF_PROVEN"

_GENERATED_URL = re.compile(r"^/(?:api/)?generated/([^/?#]+)$")


@dataclass(frozen=True, slots=True)
class SourceLayout:
    source_root: Path
    webapp: Path
    registry: Path

    @classmethod
    def resolve(cls, source_root: str | Path) -> "SourceLayout":
        root = Path(source_root).resolve(strict=True)
        if (root / "outputs" / "webapp").is_dir():
            webapp = root / "outputs" / "webapp"
            registry = root / "registry"
        elif (root / "webapp").is_dir():
            webapp = root / "webapp"
            registry = root / "registry"
        elif (root / "brands").is_dir():
            webapp = root
            registry = root.parent / "registry"
        else:
            raise MigrationError("SOURCE_ROOT_HAS_NO_WEBAPP_STATE")
        return cls(root, webapp.resolve(strict=True), registry.resolve(strict=False))

    def label(self, path: Path) -> str:
        resolved = path.absolute()
        try:
            return "webapp/" + resolved.relative_to(self.webapp.absolute()).as_posix()
        except ValueError:
            try:
                return "registry/" + resolved.relative_to(self.registry.absolute()).as_posix()
            except ValueError as exc:
                raise MigrationError("SOURCE_PATH_OUTSIDE_DECLARED_ROOTS") from exc


def _read_json_object(path: Path) -> tuple[dict[str, Any] | None, str | None]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError) as exc:
        return None, type(exc).__name__
    return (value, None) if isinstance(value, dict) else (None, "NOT_AN_OBJECT")


def _source_url(brand: dict[str, Any]) -> str:
    profile = brand.get("_profile") if isinstance(brand.get("_profile"), dict) else {}
    return str(
        brand.get("source_url") or profile.get("source_url") or profile.get("final_url") or ""
    ).strip()


def _domain_identity(brand: dict[str, Any]) -> tuple[str | None, str | None]:
    source_url = _source_url(brand)
    if not source_url:
        return None, "MISSING_SOURCE_URL"
    recomputed = domain_key_from_url(source_url)
    if not recomputed:
        return None, "INVALID_SOURCE_URL"
    stored = str(brand.get("domain_key") or "")
    if stored != recomputed:
        return recomputed, "DOMAIN_KEY_MISMATCH"
    return recomputed, None


def _walk_generated_references(value: Any, path: tuple[str, ...] = ()) -> Iterable[dict[str, str]]:
    if isinstance(value, dict):
        for key, child in value.items():
            yield from _walk_generated_references(child, (*path, str(key)))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from _walk_generated_references(child, (*path, str(index)))
    elif isinstance(value, str):
        matched = _GENERATED_URL.fullmatch(value.strip())
        if matched:
            yield {"name": matched.group(1), "json_path": "/".join(path), "value": value}


def _file_record(layout: SourceLayout, path: Path, classification: str, **extra: Any) -> dict:
    if path.is_symlink():
        link_bytes = str(path.readlink()).encode("utf-8", errors="surrogatepass")
        size = len(link_bytes)
        from db.migration_tools.records import sha256_bytes

        digest = sha256_bytes(link_bytes)
    else:
        size = path.stat().st_size
        digest = sha256_file(path)
    record = {
        "path": layout.label(path),
        "size": size,
        "sha256": digest,
        "classification": classification,
    }
    record.update({key: value for key, value in extra.items() if value is not None})
    return record


def _brand_candidates(layout: SourceLayout) -> tuple[list[dict], dict[str, dict], dict[str, list]]:
    brand_dir = layout.webapp / "brands"
    records: list[dict] = []
    parsed: list[tuple[Path, dict, str | None, str | None]] = []
    for path in sorted((p for p in brand_dir.iterdir() if p.is_file()), key=lambda p: p.name):
        if path.is_symlink():
            records.append(_file_record(layout, path, INVALID, reason="SYMLINK_NOT_ALLOWED"))
            continue
        if ".tmp" in path.name.lower():
            records.append(_file_record(layout, path, TEMPORARY))
            continue
        payload, read_error = _read_json_object(path)
        if payload is None:
            records.append(_file_record(layout, path, INVALID, reason=read_error))
            continue
        domain, identity_error = _domain_identity(payload)
        parsed.append((path, payload, domain, identity_error))

    canonical: dict[str, dict] = {}
    for path, payload, domain, identity_error in parsed:
        expected = f"{brand_store_safe_key(domain or '')}.json" if domain else ""
        if identity_error is None and path.name == expected:
            canonical[domain or ""] = payload

    conflicts: dict[str, list] = {}
    for path, payload, domain, identity_error in parsed:
        expected = f"{brand_store_safe_key(domain or '')}.json" if domain else ""
        if identity_error is None and path.name == expected:
            classification, reason = ACTIVE, None
        elif domain in canonical and str(payload.get("owner_id") or "") != str(
            canonical[domain].get("owner_id") or ""
        ):
            classification, reason = CONFLICTING_OWNER, "OWNER_DIFFERS_FROM_CANONICAL"
            conflicts.setdefault(domain or "", []).append(payload)
        elif ".bak." in path.name.lower() or path.name.lower().endswith(".bak.json"):
            classification, reason = NON_AUTHORITATIVE_BACKUP, "NONCANONICAL_BACKUP_NAME"
        elif identity_error:
            classification, reason = INVALID, identity_error
        else:
            classification, reason = UNKNOWN_REQUIRES_REVIEW, "NONCANONICAL_BRAND_PATH"
        records.append(_file_record(
            layout, path, classification, domain_key=domain,
            owner_id=str(payload.get("owner_id") or "") or None, reason=reason,
        ))
    return records, canonical, conflicts


def build_source_manifest(source_root: str | Path) -> dict[str, Any]:
    layout = SourceLayout.resolve(source_root)
    files: list[dict] = []
    brand_records, active_brands, conflicting = _brand_candidates(layout)
    files.extend(brand_records)

    active_refs: dict[str, list[dict]] = {}
    conflict_refs: dict[str, list[dict]] = {}
    for domain, payload in sorted(active_brands.items()):
        for reference in _walk_generated_references(payload):
            active_refs.setdefault(reference["name"], []).append({
                "domain_key": domain, "json_path": reference["json_path"],
                "source": f"webapp/brands/{brand_store_safe_key(domain)}.json",
            })
    for domain, payloads in sorted(conflicting.items()):
        for payload in payloads:
            for reference in _walk_generated_references(payload):
                conflict_refs.setdefault(reference["name"], []).append({
                    "domain_key": domain, "json_path": reference["json_path"],
                })

    history_path = layout.webapp / "history.json"
    history_refs: dict[str, list[dict]] = {}
    if history_path.is_file():
        try:
            history = json.loads(history_path.read_text(encoding="utf-8"))
            if not isinstance(history, list):
                raise ValueError("history root is not a list")
            files.append(_file_record(layout, history_path, ACTIVE, record_count=len(history)))
            for index, item in enumerate(history):
                for reference in _walk_generated_references(item):
                    history_refs.setdefault(reference["name"], []).append({
                        "history_index": index, "json_path": reference["json_path"],
                    })
        except (OSError, UnicodeError, ValueError) as exc:
            files.append(_file_record(layout, history_path, INVALID, reason=type(exc).__name__))
    else:
        raise MigrationError("MISSING_AUTHORITATIVE_HISTORY")

    users_path = layout.webapp / "users.json"
    if not users_path.is_file():
        raise MigrationError("MISSING_AUTHORITATIVE_USERS")
    try:
        users = json.loads(users_path.read_text(encoding="utf-8"))
        if not isinstance(users, dict):
            raise ValueError("users root is not an object")
        files.append(_file_record(layout, users_path, ACTIVE, record_count=len(users)))
    except (OSError, UnicodeError, ValueError) as exc:
        files.append(_file_record(layout, users_path, INVALID, reason=type(exc).__name__))

    for path in sorted(layout.webapp.glob("users*.json"), key=lambda p: p.name):
        if path != users_path and path.is_file():
            files.append(_file_record(layout, path, NON_AUTHORITATIVE_BACKUP))
    secret = layout.webapp / ".auth_secret"
    if secret.is_file():
        files.append(_file_record(layout, secret, IGNORED_BY_POLICY,
                                  reason="AUTH_SECRET_NEVER_MIGRATED"))

    for backup_dir in sorted(layout.webapp.glob("brands_backup*"), key=lambda p: p.name):
        if backup_dir.is_dir():
            for path in sorted((p for p in backup_dir.rglob("*") if p.is_file())):
                files.append(_file_record(layout, path, NON_AUTHORITATIVE_BACKUP))

    active_registry_names: dict[str, str] = {}
    for domain, brand in sorted(active_brands.items()):
        profile = brand.get("_profile") if isinstance(brand.get("_profile"), dict) else {}
        reference = brand_ref_from_profile(profile)
        name = registry_path(reference, store_dir=str(layout.registry)).name
        active_registry_names[name] = domain
    if layout.registry.is_dir():
        for path in sorted((p for p in layout.registry.iterdir() if p.is_file()), key=lambda p: p.name):
            classification = ACTIVE if path.name in active_registry_names else IGNORED_BY_POLICY
            extra = ({"domain_key": active_registry_names[path.name]}
                     if classification == ACTIVE else {"reason": "NO_ACTIVE_BRAND_RELATION"})
            files.append(_file_record(layout, path, classification, **extra))

    generated = layout.webapp / "generated"
    physical_names: set[str] = set()
    if generated.is_dir():
        for path in sorted((p for p in generated.rglob("*") if p.is_file()), key=lambda p: p.as_posix()):
            relative = path.relative_to(generated).as_posix()
            physical_names.add(relative)
            if path.is_symlink():
                classification, reason = INVALID, "SYMLINK_NOT_ALLOWED"
            elif relative in active_refs:
                classification, reason = ACTIVE_BRAND_REFERENCED, None
            elif relative in history_refs:
                classification, reason = HISTORY_REFERENCED, None
            elif relative in conflict_refs:
                classification, reason = CONFLICTING_BRAND_ONLY, None
            else:
                classification, reason = ORPHAN_UNREFERENCED, None
            files.append(_file_record(layout, path, classification, reason=reason))

    missing_references = []
    for source_class, references in (
        (ACTIVE_BRAND_REFERENCED, active_refs),
        (HISTORY_REFERENCED, history_refs),
        (CONFLICTING_BRAND_ONLY, conflict_refs),
    ):
        for name in sorted(references):
            if name not in physical_names:
                missing_references.append({
                    "name": name, "classification": source_class,
                    "references": references[name],
                })

    files.sort(key=lambda row: row["path"])
    counts: dict[str, int] = {}
    bytes_by_class: dict[str, int] = {}
    for row in files:
        key = row["classification"]
        counts[key] = counts.get(key, 0) + 1
        bytes_by_class[key] = bytes_by_class.get(key, 0) + int(row["size"])
    manifest: dict[str, Any] = {
        "format_version": "baseera-source-manifest/v1",
        "files": files,
        "counts_by_classification": dict(sorted(counts.items())),
        "bytes_by_classification": dict(sorted(bytes_by_class.items())),
        "active_domains": sorted(active_brands),
        "generated_references": {
            "active": {key: active_refs[key] for key in sorted(active_refs)},
            "history": {key: history_refs[key] for key in sorted(history_refs)},
            "conflicting": {key: conflict_refs[key] for key in sorted(conflict_refs)},
            "missing": missing_references,
        },
    }
    manifest["source_manifest_digest"] = content_digest(manifest)
    return manifest


def manifest_digest_is_valid(manifest: dict[str, Any]) -> bool:
    value = dict(manifest)
    expected = str(value.pop("source_manifest_digest", ""))
    return expected == content_digest(value)


__all__ = [
    "ACTIVE", "ACTIVE_BRAND_REFERENCED", "CONFLICTING_BRAND_ONLY", "CONFLICTING_OWNER",
    "HISTORY_REFERENCED", "IGNORED_BY_POLICY", "INVALID", "NON_AUTHORITATIVE_BACKUP",
    "ORPHAN_UNREFERENCED", "SourceLayout", "TEMPORARY", "TEST_ARTIFACT_IF_PROVEN",
    "UNKNOWN_REQUIRES_REVIEW", "build_source_manifest", "manifest_digest_is_valid",
]
