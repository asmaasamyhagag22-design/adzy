# -*- coding: utf-8 -*-
"""Import the legacy `history.json` gallery into `gallery_blobs` + `gallery_entries`.

READ-ONLY AT THE SOURCE. This module opens the source file and never writes to it, never renames
it, never truncates it. Callers are expected to point it at a COPY anyway; the read-only property
is enforced here as well so a mistaken path cannot damage the 93 MB original that holds the ONLY
copy of every one of these images.

WHAT THE SOURCE ACTUALLY IS. `record_history` stores each image as `image_b64` INSIDE the index, so
the index IS the image store. Extraction therefore decodes bytes, makes them durable in the managed
store, and only then records what they were — never the reverse (§2).

DETERMINISTIC IDENTITY. `legacy_seq` is the entry's POSITION in the source list, and a partial
UNIQUE index makes a second import of the same position impossible rather than merely unlikely. Two
runs over the same file produce the same rows; there is no clock, no random id, and no "did I
already do this" heuristic.

WHAT IS NEVER INVENTED
----------------------
- `owner_user_id` is set ONLY when the source names a legacy owner AND a user row already carries
  that `legacy_user_id`. Six of the 28 measured entries name no owner; they import with NULL and
  the legacy id, when present but unmatched, is preserved in metadata so nothing is lost.
- `brand_id` is ALWAYS NULL. The source records a `domain_key` STRING; that a Brand row happens to
  hold the same string today does not make it the brand this image was made for. `domain_key` is
  preserved as written.
- `created_at` comes from the source's own timestamp and nothing else. Not file mtime, not import
  time, not the filename. An entry with no usable timestamp keeps the column's default and says so
  in metadata, which is a different statement from a date that was made up.
- `is_preview` / `trust_violation` appear on 23 of 28 entries. Absent stays absent: a render that
  was not recorded as a preview is not the same claim as one recorded as not a preview.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Optional

from . import gallery_store

logger = logging.getLogger(__name__)

ORIGIN = "legacy_import"

#: Keys promoted to columns. Everything else in a source entry becomes metadata verbatim.
_COLUMNS = frozenset({
    "image_b64", "filename", "brand_name", "domain_key", "content_type", "style",
    "created_at", "owner_id", "unclaimed",
})


@dataclass
class ImportReport:
    """What actually happened, counted rather than assumed."""
    source: str = ""
    source_sha256: str = ""
    entries_in_source: int = 0
    entries_created: int = 0
    entries_already_present: int = 0
    blobs_written: int = 0
    blobs_reused: int = 0
    distinct_checksums: int = 0
    owners_resolved: int = 0
    owners_unresolved: int = 0
    timestamps_preserved: int = 0
    timestamps_absent: int = 0
    failures: list[dict] = field(default_factory=list)

    def as_dict(self) -> dict:
        data = {k: v for k, v in self.__dict__.items()}
        return data


class GalleryImportError(RuntimeError):
    """The source could not be read as a gallery index."""


def read_source(path: Path | str) -> tuple[list[dict], str]:
    """The source entries and the file's own SHA-256, read once, without modifying anything.

    The digest is returned so a caller can prove afterwards that the source it read is the source
    it started with — the byte-identity requirement is checkable rather than promised.
    """
    target = Path(path)
    try:
        raw = target.read_bytes()
    except OSError as exc:
        raise GalleryImportError(f"cannot read the gallery source: {exc}") from exc
    digest = hashlib.sha256(raw).hexdigest()
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise GalleryImportError(f"the gallery source is not valid JSON: {exc}") from exc
    if not isinstance(data, list):
        raise GalleryImportError("the gallery source is not a list of entries")
    return [item for item in data if isinstance(item, dict)], digest


def _timestamp(entry: Mapping[str, Any]) -> Optional[datetime]:
    """The source's own creation time, or None. Never a substitute.

    `record_history` writes `int(time.time())`. Anything else — a string date, a float, a missing
    key — is returned as None rather than coerced, because a coerced date is indistinguishable
    afterwards from a recorded one.
    """
    raw = entry.get("created_at")
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        return None
    try:
        return datetime.fromtimestamp(float(raw), tz=timezone.utc)
    except (OverflowError, OSError, ValueError):
        return None


def _metadata(entry: Mapping[str, Any], *, legacy_owner_id: str,
              owner_resolved: bool, timestamp: Optional[datetime]) -> dict:
    """Source metadata verbatim, plus what the import itself could not resolve.

    The provenance notes are additive and explicit. Recording "this owner string matched no
    account" is what keeps an unresolved owner different from an absent one — a distinction that
    disappears the moment both become a NULL column and nothing else.
    """
    out = {key: value for key, value in entry.items() if key not in _COLUMNS}
    if legacy_owner_id:
        out["legacy_owner_id"] = legacy_owner_id
        if not owner_resolved:
            out["legacy_owner_unresolved"] = True
    if timestamp is None:
        out["legacy_created_at_absent"] = True
    return out


def import_entries(service: Any, source: Path | str, *,
                   report: Optional[ImportReport] = None,
                   stop_after: int = 0) -> ImportReport:
    """Import every logical entry. Idempotent, deterministic, and safe to resume.

    `stop_after` exists for the interruption proof: it stops cleanly after N entries so a second
    run has to finish the rest without duplicating the first N.

    One entry's failure does not abort the others. A corrupt base64 body or a byte-write fault is
    recorded in `report.failures` and the run continues, because the alternative — one bad entry
    losing the other 27 — is the outcome the failure tests exist to prevent.
    """
    entries, digest = read_source(source)
    out = report or ImportReport()
    out.source = str(source)
    out.source_sha256 = digest
    out.entries_in_source = len(entries)
    checksums: set[str] = set()

    for position, entry in enumerate(entries):
        if stop_after and out.entries_created + out.entries_already_present >= stop_after:
            break
        try:
            _import_one(service, entry, position=position, report=out, checksums=checksums)
        except Exception as exc:  # noqa: BLE001 — one bad entry must not lose the rest
            # CLASSIFIED BY RE-READING, not by exception type. A concurrent importer that reached
            # this position first IS the idempotency guarantee firing, and the partial UNIQUE index
            # is what raised — but naming the storage exception here would drag the db layer into
            # this module, and the whole cutover depends on that layer having exactly one seam.
            # Asking the service what is actually there answers the same question without it.
            landed = service.gallery_entry_for_legacy_seq(legacy_seq=position)
            if landed is not None:
                out.entries_already_present += 1
                checksums.add(landed.checksum)
                continue
            logger.exception("gallery import failed at position %d: %s", position, exc)
            out.failures.append({"legacy_seq": position, "error": type(exc).__name__,
                                 "detail": str(exc)[:300]})
    out.distinct_checksums = len(checksums)
    return out


def _import_one(service: Any, entry: Mapping[str, Any], *, position: int,
                report: ImportReport, checksums: set[str]) -> None:
    existing = service.gallery_entry_for_legacy_seq(legacy_seq=position)
    if existing is not None:
        report.entries_already_present += 1
        checksums.add(existing.checksum)
        return

    raw = str(entry.get("image_b64") or "")
    if not raw:
        raise GalleryImportError(f"entry {position} carries no image bytes")
    try:
        payload = base64.b64decode(raw, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise GalleryImportError(f"entry {position} has invalid base64: {exc}") from exc

    # BYTES FIRST. Only after this returns may anything relational say they exist.
    stored = gallery_store.store(payload)
    checksums.add(stored.checksum)
    if stored.written:
        report.blobs_written += 1
    else:
        report.blobs_reused += 1

    legacy_owner_id = str(entry.get("owner_id") or "")
    owner_user_id = None
    if legacy_owner_id:
        matched = service.user_by_legacy_id(legacy_owner_id)
        owner_user_id = matched.id if matched is not None else None
    if owner_user_id is not None:
        report.owners_resolved += 1
    else:
        report.owners_unresolved += 1

    stamp = _timestamp(entry)
    if stamp is not None:
        report.timestamps_preserved += 1
    else:
        report.timestamps_absent += 1

    service.record_gallery_entry(
        checksum=stored.checksum, byte_size=stored.byte_size, mime=stored.mime,
        storage_key=stored.storage_key,
        filename=str(entry.get("filename") or f"{stored.checksum[:16]}.png"),
        owner_user_id=owner_user_id,
        # NEVER inferred from the domain string — see the module docstring.
        brand_id=None,
        domain_key=str(entry.get("domain_key") or "") or None,
        brand_name=str(entry.get("brand_name") or "") or None,
        content_type=str(entry.get("content_type") or "") or None,
        style=str(entry.get("style") or "") or None,
        metadata=_metadata(entry, legacy_owner_id=legacy_owner_id,
                           owner_resolved=owner_user_id is not None, timestamp=stamp),
        origin=ORIGIN, legacy_seq=position,
        imported_at=datetime.now(timezone.utc),
        created_at=stamp)
    report.entries_created += 1


def main(argv: Optional[list[str]] = None) -> int:
    """`python -m api.gallery_import <copy-of-history.json>` — operator-run, never automatic.

    An import that ran itself on startup would be a migration nobody chose, against a file this
    module deliberately treats as read-only evidence.
    """
    import argparse

    from .persistence import get_persistence

    parser = argparse.ArgumentParser(description="Import a legacy gallery index into PostgreSQL.")
    parser.add_argument("source", help="path to a COPY of history.json")
    parser.add_argument("--stop-after", type=int, default=0,
                        help="stop cleanly after N entries (for resume rehearsals)")
    args = parser.parse_args(argv)

    provider = get_persistence()
    service = getattr(provider, "_service", None)
    if service is None:
        print("the selected backend has no durable gallery; set PERSISTENCE_BACKEND=postgres")
        return 2
    report = import_entries(service, args.source, stop_after=args.stop_after)
    print(json.dumps(report.as_dict(), indent=2, ensure_ascii=False))
    return 1 if report.failures else 0


if __name__ == "__main__":  # pragma: no cover - operator entry point
    raise SystemExit(main())


__all__ = ["ORIGIN", "GalleryImportError", "ImportReport", "read_source", "import_entries", "main"]
