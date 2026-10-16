# -*- coding: utf-8 -*-
"""The managed byte store behind `gallery_blobs`. Content-addressed, byte-first, verified.

WHY BYTES LEFT THE INDEX. The legacy gallery keeps each image as `image_b64` INSIDE
`history.json` — the index IS the image store, which is why that file is 93 MB and why every
`/api/history` call reads all of it. `gallery_blobs` splits the two: a row records WHICH bytes and
WHERE, and the bytes live here.

THE ORDERING IS THE WHOLE POINT (§2). Bytes are durable BEFORE any relational row claims they
exist:

    put the bytes through the configured object store, durably
      -> READ BACK and verify the SHA-256
      -> only then may the caller open a transaction

The two failure directions are deliberately NOT symmetric. Bytes stored with no row is an orphan:
recoverable, identifiable, costs storage. A row with no bytes is a gallery entry that renders as a
broken image forever and cannot be told apart from a real one. So the acceptable failure is chosen
in advance rather than discovered.

CONTENT-ADDRESSED, so this is idempotent by construction: the same bytes written twice are the same
object at the same key, and a re-import cannot produce a second copy. Storage keys are RELATIVE
(`gallery/ab/<sha256>.png`), so moving the managed root — or moving it to a different KIND of store
— does not invalidate every row in the table.

WHERE THE BYTES PHYSICALLY LAND is `api/blob_store.py`'s business, not this module's. A local
directory serves tests and rehearsals; Supabase Storage serves production, because a directory on
one machine cannot be the byte authority for a project opened from several. Everything that makes a
blob CORRECT — identity, mime, key, read-back, idempotence, the `written` flag — stays here, in one
implementation, so the two backends cannot drift into disagreeing about what a blob is.
"""
from __future__ import annotations

import hashlib
import os                      # noqa: F401  (kept importable: `os.replace` is a monkeypatch seam)
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .blob_store import GalleryBytesError, LocalFilesystemObjectStore, object_store_for
from .user_auth import webapp_dir

#: The managed root, relative to `webapp_dir()`. Deliberately NOT `generated/`: that directory is
#: the frozen legacy `/api/generated/{name}` archive of unattributed files, which §3 keeps as
#: backend-independent byte serving. Mixing the two would make an orphan indistinguishable from a
#: managed blob.
ROOT_NAME = "gallery"

#: Magic-number sniffing. The legacy index records `content_type` as the POST type ("post",
#: "story") — not a MIME type — so the media type has to come from the bytes themselves rather than
#: from a field that looks like it but means something else.
_MAGIC: tuple[tuple[bytes, str, str], ...] = (
    (b"\x89PNG\r\n\x1a\n", "image/png", "png"),
    (b"\xff\xd8\xff", "image/jpeg", "jpg"),
    (b"GIF87a", "image/gif", "gif"),
    (b"GIF89a", "image/gif", "gif"),
    (b"%PDF-", "application/pdf", "pdf"),
)
_DEFAULT = ("application/octet-stream", "bin")


@dataclass(frozen=True, slots=True)
class StoredBlob:
    checksum: str
    byte_size: int
    mime: str
    storage_key: str
    #: True when this call actually wrote the object; False when identical bytes were already there.
    #: Reported rather than inferred, so a caller counting imports counts writes and not luck.
    written: bool


def sniff(payload: bytes) -> tuple[str, str]:
    """`(mime, extension)` from the bytes. Unknown answers octet-stream rather than guessing PNG."""
    if payload[:4] == b"RIFF" and payload[8:12] == b"WEBP":
        return "image/webp", "webp"
    for magic, mime, ext in _MAGIC:
        if payload.startswith(magic):
            return mime, ext
    return _DEFAULT


def root() -> Path:
    """The managed directory, when the configured backend HAS one.

    Meaningful for the filesystem backend only. Kept because rehearsal tooling and the backup step
    need to name the managed root; a remote deployment backs up the bucket instead.
    """
    return webapp_dir() / ROOT_NAME


def storage_key_for(checksum: str, extension: str) -> str:
    """`gallery/ab/<sha256>.<ext>` — a two-character fan-out so one directory never holds them all."""
    digest = str(checksum)
    return f"{ROOT_NAME}/{digest[:2]}/{digest}.{extension}"


def path_for(storage_key: str) -> Path:
    """Resolve a stored key under the LOCAL managed root, refusing anything that escapes it.

    Deliberately always filesystem: this answers "where would this key live on disk", which is a
    question about the local store and about backups. Byte access goes through `store`/`load`, which
    honour the configured backend. A `storage_key` comes out of the database, and a database value
    that can be made to name `../../etc/passwd` is a path traversal with extra steps.
    """
    return LocalFilesystemObjectStore(ROOT_NAME).path_for(storage_key)


def store(payload: bytes, *, expected_checksum: str = "") -> StoredBlob:
    """Make `payload` durable and return its identity. Raises rather than half-succeeding.

    `expected_checksum` is for the import path, where the source claims a digest: a mismatch is a
    corrupt source and must stop the entry, not silently store whatever arrived.
    """
    if not payload:
        raise GalleryBytesError("refusing to store an empty gallery blob")
    checksum = hashlib.sha256(payload).hexdigest()
    if expected_checksum and expected_checksum != checksum:
        raise GalleryBytesError(
            f"checksum mismatch: source claimed {expected_checksum}, bytes hash to {checksum}")

    mime, extension = sniff(payload)
    key = storage_key_for(checksum, extension)
    backend = object_store_for(ROOT_NAME)

    # Content-addressed, so an object already at this key IS these bytes — but that is VERIFIED
    # rather than assumed, because a truncated earlier write would otherwise be trusted forever.
    existing = backend.get(key)
    if existing is not None and hashlib.sha256(existing).hexdigest() == checksum:
        return StoredBlob(checksum, len(payload), mime, key, written=False)

    backend.put(key, payload, mime=mime)

    # READ BACK. The point of the whole ordering is that the caller may now claim these bytes
    # exist, so "exist" is measured off the store rather than off the fact that the write did not
    # raise. A store that accepted the bytes into a buffer and lost them fails HERE, before any row.
    verified = backend.get(key)
    if verified is None or hashlib.sha256(verified).hexdigest() != checksum:
        raise GalleryBytesError("gallery bytes did not survive the round trip")
    return StoredBlob(checksum, len(payload), mime, key, written=True)


def load(storage_key: str) -> Optional[bytes]:
    """The stored bytes, or None when the object is gone. Absence is an answer, not an exception —
    a caller listing history must be able to say "this entry's bytes are missing" rather than fail
    the whole page.

    A TRANSPORT failure is not absence and is not swallowed: reporting a remote outage as "no bytes"
    would render every entry as permanently broken during a blip that resolves itself.
    """
    return object_store_for(ROOT_NAME).get(storage_key)


__all__ = ["GalleryBytesError", "StoredBlob", "ROOT_NAME", "sniff", "root",
           "storage_key_for", "path_for", "store", "load"]
