"""Build a canonical, reviewable migration plan before any database write."""
from __future__ import annotations

import base64
import binascii
import json
import re
from collections import Counter
from collections.abc import Mapping
from datetime import date
from pathlib import Path
from typing import Any

from competitor.registry import brand_ref_from_profile, registry_path

from db.legacy_identity import legacy_user_uuid
from db.migration_tools.inventory import (
    ACTIVE,
    ACTIVE_BRAND_REFERENCED,
    CONFLICTING_BRAND_ONLY,
    CONFLICTING_OWNER,
    HISTORY_REFERENCED,
    IGNORED_BY_POLICY,
    NON_AUTHORITATIVE_BACKUP,
    ORPHAN_UNREFERENCED,
    SourceLayout,
    build_source_manifest,
)
from db.migration_tools.records import (
    EXPECTED_SCHEMA_HEAD,
    TOOL_FORMAT_VERSION,
    TOOL_VERSION,
    MigrationError,
    calculate_plan_digest,
    deterministic_uuid,
    iso_from_epoch,
    parse_datetime,
    quarantine_manifest_from_plan,
    sha256_bytes,
)
from db.migration_tools.runtime_semantics import content_language_decision, country_decision

_DATA_URI = re.compile(r"^data:([^;,]*)(;base64)?,(.*)$", re.DOTALL | re.IGNORECASE)
_LEGACY_USER_ID = re.compile(r"^[0-9a-f]{16}$")
_LOCAL_GENERATED_URL = re.compile(r"^/(?:api/)?generated/([^/?#]+)$")


def observed_mime(raw: bytes) -> str | None:
    if (len(raw) >= 33 and raw.startswith(b"\x89PNG\r\n\x1a\n")
            and raw[12:16] == b"IHDR"
            and raw.endswith(b"\x00\x00\x00\x00IEND\xaeB`\x82")):
        return "image/png"
    if len(raw) >= 4 and raw.startswith(b"\xff\xd8\xff") and raw.endswith(b"\xff\xd9"):
        return "image/jpeg"
    if len(raw) >= 14 and raw.startswith((b"GIF87a", b"GIF89a")) and raw.endswith(b";"):
        return "image/gif"
    if (len(raw) >= 16 and raw[:4] == b"RIFF" and raw[8:12] == b"WEBP"
            and int.from_bytes(raw[4:8], "little") + 8 == len(raw)):
        return "image/webp"
    return None


def _inline_bytes(value: Any) -> tuple[bytes | None, str | None, str | None]:
    """Return bytes, declared MIME, and a safe reason code (never the payload)."""
    if not isinstance(value, str):
        return None, None, "INLINE_ASSET_NOT_A_STRING"
    payload = value
    declared: str | None = None
    if value.lower().startswith("data:"):
        matched = _DATA_URI.fullmatch(value)
        if not matched or not matched.group(2):
            return None, None, "INLINE_ASSET_DECODE_FAILED"
        declared = matched.group(1).strip().lower() or None
        payload = matched.group(3)
    try:
        raw = base64.b64decode(payload, validate=True)
    except (binascii.Error, ValueError):
        return None, declared, "INLINE_ASSET_DECODE_FAILED"
    if not raw:
        return None, declared, "INLINE_ASSET_DECODE_FAILED"
    return raw, declared, None


def _quarantine(
    locator: str,
    reason: str,
    *,
    source_sha256: str | None = None,
    payload_size: int | None = None,
    domain_key: str | None = None,
    source_owner_id: str | None = None,
    active_owner_id: str | None = None,
    explanation: str,
    human: bool = True,
    continue_with_ack: bool = True,
) -> dict[str, Any]:
    return {
        "quarantine_id": str(deterministic_uuid("quarantine", locator, reason)),
        "source_locator": locator,
        "source_sha256": source_sha256,
        "payload_size": payload_size,
        "domain_key": domain_key,
        "source_owner_id": source_owner_id,
        "active_owner_id": active_owner_id,
        "reason": reason,
        "safe_explanation": explanation,
        "human_decision_required": human,
        "cutover_can_continue_after_ack": continue_with_ack,
    }


def _profile_created_at(profile: dict[str, Any]) -> str | None:
    meta = profile.get("extraction_meta")
    value = meta.get("extracted_at") if isinstance(meta, dict) else None
    parsed = parse_datetime(value)
    return parsed.isoformat() if parsed else None


def _valid_swot(value: Any) -> bool:
    return isinstance(value, dict) and all(
        isinstance(value.get(key), list)
        for key in ("strengths", "weaknesses", "opportunities", "threats")
    )


def _valid_strategy(value: Any) -> bool:
    return isinstance(value, dict) and bool(value) and any(
        key in value for key in ("strategic_posture", "tows_matrix", "goals")
    )


#: The flag each stage's patch sets beside its block. Live application patches carry it (see
#: `api/webapp_analysis.generate_swot` / `generate_strategy`), the legacy documents carry it, and
#: `api/read_model` folds `run.output` onto the brand document verbatim — so a migrated run that
#: omits it produces a brand the SPA renders as "no SWOT yet".
_STAGE_FLAG = {"swot": "has_swot", "marketing_strategy": "has_strategy"}


def _stage_output(document_key: str, block: Any, *, extras: Mapping[str, Any] = ()) -> dict:
    """The canonical `Run.output` for a stage whose result lives under ONE document key.

    A LIVE run's output is the generator's patch verbatim — `{"swot": {...}, "has_swot": true}`,
    `{"marketing_strategy": {...}, "has_strategy": true}` — because `webapp_jobs._persist` hands the
    patch straight to `runs.output`. `api/read_model` then merges that output onto the brand
    document at the top level, which is why the wrapper key IS the contract: the SPA reads
    `brand.swot`, `brand.has_swot`, `brand.marketing_strategy`, `brand.has_strategy`.

    This function existed as four inconsistent call sites. `trends`, `calendar`, `influencer_poster`
    and `poster` wrapped their blocks; SWOT and Strategy passed the INNER block, so their fields
    scattered across the document and both keys came back null — beside `swot_approved: true`,
    because approval derives from an Approval row and does not care what the fold produced. Measured
    on the real corpus: every migrated brand rendered an empty SWOT panel and an empty Strategy
    panel. One function, so the shape cannot drift per stage again.

    WRAPS EXACTLY ONCE. A source that already carries `{"swot": {...}}` is unwrapped first, so no
    input shape can produce `{"swot": {"swot": {...}}}`.
    """
    payload = block
    if isinstance(block, dict) and document_key in block:
        flag = _STAGE_FLAG.get(document_key, "")
        # Only when the outer dict is a WRAPPER and nothing else — a block that merely happens to
        # contain a same-named field keeps its own shape.
        if set(block) <= {document_key, flag, "_swot_raw"}:
            payload = block[document_key]
    out: dict[str, Any] = {document_key: payload}
    flag = _STAGE_FLAG.get(document_key)
    if flag:
        out[flag] = True
    for key, value in dict(extras or {}).items():
        if value is not None:
            out[key] = value
    return out


def _valid_calendar(value: Any) -> bool:
    return isinstance(value, dict) and isinstance(value.get("calendar"), list) and bool(
        value.get("calendar")
    )


def _run_row(
    domain: str,
    brand_id: str,
    kind: str,
    source_key: str,
    output: dict[str, Any],
    *,
    calendar_post_id: str | None = None,
    selected_attempt_id: str | None = None,
) -> dict[str, Any]:
    return {
        "id": str(deterministic_uuid("run", domain, source_key)),
        "brand_id": brand_id,
        "domain_key": domain,
        "kind": kind,
        "status": "succeeded",
        "kind_seq": None,
        "input_profile_id": None,
        "calendar_post_id": calendar_post_id,
        "selected_attempt_id": selected_attempt_id,
        "requested_config": {},
        "effective_config": None,
        "config_schema_version": 1,
        "output": output,
        "output_schema_version": 1,
        "error": None,
        "origin": "legacy_import",
        "lineage_complete": False,
        "legacy_source_key": source_key,
        "created_by_user_id": None,
        "created_at": None,
        "finished_at": None,
        "source_locator": f"webapp/brands/{domain}.json#/{source_key}",
        "migration_classification": "MIGRATE_AUTHORITATIVE_CURRENT_STATE",
        "provenance": "legacy_brand_current_block",
    }


def _storage_key(brand_id: str, asset_id: str, mime: str) -> str:
    extension = {"image/png": ".png", "image/jpeg": ".jpg", "image/gif": ".gif",
                 "image/webp": ".webp"}.get(mime, ".bin")
    return f"legacy/{brand_id}/{asset_id}{extension}"


def _source_file_map(manifest: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {str(row["path"]): row for row in manifest["files"]}


def _safe_calendar_date(value: Any) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return date.fromisoformat(value.strip()).isoformat()
    except ValueError:
        return None


def _local_generated_name(value: Any) -> str | None:
    matched = _LOCAL_GENERATED_URL.fullmatch(str(value or "").strip())
    return matched.group(1) if matched else None


def _load_active_brands(layout: SourceLayout, manifest: dict[str, Any]) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for row in manifest["files"]:
        if row["classification"] != ACTIVE or not row["path"].startswith("webapp/brands/"):
            continue
        path = layout.webapp / row["path"].removeprefix("webapp/")
        payload = json.loads(path.read_text(encoding="utf-8"))
        out[str(payload["domain_key"])] = payload
    return out


def _active_registry_rows(layout: SourceLayout, manifest: dict[str, Any]) -> dict[str, Path]:
    out = {}
    for row in manifest["files"]:
        if row["classification"] == ACTIVE and row["path"].startswith("registry/"):
            out[str(row["domain_key"])] = layout.registry / row["path"].removeprefix("registry/")
    return out


def build_migration_plan(source_root: str | Path) -> tuple[dict, dict, dict]:
    layout = SourceLayout.resolve(source_root)
    manifest = build_source_manifest(source_root)
    file_map = _source_file_map(manifest)
    active_brands = _load_active_brands(layout, manifest)
    active_registry = _active_registry_rows(layout, manifest)

    plan: dict[str, Any] = {
        "format_version": TOOL_FORMAT_VERSION,
        "tool_version": TOOL_VERSION,
        "expected_schema_head": EXPECTED_SCHEMA_HEAD,
        "source_manifest_digest": manifest["source_manifest_digest"],
        "users": [], "brands": [], "profiles": [], "runs": [], "calendar_posts": [],
        "attempts": [], "assets": [], "approvals": [], "owner_inputs": [],
        "competitors": [], "jobs": [], "usage": [], "history": [], "quarantine": [],
        "ignored_files": [],
    }
    quarantine: list[dict[str, Any]] = plan["quarantine"]

    users_path = layout.webapp / "users.json"
    users = json.loads(users_path.read_text(encoding="utf-8"))
    known_users: dict[str, str] = {}
    for email_key, payload in sorted(users.items()):
        locator = f"webapp/users.json#/{email_key}"
        if not isinstance(payload, dict):
            quarantine.append(_quarantine(
                locator, "UNREPRESENTABLE_SOURCE_FACT", explanation="user record is not an object"))
            continue
        email = str(payload.get("email") or email_key).strip().lower()
        legacy_id = str(payload.get("id") or "")
        password_hash = payload.get("password_hash")
        if email != str(email_key).strip().lower() or not email or not _LEGACY_USER_ID.fullmatch(
            legacy_id
        ) or not isinstance(password_hash, str) or not password_hash:
            quarantine.append(_quarantine(
                locator, "UNREPRESENTABLE_SOURCE_FACT",
                explanation="user identity, canonical email, or password hash is invalid"))
            continue
        user_id = str(legacy_user_uuid(legacy_id))
        known_users[legacy_id] = user_id
        plan["users"].append({
            "id": user_id, "legacy_user_id": legacy_id, "email": email,
            "password_hash_sha256": sha256_bytes(password_hash.encode("utf-8")),
            "full_name": payload.get("full_name") if isinstance(payload.get("full_name"), str) else None,
            "is_active": True, "created_at": iso_from_epoch(payload.get("created_at")),
            "source_locator": locator,
            "migration_classification": "MIGRATE_AUTHORITATIVE",
            "provenance": "legacy_user_store",
        })

    brand_ids: dict[str, str] = {}
    profile_ids: dict[str, str] = {}
    brand_owners: dict[str, str] = {}
    for domain, brand in sorted(active_brands.items()):
        owner_legacy = str(brand.get("owner_id") or "")
        if (brand.get("deleted_at") or brand.get("archived") is True) and not owner_legacy:
            row = file_map[f"webapp/brands/{domain}.json"]
            quarantine.append(_quarantine(
                row["path"], "CUTOVER_PRODUCT_DECISION_REQUIRED_ARCHIVED_UNCLAIMED",
                source_sha256=row["sha256"], payload_size=row["size"], domain_key=domain,
                explanation="archived unclaimed Brand cannot be claimed, reactivated, or assigned",
                continue_with_ack=False,
            ))
            continue
        owner_id = known_users.get(owner_legacy) if owner_legacy else None
        ownership = "UNCLAIMED" if not owner_legacy else (
            "OWNED_RESOLVED" if owner_id else "ORPHAN_OWNER")
        if ownership == "ORPHAN_OWNER":
            quarantine.append(_quarantine(
                f"webapp/brands/{domain}.json", "ORPHAN_OWNER", domain_key=domain,
                source_owner_id=owner_legacy, active_owner_id=None,
                source_sha256=file_map[f"webapp/brands/{domain}.json"]["sha256"],
                payload_size=file_map[f"webapp/brands/{domain}.json"]["size"],
                explanation="active Brand names an owner absent from the authoritative user store",
                continue_with_ack=False,
            ))
            continue
        brand_id = str(deterministic_uuid("brand", domain))
        profile_id = str(deterministic_uuid("profile", domain, 1))
        brand_ids[domain] = brand_id
        profile_ids[domain] = profile_id
        brand_owners[domain] = owner_legacy
        profile = brand.get("_profile")
        if not isinstance(profile, dict) or not profile:
            quarantine.append(_quarantine(
                f"webapp/brands/{domain}.json#/_profile", "UNREPRESENTABLE_SOURCE_FACT",
                domain_key=domain, explanation="current profile is absent or not an object",
                continue_with_ack=False,
            ))
            continue
        country = country_decision(profile)
        language = content_language_decision(profile)
        plan["brands"].append({
            "id": brand_id, "domain_key": domain, "owner_id": owner_id,
            "owner_legacy_id": owner_legacy or None, "ownership": ownership,
            "current_profile_id": profile_id,
            "display_name": brand.get("organization_name") if isinstance(
                brand.get("organization_name"), str) else None,
            "source_url": str(brand.get("source_url") or "") or None,
            "deleted_at": None, "created_at": iso_from_epoch(brand.get("created_at")),
            "updated_at": iso_from_epoch(brand.get("updated_at")),
            "source_locator": f"webapp/brands/{domain}.json",
            "classification": ACTIVE,
            "migration_classification": "MIGRATE_AUTHORITATIVE",
            "provenance": "canonical_brand_store_document",
        })
        plan["profiles"].append({
            "id": profile_id, "brand_id": brand_id, "domain_key": domain, "version": 1,
            "profile": profile, "schema_version": 1,
            "country_canonical": country.country or None,
            "country_source": country.source,
            "country_raw": country.raw or None,
            "primary_language": language.language or None,
            "language_source": f"migration_derived_{language.source}",
            "origin": "legacy_import", "created_at": _profile_created_at(profile),
            "source_locator": f"webapp/brands/{domain}.json#/_profile",
            "migration_classification": "MIGRATE_AUTHORITATIVE_CURRENT_STATE",
            "provenance": "full_legacy_profile_with_migration_derived_projections",
        })

    for row in manifest["files"]:
        if row["classification"] == CONFLICTING_OWNER:
            domain = str(row.get("domain_key") or "")
            quarantine.append(_quarantine(
                row["path"], "CONFLICTING_OWNER_NONCANONICAL_BRAND",
                source_sha256=row["sha256"], payload_size=row["size"], domain_key=domain,
                source_owner_id=row.get("owner_id"), active_owner_id=brand_owners.get(domain) or None,
                explanation="noncanonical Brand candidate conflicts with the canonical active owner",
            ))

    local_assets_by_name: dict[str, dict[str, Any]] = {}
    for name, references in manifest["generated_references"]["active"].items():
        domains = sorted({str(ref["domain_key"]) for ref in references})
        locator = f"webapp/generated/{name}"
        if len(domains) != 1 or domains[0] not in brand_ids:
            quarantine.append(_quarantine(
                locator, "UNREPRESENTABLE_SOURCE_FACT",
                explanation="generated file reference has no single migratable active Brand"))
            continue
        row = file_map.get(f"webapp/generated/{name}")
        if row is not None and row["classification"] != ACTIVE_BRAND_REFERENCED:
            row = None
        if row is None:
            quarantine.append(_quarantine(
                locator, "REFERENCED_ASSET_BYTES_MISSING", domain_key=domains[0],
                explanation="active Brand references generated bytes absent from the source root"))
            continue
        source_path = layout.webapp / row["path"].removeprefix("webapp/")
        if source_path.is_symlink() or not source_path.is_file():
            quarantine.append(_quarantine(
                row["path"], "REFERENCED_ASSET_BYTES_MISSING", domain_key=domains[0],
                source_sha256=row["sha256"], payload_size=row["size"],
                explanation="referenced source is not a contained regular file"))
            continue
        source_raw = source_path.read_bytes()
        mime = observed_mime(source_raw)
        if mime is None:
            quarantine.append(_quarantine(
                row["path"], "UNREPRESENTABLE_SOURCE_FACT", domain_key=domains[0],
                source_sha256=row["sha256"], payload_size=row["size"],
                explanation="referenced generated file has an unrecognised media format"))
            continue
        domain = domains[0]
        asset_id = str(deterministic_uuid("asset", "generated", domain, name))
        json_paths = sorted({ref["json_path"] for ref in references})
        kind = ("calendar_poster" if any("content_calendar/" in p for p in json_paths)
                else "influencer_poster" if any(p.endswith("poster_url") for p in json_paths)
                else "influencer_portrait")
        asset = {
            "id": asset_id, "brand_id": brand_ids[domain], "domain_key": domain,
            "storage_key": _storage_key(brand_ids[domain], asset_id, mime), "kind": kind,
            "mime": mime, "bytes": int(row["size"]), "checksum_sha256": row["sha256"],
            "width": None, "height": None, "created_by_user_id": None,
            "source_created_at": None, "source_locator": row["path"],
            "source_representation": "local_file", "declared_mime": None,
            "observed_mime": mime, "source_classification": ACTIVE_BRAND_REFERENCED,
            "relations": references, "migration_status": "PLANNED",
            "migration_classification": "MIGRATE_MANAGED_ASSET",
            "provenance": "active_brand_reference_creator_unknown",
        }
        plan["assets"].append(asset)
        local_assets_by_name[name] = asset

    run_by_domain_kind: dict[tuple[str, str], dict[str, Any]] = {}
    calendar_run_by_domain: dict[str, dict[str, Any]] = {}
    for domain, brand in sorted(active_brands.items()):
        if domain not in brand_ids:
            continue
        brand_id = brand_ids[domain]
        candidates: list[tuple[str, str, dict[str, Any]]] = []
        if bool(brand.get("has_trends")) and isinstance(brand.get("trends"), list) and brand["trends"]:
            candidates.append(("trends", "trends", _stage_output("trends", brand["trends"])))
        if bool(brand.get("has_swot")) and _valid_swot(brand.get("swot")):
            # `_swot_raw` is carried when the source has it: the live patch includes it, and it
            # holds the trends the frontend projection drops. Absent in the source means absent
            # here — it is not invented.
            candidates.append(("swot", "swot", _stage_output(
                "swot", dict(brand["swot"]),
                extras={"_swot_raw": brand.get("_swot_raw")})))
        if bool(brand.get("has_strategy")) and _valid_strategy(brand.get("marketing_strategy")):
            candidates.append(("strategy", "marketing_strategy", _stage_output(
                "marketing_strategy", dict(brand["marketing_strategy"]))))
        if bool(brand.get("has_plan")) and _valid_calendar(brand.get("content_calendar")):
            candidates.append(("calendar", "content_calendar", {
                "marketing_plan": brand.get("marketing_plan"),
                "content_calendar": brand.get("content_calendar"),
            }))
        ai = brand.get("ai_influencer")
        domain_assets = [asset for asset in plan["assets"] if asset["domain_key"] == domain]
        influencer_assets = [a for a in domain_assets if a["kind"].startswith("influencer_")]
        if isinstance(ai, dict) and influencer_assets and (ai.get("has_portrait") or ai.get("has_poster")):
            candidates.append(("influencer_poster", "ai_influencer", {
                "ai_influencer": ai,
                "migration_asset_ids": [a["id"] for a in sorted(
                    influencer_assets, key=lambda item: item["id"])],
            }))
        for kind, source_key, output in candidates:
            run = _run_row(domain, brand_id, kind, source_key, output)
            plan["runs"].append(run)
            run_by_domain_kind[(domain, kind)] = run
            if kind == "calendar":
                calendar_run_by_domain[domain] = run

    for domain, calendar_run in sorted(calendar_run_by_domain.items()):
        rows = ((active_brands[domain].get("content_calendar") or {}).get("calendar") or [])
        for ordinal, payload in enumerate(rows):
            locator = f"webapp/brands/{domain}.json#/content_calendar/calendar/{ordinal}"
            if not isinstance(payload, dict):
                quarantine.append(_quarantine(
                    locator, "UNREPRESENTABLE_SOURCE_FACT", domain_key=domain,
                    explanation="calendar item is not an object"))
                continue
            raw_date = payload.get("date") or payload.get("scheduled_date")
            scheduled = _safe_calendar_date(raw_date) if raw_date not in (None, "") else None
            if raw_date not in (None, "") and scheduled is None:
                quarantine.append(_quarantine(
                    locator, "UNREPRESENTABLE_SOURCE_FACT", domain_key=domain,
                    explanation="calendar item has a malformed scheduled date"))
                continue
            post_id = str(deterministic_uuid("calendar_post", domain, calendar_run["id"], ordinal))
            plan["calendar_posts"].append({
                "id": post_id, "calendar_run_id": calendar_run["id"],
                "brand_id": brand_ids[domain], "domain_key": domain, "ordinal": ordinal,
                "scheduled_date": scheduled,
                "channel": payload.get("platform") or payload.get("channel"),
                "content_type": payload.get("content_type") or payload.get("type"),
                "funnel_stage": (str(payload.get("funnel_stage")).lower()
                                 if str(payload.get("funnel_stage") or "").lower()
                                 in {"tofu", "mofu", "bofu"} else None),
                "topic": payload.get("topic"), "hook": payload.get("hook"),
                "cta": payload.get("cta"), "payload": payload, "schema_version": 1,
                "source_locator": locator, "provenance": "legacy_calendar_ordinal",
                "migration_classification": "MIGRATE_AUTHORITATIVE_CURRENT_STATE",
            })
            poster = payload.get("poster")
            url = poster.get("url") if isinstance(poster, dict) else None
            name = _local_generated_name(url)
            asset = local_assets_by_name.get(name) if name else None
            if asset:
                source_key = f"content_calendar/calendar/{ordinal}/poster"
                run = _run_row(
                    domain, brand_ids[domain], "poster", source_key,
                    {"poster": poster, "migration_asset_id": asset["id"]},
                    calendar_post_id=post_id,
                )
                plan["runs"].append(run)

    output_source_count = len(plan["runs"])
    grouped_runs: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for run in plan["runs"]:
        grouped_runs.setdefault((run["domain_key"], run["kind"]), []).append(run)
    ambiguous_ids: set[str] = set()
    for (domain, kind), rows in sorted(grouped_runs.items()):
        if len(rows) <= 1:
            continue
        for run in rows:
            ambiguous_ids.add(run["id"])
            quarantine.append(_quarantine(
                run["source_locator"], "AMBIGUOUS_LEGACY_CURRENT_RUN", domain_key=domain,
                explanation=(f"multiple current legacy outputs map to Run kind {kind!r} without "
                             "a truthful sequence"),
                continue_with_ack=False,
            ))
    if ambiguous_ids:
        plan["runs"] = [run for run in plan["runs"] if run["id"] not in ambiguous_ids]

    attempts_by_run: dict[str, list[dict[str, Any]]] = {}
    for domain, brand in sorted(active_brands.items()):
        ai = brand.get("ai_influencer")
        run = run_by_domain_kind.get((domain, "influencer_poster"))
        if not isinstance(ai, dict) or run is None:
            continue
        candidates = ai.get("candidates") if isinstance(ai.get("candidates"), dict) else {}
        for candidate_key, candidate in candidates.items():
            if not isinstance(candidate, dict):
                continue
            name = _local_generated_name(candidate.get("url"))
            asset = local_assets_by_name.get(name) if name else None
            if asset is None:
                continue
            rows = attempts_by_run.setdefault(run["id"], [])
            attempt_id = str(deterministic_uuid("attempt", run["id"], str(candidate_key)))
            rows.append({
                "id": attempt_id, "run_id": run["id"], "brand_id": brand_ids[domain],
                "domain_key": domain, "ordinal": len(rows), "status": "succeeded",
                "asset_id": asset["id"], "qa": {"legacy_candidate": candidate},
                "qa_passed": None, "error": None, "source_created_at": None,
                "source_locator": f"webapp/brands/{domain}.json#/ai_influencer/candidates/{candidate_key}",
                "migration_classification": "MIGRATE_AUTHORITATIVE_CURRENT_STATE",
                "provenance": "legacy_candidate_creator_unknown",
            })
        if not candidates and ai.get("has_poster"):
            name = _local_generated_name(ai.get("poster_url"))
            asset = local_assets_by_name.get(name) if name else None
            if asset is not None:
                attempt_id = str(deterministic_uuid("attempt", run["id"], "legacy-final-poster"))
                attempts_by_run.setdefault(run["id"], []).append({
                    "id": attempt_id, "run_id": run["id"], "brand_id": brand_ids[domain],
                    "domain_key": domain, "ordinal": 0, "status": "succeeded",
                    "asset_id": asset["id"], "qa": {"legacy_validated": ai.get("validated")},
                    "qa_passed": True if ai.get("validated") is True else None, "error": None,
                    "source_created_at": None,
                    "source_locator": f"webapp/brands/{domain}.json#/ai_influencer/poster_url",
                    "migration_classification": "MIGRATE_AUTHORITATIVE_CURRENT_STATE",
                    "provenance": "legacy_final_poster_creator_unknown",
                })
    plan["attempts"] = sorted(
        [row for rows in attempts_by_run.values() for row in rows],
        key=lambda row: (row["run_id"], row["ordinal"]),
    )

    history_path = layout.webapp / "history.json"
    history_sha = file_map["webapp/history.json"]["sha256"]
    history = json.loads(history_path.read_text(encoding="utf-8"))
    for index, item in enumerate(history):
        locator = f"webapp/history.json#/{index}"
        if not isinstance(item, dict):
            quarantine.append(_quarantine(
                locator, "UNREPRESENTABLE_SOURCE_FACT", source_sha256=history_sha,
                explanation="history entry is not an object"))
            plan["history"].append({"index": index, "source_locator": locator,
                                    "classification": "QUARANTINED"})
            continue
        domain = str(item.get("domain_key") or "")
        source_owner = str(item.get("owner_id") or "")
        active_owner = brand_owners.get(domain, "__ABSENT__")
        media_value = item.get("image_b64")
        media_url = str(item.get("image_url") or item.get("url") or "").strip()
        asset_source_locator = f"{locator}/image_b64"
        source_representation = "metadata_only"
        declared: str | None = None
        decode_error: str | None = None
        raw: bytes | None = None
        if isinstance(media_value, str):
            raw, declared, decode_error = _inline_bytes(media_value)
            source_representation = (
                "data_uri" if media_value.lower().startswith("data:") else "raw_base64")
        elif media_url.startswith(("http://", "https://")):
            decode_error = "EXTERNAL_URL_NOT_A_MANAGED_ASSET"
            source_representation = "external_url"
        elif media_url:
            local_match = _LOCAL_GENERATED_URL.fullmatch(media_url)
            name = local_match.group(1) if local_match else ""
            if not local_match:
                decode_error = "MEDIA_REPRESENTATION_MISSING"
            generated_record = file_map.get(f"webapp/generated/{name}")
            source_representation = "local_file"
            if decode_error:
                pass
            elif generated_record is None or generated_record["classification"] not in {
                HISTORY_REFERENCED, ACTIVE_BRAND_REFERENCED,
            }:
                decode_error = "REFERENCED_ASSET_BYTES_MISSING"
            else:
                source_path = layout.webapp / generated_record["path"].removeprefix("webapp/")
                if source_path.is_symlink() or not source_path.is_file():
                    decode_error = "REFERENCED_ASSET_BYTES_MISSING"
                else:
                    raw = source_path.read_bytes()
                    asset_source_locator = generated_record["path"]
        else:
            decode_error = "MEDIA_REPRESENTATION_MISSING"
        observed = observed_mime(raw or b"")
        reason: str | None = None
        explanation = "history media could not be represented safely"
        if decode_error in {"INLINE_ASSET_NOT_A_STRING", "INLINE_ASSET_DECODE_FAILED"}:
            reason = "INLINE_ASSET_DECODE_FAILED"
            explanation = "inline media could not be decoded safely"
        elif decode_error == "REFERENCED_ASSET_BYTES_MISSING":
            reason = decode_error
            explanation = "history references local generated bytes absent from the source root"
        elif decode_error == "EXTERNAL_URL_NOT_A_MANAGED_ASSET":
            reason = "UNREPRESENTABLE_SOURCE_FACT"
            explanation = "external URL is preserved as source content and is not downloaded"
        elif decode_error:
            reason = "UNREPRESENTABLE_SOURCE_FACT"
            explanation = "history entry contains no managed media representation"
        if raw is not None and observed is None:
            reason = "UNREPRESENTABLE_SOURCE_FACT"
            explanation = "decoded inline bytes have an unrecognised media format"
        elif declared and observed and declared != observed:
            reason = "UNREPRESENTABLE_SOURCE_FACT"
            explanation = "declared data-URI MIME differs from observed bytes"
        elif domain not in brand_ids:
            # NOT "this image is dropped". It is not a BRAND-SCOPED ASSET; the gallery entry
            # migrates through the gallery importer, which owns `gallery_blobs`/`gallery_entries`.
            reason = "HISTORY_NOT_A_BRAND_SCOPED_ASSET"
            explanation = (
                "history entry has no migratable canonical Brand, so it becomes no brand-scoped "
                "Asset; the GALLERY entry itself still migrates (gallery_entries)"
            )
        elif source_owner != active_owner:
            reason = "HISTORY_NOT_A_BRAND_SCOPED_ASSET"
            explanation = (
                "history viewer scope differs from Brand visibility, so a brand-scoped Asset "
                "would expose it to a different principal; the GALLERY entry itself still "
                "migrates (gallery_entries), owned by the viewer the source names"
            )
        history_row = {
            "index": index, "source_locator": locator,
            "representation": source_representation,
            "domain_key": domain or None, "source_owner_id": source_owner or None,
            "source_recorded_at": iso_from_epoch(item.get("created_at")),
            "declared_mime": declared, "observed_mime": observed,
            "bytes": len(raw) if raw is not None else None,
            "sha256": sha256_bytes(raw) if raw is not None else None,
            "classification": "QUARANTINED" if reason else "MIGRATABLE_WITH_UNKNOWN_CREATOR",
        }
        if reason:
            quarantine.append(_quarantine(
                locator, reason, source_sha256=history_sha,
                payload_size=len(raw) if raw is not None else None, domain_key=domain or None,
                source_owner_id=source_owner or None,
                active_owner_id=None if active_owner == "__ABSENT__" else active_owner or None,
                explanation=explanation,
            ))
        else:
            assert raw is not None and observed is not None
            asset_id = str(deterministic_uuid("asset", "history", index, history_sha))
            history_row["logical_asset_id"] = asset_id
            plan["assets"].append({
                "id": asset_id, "brand_id": brand_ids[domain], "domain_key": domain,
                "storage_key": _storage_key(brand_ids[domain], asset_id, observed),
                "kind": f"legacy_history_{str(item.get('content_type') or 'media')}",
                "mime": observed, "bytes": len(raw), "checksum_sha256": sha256_bytes(raw),
                "width": None, "height": None, "created_by_user_id": None,
                "source_created_at": history_row["source_recorded_at"],
                "source_locator": asset_source_locator, "history_index": index,
                "source_representation": history_row["representation"],
                "declared_mime": declared, "observed_mime": observed,
                "source_classification": HISTORY_REFERENCED,
                "relations": [{"domain_key": domain, "history_index": index,
                               "viewer_owner_id": source_owner or None}],
                "migration_status": "PLANNED",
                "migration_classification": "MIGRATE_MANAGED_ASSET",
                "provenance": "legacy_history_creator_unknown",
            })
        history_row["gallery_disposition"] = "MIGRATED_BY_GALLERY_IMPORT"
        history_row["asset_disposition"] = (
            "NOT_A_BRAND_SCOPED_ASSET" if reason else "MIGRATE_MANAGED_ASSET")
        plan["history"].append(history_row)

    # WHAT AN OPERATOR MUST NOT MISREAD. The rows above are the ASSET disposition of each history
    # entry. The GALLERY disposition is separate and is not decided here: every logical entry
    # migrates through `api/gallery_import` into `gallery_entries`, which is what `/api/history`
    # serves. Stated as a summary because the previous artifact could be read as "16 gallery images
    # were dropped" — true about brand-scoped Assets, false about the gallery, and read at the one
    # moment an operator is deciding whether to continue.
    plan["gallery_summary"] = {
        "source_logical_entries": len(plan["history"]),
        "gallery_entries_migrated_by": "api.gallery_import (gallery_blobs + gallery_entries)",
        "gallery_entries_dropped": 0,
        "brand_scoped_assets_created": sum(
            1 for row in plan["history"] if row["asset_disposition"] == "MIGRATE_MANAGED_ASSET"),
        "not_brand_scoped_assets": sum(
            1 for row in plan["history"] if row["asset_disposition"] == "NOT_A_BRAND_SCOPED_ASSET"),
        "note": ("quarantine entries whose locator is webapp/history.json#/N describe the ASSET "
                 "disposition only. No gallery image is dropped by this plan."),
    }

    swot_runs = {(row["domain_key"], row["kind"]): row for row in plan["runs"]}
    for domain, brand in sorted(active_brands.items()):
        if domain not in brand_ids:
            continue
        if brand.get("swot_approved") is True:
            run = swot_runs.get((domain, "swot"))
            if run is None:
                quarantine.append(_quarantine(
                    f"webapp/brands/{domain}.json#/swot_approved",
                    "UNREPRESENTABLE_SOURCE_FACT", domain_key=domain,
                    explanation="true SWOT approval has no structurally valid current SWOT subject",
                    continue_with_ack=False,
                ))
            else:
                plan["approvals"].append({
                    "id": str(deterministic_uuid("approval", run["id"], "approved")),
                    "brand_id": brand_ids[domain], "domain_key": domain,
                    "subject_run_id": run["id"], "subject_kind": "swot",
                    "decision": "approved", "decided_by_user_id": None,
                    "decided_at": None, "origin": "legacy_import", "note": None,
                    "source_locator": f"webapp/brands/{domain}.json#/swot_approved",
                    "migration_classification": "MIGRATE_AUTHORITATIVE_APPROVAL_FACT",
                    "provenance": "legacy_boolean_true_actor_and_time_unknown",
                })

        if "owner_inputs" in brand and isinstance(brand.get("owner_inputs"), dict):
            plan["owner_inputs"].append({
                "brand_id": brand_ids[domain], "domain_key": domain,
                "payload": brand["owner_inputs"], "schema_version": 1,
                "source_locator": f"webapp/brands/{domain}.json#/owner_inputs",
                "migration_classification": "MIGRATE_AUTHORITATIVE_CURRENT_STATE",
                "provenance": "legacy_owner_input_current_state",
            })

    for domain, path in sorted(active_registry.items()):
        if domain not in brand_ids:
            continue
        registry = json.loads(path.read_text(encoding="utf-8"))
        entries = registry.get("entries") if isinstance(registry, dict) else None
        if not isinstance(entries, list):
            quarantine.append(_quarantine(
                f"registry/{path.name}", "UNREPRESENTABLE_SOURCE_FACT", domain_key=domain,
                explanation="active competitor registry does not contain an entries list"))
            continue
        keys = [str(e.get("key") or "") for e in entries if isinstance(e, dict)]
        duplicates = {key for key, count in Counter(keys).items() if key and count > 1}
        for index, entry in enumerate(entries):
            locator = f"registry/{path.name}#/{index}"
            if not isinstance(entry, dict) or not str(entry.get("key") or ""):
                quarantine.append(_quarantine(
                    locator, "UNREPRESENTABLE_SOURCE_FACT", domain_key=domain,
                    explanation="competitor registry entry has no stable key"))
                continue
            key = str(entry["key"])
            if key in duplicates:
                quarantine.append(_quarantine(
                    locator, "UNREPRESENTABLE_SOURCE_FACT", domain_key=domain,
                    explanation="competitor registry contains a duplicate stable key"))
                continue
            if entry.get("status", "approved") == "removed":
                continue
            plan["competitors"].append({
                "id": str(deterministic_uuid("competitor", domain, key)),
                "brand_id": brand_ids[domain], "domain_key": domain, "key": key,
                "payload": entry,
                "source_created_at": entry.get("added_at") or entry.get("first_seen") or None,
                "source_locator": locator,
                "migration_classification": "MIGRATE_AUTHORITATIVE_RELATIONSHIP",
                "provenance": "legacy_approved_competitor_registry_relationship",
            })

    ignored_classes = {
        NON_AUTHORITATIVE_BACKUP, IGNORED_BY_POLICY, ORPHAN_UNREFERENCED,
        CONFLICTING_BRAND_ONLY,
    }
    plan["ignored_files"] = [
        {"path": row["path"], "classification": row["classification"],
         "size": row["size"], "sha256": row["sha256"]}
        for row in manifest["files"] if row["classification"] in ignored_classes
    ]

    for key in (
        "users", "brands", "profiles", "runs", "calendar_posts", "attempts", "assets",
        "approvals", "owner_inputs", "competitors", "history", "quarantine", "ignored_files",
    ):
        plan[key].sort(key=lambda row: (
            str(row.get("source_locator") or row.get("path") or ""),
            str(row.get("id") or row.get("quarantine_id") or ""),
        ))

    brand_file_rows = [row for row in manifest["files"]
                       if row["path"].startswith("webapp/brands/")]
    generated_rows_all = [row for row in manifest["files"]
                          if row["path"].startswith("webapp/generated/")]
    migrated_history = sum(h["classification"] != "QUARANTINED" for h in plan["history"])
    brand_non_authoritative = sum(
        row["classification"] == NON_AUTHORITATIVE_BACKUP for row in brand_file_rows)
    brand_ignored = sum(
        row["classification"] not in {ACTIVE, CONFLICTING_OWNER, NON_AUTHORITATIVE_BACKUP}
        for row in brand_file_rows)
    brand_quarantined = (
        len(brand_file_rows) - len(plan["brands"]) - brand_non_authoritative - brand_ignored)
    plan["reconciliation"] = {
        "users": {"source": len(users), "migrated": len(plan["users"]),
                  "quarantined": len(users) - len(plan["users"]),
                  "non_authoritative": 0, "ignored_by_policy": 0},
        "brand_files": {"source": len(brand_file_rows), "migrated": len(plan["brands"]),
                        "quarantined": brand_quarantined,
                        "non_authoritative": brand_non_authoritative,
                        "ignored_by_policy": brand_ignored},
        "profiles": {"source": len(active_brands), "migrated": len(plan["profiles"]),
                     "quarantined": len(active_brands) - len(plan["profiles"]),
                     "non_authoritative": 0, "ignored_by_policy": 0},
        "output_blocks": {"source": output_source_count, "migrated": len(plan["runs"]),
                          "quarantined": output_source_count - len(plan["runs"]),
                          "non_authoritative": 0, "ignored_by_policy": 0},
        "calendar_items": {"source": sum(len(((b.get("content_calendar") or {}).get("calendar")
                                               or [])) for b in active_brands.values()
                                              if isinstance(b.get("content_calendar"), dict)),
                           "migrated": len(plan["calendar_posts"]),
                           "quarantined": sum(1 for q in quarantine
                                              if "/content_calendar/calendar/" in
                                              q["source_locator"]),
                           "non_authoritative": 0, "ignored_by_policy": 0},
        "owner_inputs": {"source": len(plan["owner_inputs"]),
                         "migrated": len(plan["owner_inputs"]), "quarantined": 0,
                         "non_authoritative": 0, "ignored_by_policy": 0},
        "approvals": {"source": sum(b.get("swot_approved") is True
                                     for b in active_brands.values()),
                      "migrated": len(plan["approvals"]),
                      "quarantined": sum(1 for q in quarantine
                                         if q["source_locator"].endswith("/swot_approved")),
                      "non_authoritative": 0, "ignored_by_policy": 0},
        "history_entries": {"source": len(history), "migrated": migrated_history,
                            "quarantined": len(history) - migrated_history,
                            "non_authoritative": 0, "ignored_by_policy": 0},
        "generated_files": {
            "source": len(generated_rows_all),
            "migrated": sum(r["classification"] == ACTIVE_BRAND_REFERENCED
                            for r in generated_rows_all),
            "quarantined": 0,
            "non_authoritative": sum(r["classification"] == CONFLICTING_BRAND_ONLY
                                     for r in generated_rows_all),
            "ignored_by_policy": sum(r["classification"] == ORPHAN_UNREFERENCED
                                     for r in generated_rows_all),
        },
        "logical_managed_assets": {
            "source": len(history) + sum(r["classification"] == ACTIVE_BRAND_REFERENCED
                                         for r in generated_rows_all),
            "migrated": len(plan["assets"]),
            "quarantined": len(history) - migrated_history,
            "non_authoritative": 0, "ignored_by_policy": 0,
        },
    }
    plan["byte_reconciliation"] = {
        "source_referenced_bytes_count": len(history) + sum(
            r["classification"] == ACTIVE_BRAND_REFERENCED for r in generated_rows_all),
        "source_referenced_byte_sum": sum(int(h.get("bytes") or 0) for h in plan["history"])
        + sum(int(a["bytes"]) for a in plan["assets"] if a["source_representation"] == "local_file"),
        "planned_logical_asset_count": len(plan["assets"]),
        "planned_physical_blob_count": len(plan["assets"]),
        "planned_byte_sum": sum(int(a["bytes"]) for a in plan["assets"]),
        "quarantined_or_missing_count": len(history) - migrated_history,
        "quarantined_or_missing_byte_sum": sum(
            int(h.get("bytes") or 0) for h in plan["history"]
            if h["classification"] == "QUARANTINED"),
        "sha256_mapping": [{"source_locator": a["source_locator"],
                            "storage_key": a["storage_key"], "sha256": a["checksum_sha256"],
                            "bytes": a["bytes"]} for a in plan["assets"]],
    }

    plan["plan_digest"] = calculate_plan_digest(plan)
    quarantine_manifest = quarantine_manifest_from_plan(plan)
    plan["quarantine_manifest_digest"] = quarantine_manifest["quarantine_manifest_digest"]
    return plan, manifest, quarantine_manifest


__all__ = ["build_migration_plan", "observed_mime"]
