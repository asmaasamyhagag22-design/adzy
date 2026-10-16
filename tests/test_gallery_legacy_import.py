# -*- coding: utf-8 -*-
"""Importing the legacy gallery: deterministic, idempotent, and honest about what it cannot know.

§6, §7, §8. The corpus is the real one — a read-only COPY of the authoritative 93 MB
`history.json`, whose SHA-256 is checked before and after every run in this file. If that digest
ever moves, an "import" mutated the only copy of 28 images and the whole suite should stop.

THE THREE THINGS THAT MUST NEVER BE INVENTED, each with its own test:

  owner      — 22 entries name a legacy owner, 6 name none. An owner is set ONLY when a user row
               already carries that `legacy_user_id`; otherwise NULL, with the legacy string kept
               in metadata and flagged unresolved. An unresolved owner and an absent owner are
               different facts and stay different.
  brand      — NEVER set. The source records a domain STRING; that a Brand holds that string today
               does not make it the brand this image was made for.
  created_at — the source's own epoch, or nothing. Not mtime, not import time, not the filename.

And the sparse metadata: `is_preview`/`trust_violation` are on 23 of 28. Missing stays missing,
because "not recorded as a preview" is a different claim from "recorded as not a preview".
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import types
import uuid
from datetime import datetime, timezone
from pathlib import Path

_fake = types.ModuleType("playwright")
_fake_sync = types.ModuleType("playwright.sync_api")


class _Stub: ...


for _n in ("Browser", "BrowserContext", "Page", "sync_playwright"):
    setattr(_fake_sync, _n, _Stub)
_fake_sync.TimeoutError = type("TimeoutError", (Exception,), {})
_fake_sync.Error = type("Error", (Exception,), {})
sys.modules.setdefault("playwright", _fake)
sys.modules.setdefault("playwright.sync_api", _fake_sync)

import pytest  # noqa: E402

from api.persistence import ENV_BACKEND, POSTGRES, reset_persistence  # noqa: E402

_ROOT = Path(__file__).resolve().parents[1]
_DSN = os.environ.get("BASEERA_TEST_DATABASE_URL", "")

#: The authoritative corpus. Read-only here, always: it holds the ONLY copy of every one of these
#: images, so the tests work from a copy and verify the original's digest either side.
_AUTHORITATIVE = _ROOT / "outputs" / "webapp" / "history.json"

pytestmark = pytest.mark.skipif(
    not _DSN, reason="BASEERA_TEST_DATABASE_URL is required for the gallery import proof")

#: Measured from the real corpus on 2026-08-18 and re-measured by `test_the_corpus_is_what_the_
#: audit_measured`. Written down so a change in the source fails loudly rather than silently
#: changing what "complete" means.
EXPECTED_ENTRIES = 28
EXPECTED_CHECKSUMS = 25
EXPECTED_SHARED_BLOBS = 2
EXPECTED_SHARED_ENTRIES = 5
EXPECTED_UNOWNED = 6
EXPECTED_SPARSE_PREVIEW = 23

_PNG = b"\x89PNG\r\n\x1a\n" + b"import-fixture--" + b"\x00" * 32
_PNG_TWIN = _PNG


def _migrate() -> None:
    inherited = os.environ.get("PYTHONPATH", "")
    pythonpath = os.pathsep.join(p for p in (inherited, str(_ROOT)) if p)
    proc = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=str(_ROOT), capture_output=True, text=True, timeout=300,
        env={**os.environ, "PYTHONPATH": pythonpath, "BASEERA_DATABASE_URL": _DSN})
    assert proc.returncode == 0, f"could not provision the database:\n{proc.stderr[-2000:]}"


@pytest.fixture()
def service(monkeypatch, tmp_path):
    pytest.importorskip("sqlalchemy", reason="SQLAlchemy is not installed")
    _migrate()
    monkeypatch.setenv("BASEERA_WEBAPP_DIR", str(tmp_path / "w"))
    monkeypatch.setenv(ENV_BACKEND, POSTGRES)
    monkeypatch.setenv("BASEERA_DATABASE_URL", _DSN)
    from db.session import dispose
    dispose()
    reset_persistence()
    from api.persistence import get_persistence
    yield get_persistence()._service
    dispose()
    reset_persistence()


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture()
def corpus(tmp_path):
    """A read-only COPY of the real index, with the original's digest asserted either side."""
    if not _AUTHORITATIVE.is_file():
        pytest.skip("the authoritative legacy gallery index is not present on this machine")
    before = _digest(_AUTHORITATIVE)
    copy = tmp_path / "history_copy.json"
    copy.write_bytes(_AUTHORITATIVE.read_bytes())
    yield copy
    assert _digest(_AUTHORITATIVE) == before, (
        "the AUTHORITATIVE history.json changed during this test — it holds the only copy of "
        "every one of these images and must never be written")


def _synthetic(tmp_path, entries: list[dict]) -> Path:
    target = tmp_path / f"synthetic_{uuid.uuid4().hex[:8]}.json"
    target.write_text(json.dumps(entries), encoding="utf-8")
    return target


def _b64(payload: bytes) -> str:
    import base64
    return base64.b64encode(payload).decode("ascii")


def _imported(service) -> list:
    """Every legacy_import entry, in source order. Owner-independent: unattributed entries are
    visible to everyone, which is the contract, and the owned ones here have no live owner."""
    from sqlalchemy import select

    from db.models import GalleryEntry
    from db.session import get_session_factory

    with get_session_factory()() as session:
        return list(session.scalars(
            select(GalleryEntry).where(GalleryEntry.origin == "legacy_import")
            .order_by(GalleryEntry.legacy_seq)).all())


def _clear(service) -> None:
    from sqlalchemy import delete

    from db.models import GalleryBlob, GalleryEntry
    from db.session import get_session_factory

    with get_session_factory()() as session:
        session.execute(delete(GalleryEntry))
        session.execute(delete(GalleryBlob))
        session.commit()


# =====================================================================
# the corpus itself
# =====================================================================

def test_the_corpus_is_what_the_audit_measured(corpus):
    """Re-measured from the exact backup, as §"DATA POLICY" requires — not taken on trust."""
    import base64

    entries = json.loads(corpus.read_text(encoding="utf-8"))
    assert len(entries) == EXPECTED_ENTRIES

    sums = [hashlib.sha256(base64.b64decode(e["image_b64"])).hexdigest() for e in entries]
    assert len(set(sums)) == EXPECTED_CHECKSUMS

    from collections import Counter
    shared = {digest: count for digest, count in Counter(sums).items() if count > 1}
    assert len(shared) == EXPECTED_SHARED_BLOBS
    assert sum(shared.values()) == EXPECTED_SHARED_ENTRIES

    assert sum(1 for e in entries if not e.get("owner_id")) == EXPECTED_UNOWNED
    assert sum(1 for e in entries if "is_preview" in e) == EXPECTED_SPARSE_PREVIEW


# =====================================================================
# §6 — the import itself
# =====================================================================

def test_every_logical_entry_is_imported_over_the_deduplicated_blobs(service, corpus):
    from api.gallery_import import import_entries

    _clear(service)
    report = import_entries(service, corpus)

    assert report.failures == [], report.failures
    assert report.entries_in_source == EXPECTED_ENTRIES
    assert report.entries_created == EXPECTED_ENTRIES, "a logical publication was lost"
    assert report.distinct_checksums == EXPECTED_CHECKSUMS
    assert report.blobs_written == EXPECTED_CHECKSUMS
    assert report.blobs_reused == EXPECTED_ENTRIES - EXPECTED_CHECKSUMS

    rows = _imported(service)
    assert len(rows) == EXPECTED_ENTRIES
    assert [r.legacy_seq for r in rows] == list(range(EXPECTED_ENTRIES))

    from collections import Counter
    shared = {c: n for c, n in Counter(r.checksum for r in rows).items() if n > 1}
    assert len(shared) == EXPECTED_SHARED_BLOBS
    assert sum(shared.values()) == EXPECTED_SHARED_ENTRIES, (
        "the entries that share bytes did not all survive")


def test_every_imported_entry_points_at_bytes_that_exist(service, corpus):
    """The invariant the whole ordering exists for: no row may name absent bytes."""
    from api import gallery_store
    from api.gallery_import import import_entries

    _clear(service)
    import_entries(service, corpus)
    for row in _imported(service):
        blob = service.gallery_blob(checksum=row.checksum)
        assert blob is not None, f"entry {row.legacy_seq} names an unknown blob"
        payload = gallery_store.load(blob.storage_key)
        assert payload is not None, f"entry {row.legacy_seq} points at bytes that are not there"
        assert hashlib.sha256(payload).hexdigest() == row.checksum
        assert len(payload) == blob.byte_size


def test_unknown_provenance_stays_unknown(service, corpus):
    """Six entries name no owner; none names a Brand. Neither is guessed."""
    from api.gallery_import import import_entries

    _clear(service)
    import_entries(service, corpus)
    rows = _imported(service)

    assert all(r.brand_id is None for r in rows), "a brand_id was inferred from a domain string"
    assert all(r.domain_key for r in rows), "the domain string itself was dropped"

    unowned = [r for r in rows if "legacy_owner_id" not in r.metadata_json]
    assert len(unowned) == EXPECTED_UNOWNED
    assert all(r.owner_user_id is None for r in unowned)

    # The 22 that DO name an owner keep the string even when no account matches — an unresolved
    # owner and an absent one must stay tellable apart.
    named = [r for r in rows if "legacy_owner_id" in r.metadata_json]
    assert len(named) == EXPECTED_ENTRIES - EXPECTED_UNOWNED
    assert all(r.metadata_json.get("legacy_owner_unresolved") for r in named)
    assert all(r.owner_user_id is None for r in named)


def test_an_owner_is_attached_only_when_an_account_actually_carries_that_legacy_id(service,
                                                                                   corpus):
    """The other half of the policy: 'only if explicitly known' has to RESOLVE when it is known,
    or the rule is indistinguishable from 'never'."""
    from api.gallery_import import import_entries
    from api.user_auth import hash_password

    entries = json.loads(corpus.read_text(encoding="utf-8"))
    legacy_id = next(str(e["owner_id"]) for e in entries if e.get("owner_id"))
    account = service.create_user(email=f"gi{uuid.uuid4().hex[:8]}@test.local",
                                  hashed_password=hash_password("password123"),
                                  full_name="GI", legacy_user_id=legacy_id)

    _clear(service)
    report = import_entries(service, corpus)
    assert report.owners_resolved > 0, "a legacy owner with a matching account stayed NULL"

    rows = _imported(service)
    resolved = [r for r in rows if r.owner_user_id is not None]
    assert resolved and all(r.owner_user_id == account.id for r in resolved)
    assert all(not r.metadata_json.get("legacy_owner_unresolved") for r in resolved)


def test_historical_timestamps_come_from_the_source_and_nothing_else(service, corpus):
    from api.gallery_import import import_entries

    entries = json.loads(corpus.read_text(encoding="utf-8"))
    _clear(service)
    import_entries(service, corpus)

    for row in _imported(service):
        source_epoch = entries[row.legacy_seq]["created_at"]
        assert int(row.created_at.timestamp()) == int(source_epoch)
        assert row.imported_at is not None
        assert row.imported_at != row.created_at, (
            "created_at was filled from the import clock")


def test_a_source_with_no_usable_timestamp_says_so_instead_of_inventing_one(service, tmp_path):
    from api.gallery_import import import_entries

    _clear(service)
    source = _synthetic(tmp_path, [
        {"image_b64": _b64(_PNG), "filename": "no-date.png", "domain_key": "d_com"},
    ])
    report = import_entries(service, source)
    assert report.timestamps_absent == 1 and report.timestamps_preserved == 0

    row = _imported(service)[0]
    assert row.metadata_json.get("legacy_created_at_absent") is True


def test_sparse_metadata_stays_sparse(service, corpus):
    """`is_preview` on 23 of 28. Filling the other five with False would invent a disclosure."""
    from api.gallery_import import import_entries

    _clear(service)
    import_entries(service, corpus)
    rows = _imported(service)

    with_preview = [r for r in rows if "is_preview" in r.metadata_json]
    assert len(with_preview) == EXPECTED_SPARSE_PREVIEW
    assert all("trust_violation" not in r.metadata_json
               for r in rows if "is_preview" not in r.metadata_json)


# =====================================================================
# §7 — idempotency
# =====================================================================

def test_a_second_identical_import_creates_nothing(service, corpus):
    from api.gallery_import import import_entries

    _clear(service)
    first = import_entries(service, corpus)
    before = [(r.id, r.legacy_seq, r.checksum, r.owner_user_id, dict(r.metadata_json),
               r.created_at) for r in _imported(service)]

    second = import_entries(service, corpus)
    assert second.entries_created == 0
    assert second.entries_already_present == EXPECTED_ENTRIES
    assert second.blobs_written == 0

    after = [(r.id, r.legacy_seq, r.checksum, r.owner_user_id, dict(r.metadata_json),
              r.created_at) for r in _imported(service)]
    assert after == before, "a retry drifted the rows it should have left alone"
    assert first.entries_created == EXPECTED_ENTRIES


def test_a_run_interrupted_halfway_is_finished_by_the_next_one(service, corpus):
    """§8: 28 entries, stopped at 12, resumed. No duplicate, no lost entry."""
    from api.gallery_import import import_entries

    _clear(service)
    partial = import_entries(service, corpus, stop_after=12)
    assert partial.entries_created == 12
    assert len(_imported(service)) == 12

    finished = import_entries(service, corpus)
    assert finished.entries_created == EXPECTED_ENTRIES - 12
    assert finished.entries_already_present == 12

    rows = _imported(service)
    assert len(rows) == EXPECTED_ENTRIES
    assert [r.legacy_seq for r in rows] == list(range(EXPECTED_ENTRIES))


def test_the_unique_index_refuses_a_second_entry_for_one_source_position(service, tmp_path):
    """Deterministic identity enforced by the SCHEMA, not by the importer remembering."""
    from db.errors import ConcurrencyConflict

    _clear(service)
    from api import gallery_store

    stored = gallery_store.store(_PNG)
    common = dict(checksum=stored.checksum, byte_size=stored.byte_size, mime=stored.mime,
                  storage_key=stored.storage_key, origin="legacy_import", legacy_seq=4242,
                  imported_at=datetime.now(timezone.utc))
    service.record_gallery_entry(filename="one.png", **common)
    with pytest.raises(ConcurrencyConflict):
        service.record_gallery_entry(filename="two.png", **common)


# =====================================================================
# §8 — fault injection
# =====================================================================

def test_a_corrupt_base64_entry_is_recorded_and_the_rest_still_import(service, tmp_path):
    from api.gallery_import import import_entries

    _clear(service)
    source = _synthetic(tmp_path, [
        {"image_b64": _b64(_PNG), "filename": "ok-first.png", "created_at": 1_750_000_000},
        {"image_b64": "!!!not base64!!!", "filename": "corrupt.png", "created_at": 1_750_000_001},
        {"image_b64": _b64(b"\x89PNG\r\n\x1a\n" + b"third" + b"\x00" * 40),
         "filename": "ok-third.png", "created_at": 1_750_000_002},
    ])
    report = import_entries(service, source)

    assert report.entries_created == 2, "one bad entry took the others with it"
    assert len(report.failures) == 1
    assert report.failures[0]["legacy_seq"] == 1
    assert {r.filename for r in _imported(service)} == {"ok-first.png", "ok-third.png"}


def test_an_entry_with_no_bytes_is_a_failure_not_an_empty_publication(service, tmp_path):
    from api.gallery_import import import_entries

    _clear(service)
    source = _synthetic(tmp_path, [{"filename": "bodiless.png", "created_at": 1_750_000_000}])
    report = import_entries(service, source)
    assert report.entries_created == 0 and len(report.failures) == 1
    assert _imported(service) == []


def test_a_byte_write_failure_creates_no_row(service, tmp_path, monkeypatch):
    """§2 in its sharpest form: if the bytes did not land, nothing may claim they did."""
    from api import gallery_import, gallery_store

    _clear(service)
    source = _synthetic(tmp_path, [
        {"image_b64": _b64(_PNG), "filename": "unwritable.png", "created_at": 1_750_000_000}])

    def refuse(*_a, **_k):
        raise gallery_store.GalleryBytesError("the volume is read-only")

    monkeypatch.setattr(gallery_import.gallery_store, "store", refuse)
    report = gallery_import.import_entries(service, source)

    assert report.entries_created == 0
    assert report.failures and report.failures[0]["error"] == "GalleryBytesError"
    assert _imported(service) == []
    assert service.gallery_entry_count() == 0


def test_a_database_failure_after_the_bytes_land_leaves_an_orphan_not_a_dangling_row(
        service, tmp_path, monkeypatch):
    """The chosen failure direction, asserted rather than assumed.

    The bytes are on disk and no row names them: recoverable, identifiable, costs disk. The
    reverse — a row naming bytes that never landed — would render as a broken image forever with
    nothing to tell it apart from a real entry.
    """
    from api import gallery_import, gallery_store

    _clear(service)
    source = _synthetic(tmp_path, [
        {"image_b64": _b64(_PNG), "filename": "orphaned.png", "created_at": 1_750_000_000}])

    real_record = service.record_gallery_entry

    def explode(**_kwargs):
        raise RuntimeError("the transaction was rolled back")

    monkeypatch.setattr(service, "record_gallery_entry", explode)
    report = gallery_import.import_entries(service, source)
    monkeypatch.setattr(service, "record_gallery_entry", real_record)

    assert report.entries_created == 0 and len(report.failures) == 1
    assert _imported(service) == []

    checksum = hashlib.sha256(_PNG).hexdigest()
    orphan = gallery_store.path_for(gallery_store.storage_key_for(checksum, "png"))
    assert orphan.is_file(), "the bytes were not durable before the transaction was attempted"
    assert service.gallery_blob(checksum=checksum) is None, "a blob row survived a failed entry"

    # And the retry finishes the job using the bytes that are already there.
    resumed = gallery_import.import_entries(service, source)
    assert resumed.entries_created == 1
    assert resumed.blobs_reused == 1 and resumed.blobs_written == 0


def test_a_blob_that_exists_without_its_entry_is_completed_by_a_retry(service, tmp_path,
                                                                      monkeypatch):
    """DB failure AFTER the blob row but BEFORE the entry. The transaction is one commit, so this
    is only reachable by injecting inside it — and the retry must still land the publication."""
    from api import gallery_import
    from db import services as db_services

    _clear(service)
    source = _synthetic(tmp_path, [
        {"image_b64": _b64(_PNG), "filename": "half.png", "created_at": 1_750_000_000}])

    original = db_services.PersistenceService.record_gallery_entry

    def fail_between(self, **kwargs):
        with self._uow_factory() as uow:
            uow.gallery_blobs.ensure(
                checksum=kwargs["checksum"], byte_size=kwargs["byte_size"],
                mime=kwargs["mime"], storage_key=kwargs["storage_key"])
            raise RuntimeError("crashed after the blob, before the entry")

    monkeypatch.setattr(db_services.PersistenceService, "record_gallery_entry", fail_between)
    first = gallery_import.import_entries(service, source)
    monkeypatch.setattr(db_services.PersistenceService, "record_gallery_entry", original)

    assert first.entries_created == 0
    # ONE commit means the blob rolled back with the entry — there is no half state to inherit.
    assert service.gallery_blob(checksum=hashlib.sha256(_PNG).hexdigest()) is None

    second = gallery_import.import_entries(service, source)
    assert second.entries_created == 1
    assert len(_imported(service)) == 1


def test_the_import_never_writes_to_its_source(service, corpus):
    """The read-only property, checked on the COPY as well as on the original (the fixture does
    the original). An import that edits its own evidence has destroyed the thing it was proving."""
    from api.gallery_import import import_entries

    before = _digest(corpus)
    _clear(service)
    import_entries(service, corpus)
    import_entries(service, corpus)
    assert _digest(corpus) == before, "the import mutated the file it was reading"


def test_an_unreadable_source_fails_before_touching_the_database(service, tmp_path):
    from api.gallery_import import GalleryImportError, import_entries

    _clear(service)
    missing = tmp_path / "not_here.json"
    with pytest.raises(GalleryImportError):
        import_entries(service, missing)

    not_json = tmp_path / "garbage.json"
    not_json.write_text("{not json at all", encoding="utf-8")
    with pytest.raises(GalleryImportError):
        import_entries(service, not_json)

    wrong_shape = tmp_path / "object.json"
    wrong_shape.write_text('{"history": []}', encoding="utf-8")
    with pytest.raises(GalleryImportError):
        import_entries(service, wrong_shape)

    assert service.gallery_entry_count() == 0
