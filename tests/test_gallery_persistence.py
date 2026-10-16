# -*- coding: utf-8 -*-
"""The gallery's two halves: physical bytes and logical publications (§2, §4, §5).

The split is the design, and the measured corpus is why it exists: 28 logical entries carry 25
distinct checksums, so five of them are the same image published again. One table keyed by content
would have forced a choice between losing five real events and storing the bytes five times. These
tests hold that line from both directions — the same bytes must NOT create a second blob, and the
same bytes must ALWAYS create a second entry.

The other half is ordering. `gallery_store` makes bytes durable, fsyncs, atomically renames and
READS BACK to verify the digest before anything relational claims they exist. The two failure
directions are deliberately asymmetric and both are asserted: an orphan file is acceptable, a row
pointing at absent bytes is not.
"""
from __future__ import annotations

import hashlib
import os
import subprocess
import sys
import types
import uuid
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

pytestmark = pytest.mark.skipif(
    not _DSN, reason="BASEERA_TEST_DATABASE_URL is required for the gallery persistence proof")

#: A real PNG header, so mime sniffing has something true to find.
_PNG = b"\x89PNG\r\n\x1a\n" + b"gallery-fixture-" + b"\x00" * 32
_PNG_OTHER = b"\x89PNG\r\n\x1a\n" + b"a-different-one-" + b"\x00" * 32
_JPEG = b"\xff\xd8\xff\xe0" + b"jpeg-fixture" + b"\x00" * 16


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
    monkeypatch.setenv("BASEERA_WEBAPP_DIR", str(tmp_path / "webapp"))
    monkeypatch.setenv(ENV_BACKEND, POSTGRES)
    monkeypatch.setenv("BASEERA_DATABASE_URL", _DSN)
    from db.session import dispose
    dispose()
    reset_persistence()
    from api.persistence import get_persistence
    yield get_persistence()._service
    dispose()
    reset_persistence()


@pytest.fixture()
def user(service):
    from api.user_auth import hash_password

    return service.create_user(email=f"gp{uuid.uuid4().hex[:8]}@test.local",
                               hashed_password=hash_password("password123"), full_name="GP")


def _store(payload: bytes):
    from api import gallery_store
    return gallery_store.store(payload)


def _record(service, payload: bytes, *, filename: str, **kwargs):
    stored = _store(payload)
    return service.record_gallery_entry(
        checksum=stored.checksum, byte_size=stored.byte_size, mime=stored.mime,
        storage_key=stored.storage_key, filename=filename, **kwargs)


# =====================================================================
# §2 — byte storage
# =====================================================================

def test_stored_bytes_are_content_addressed_and_verified(tmp_path, monkeypatch):
    monkeypatch.setenv("BASEERA_WEBAPP_DIR", str(tmp_path / "webapp"))
    from api import gallery_store

    stored = gallery_store.store(_PNG)
    assert stored.checksum == hashlib.sha256(_PNG).hexdigest()
    assert stored.byte_size == len(_PNG)
    assert stored.mime == "image/png"
    assert stored.written is True
    assert gallery_store.path_for(stored.storage_key).is_file()
    assert gallery_store.load(stored.storage_key) == _PNG


def test_storing_the_same_bytes_twice_writes_one_file(tmp_path, monkeypatch):
    """Idempotent BY CONSTRUCTION, not by a check the caller has to remember."""
    monkeypatch.setenv("BASEERA_WEBAPP_DIR", str(tmp_path / "webapp"))
    from api import gallery_store

    first = gallery_store.store(_PNG)
    second = gallery_store.store(_PNG)
    assert first.storage_key == second.storage_key
    assert first.written is True and second.written is False
    files = list((tmp_path / "webapp" / "gallery").rglob("*.png"))
    assert len(files) == 1, files


def test_a_truncated_earlier_write_is_rewritten_not_trusted(tmp_path, monkeypatch):
    """Content-addressing says these bytes ARE at this key. That is verified, not assumed —
    a half-written file would otherwise be trusted forever because its NAME is right."""
    monkeypatch.setenv("BASEERA_WEBAPP_DIR", str(tmp_path / "webapp"))
    from api import gallery_store

    stored = gallery_store.store(_PNG)
    target = gallery_store.path_for(stored.storage_key)
    target.write_bytes(_PNG[:4])                       # simulate a torn write

    again = gallery_store.store(_PNG)
    assert again.written is True, "a corrupt file at the right key was trusted"
    assert gallery_store.load(stored.storage_key) == _PNG


def test_a_source_claiming_the_wrong_checksum_is_refused(tmp_path, monkeypatch):
    monkeypatch.setenv("BASEERA_WEBAPP_DIR", str(tmp_path / "webapp"))
    from api.gallery_store import GalleryBytesError, store

    with pytest.raises(GalleryBytesError) as caught:
        store(_PNG, expected_checksum="0" * 64)
    assert "mismatch" in str(caught.value)
    assert not list((tmp_path / "webapp" / "gallery").rglob("*")), "bytes were stored anyway"


def test_empty_bytes_are_refused(tmp_path, monkeypatch):
    monkeypatch.setenv("BASEERA_WEBAPP_DIR", str(tmp_path / "webapp"))
    from api.gallery_store import GalleryBytesError, store

    with pytest.raises(GalleryBytesError):
        store(b"")


def test_a_storage_key_cannot_escape_the_managed_root(tmp_path, monkeypatch):
    """`storage_key` comes out of the DATABASE, and a database value that can name
    `../../secrets` is a path traversal with extra steps."""
    monkeypatch.setenv("BASEERA_WEBAPP_DIR", str(tmp_path / "webapp"))
    from api.gallery_store import GalleryBytesError, path_for

    for hostile in ("gallery/../../escape.png", "../escape.png", "generated/legacy.png"):
        with pytest.raises(GalleryBytesError):
            path_for(hostile)


def test_mime_comes_from_the_bytes_not_from_content_type(tmp_path, monkeypatch):
    """The legacy index records `content_type` as the POST type ("post"/"story"), which merely
    LOOKS like a media type. Sniffing is the only honest source."""
    monkeypatch.setenv("BASEERA_WEBAPP_DIR", str(tmp_path / "webapp"))
    from api.gallery_store import store

    assert store(_PNG).mime == "image/png"
    assert store(_JPEG).mime == "image/jpeg"
    assert store(b"not an image at all, really").mime == "application/octet-stream"


def test_no_partial_file_survives_a_failed_write(tmp_path, monkeypatch):
    """A byte-write fault leaves nothing at the destination — the DB must never be offered a key
    that points at a half-file."""
    monkeypatch.setenv("BASEERA_WEBAPP_DIR", str(tmp_path / "webapp"))
    from api import gallery_store

    real_replace = os.replace

    def exploding_replace(src, dst):
        raise OSError("disk full")

    monkeypatch.setattr(gallery_store.os, "replace", exploding_replace)
    with pytest.raises(gallery_store.GalleryBytesError):
        gallery_store.store(_PNG)
    monkeypatch.setattr(gallery_store.os, "replace", real_replace)

    root = tmp_path / "webapp" / "gallery"
    leftovers = [p for p in root.rglob("*") if p.is_file()]
    assert leftovers == [], f"a partial file survived a failed write: {leftovers}"


# =====================================================================
# §5 — physical dedup, logical preservation
# =====================================================================

def test_identical_bytes_reuse_one_blob_and_still_make_two_entries(service, user):
    """The measured corpus in miniature, and the reason the tables are split."""
    blob_a, entry_a = _record(service, _PNG, filename="first.png", owner_user_id=user.id)
    blob_b, entry_b = _record(service, _PNG, filename="second.png", owner_user_id=user.id)

    assert blob_a.checksum == blob_b.checksum, "the same bytes made two blobs"
    assert entry_a.id != entry_b.id, "two publications collapsed into one entry"
    assert entry_a.checksum == entry_b.checksum

    history = service.gallery_history(user_id=user.id, limit=50)
    names = [e.filename for e in history]
    assert "first.png" in names and "second.png" in names


def test_five_entries_over_two_blobs_all_survive(service, user):
    """The exact shape the forensic pass measured: 5 logical entries, 2 physical blobs.

    Scoped to THIS run's filenames. The disposable database is shared across the whole suite and
    the unattributed historical entries are visible to everyone by design, so a bare count here
    would measure the neighbours.
    """
    tag = uuid.uuid4().hex[:8]
    for index in range(3):
        _record(service, _PNG, filename=f"{tag}-a{index}.png", owner_user_id=user.id)
    for index in range(2):
        _record(service, _PNG_OTHER, filename=f"{tag}-b{index}.png", owner_user_id=user.id)

    mine = [e for e in service.gallery_history(user_id=user.id, limit=500)
            if e.filename.startswith(tag)]
    assert len(mine) == 5, [e.filename for e in mine]
    assert len({e.checksum for e in mine}) == 2


def test_different_bytes_never_share_a_blob(service, user):
    blob_a, _ = _record(service, _PNG, filename="a.png", owner_user_id=user.id)
    blob_b, _ = _record(service, _PNG_OTHER, filename="b.png", owner_user_id=user.id)
    assert blob_a.checksum != blob_b.checksum
    assert blob_a.storage_key != blob_b.storage_key


# =====================================================================
# §15 — who sees what
# =====================================================================

def test_history_shows_mine_plus_the_unattributed_and_never_a_strangers(service, user):
    from api.user_auth import hash_password

    stranger = service.create_user(email=f"gs{uuid.uuid4().hex[:8]}@test.local",
                                   hashed_password=hash_password("password123"), full_name="GS")
    _record(service, _PNG, filename="mine.png", owner_user_id=user.id)
    _record(service, _PNG_OTHER, filename="theirs.png", owner_user_id=stranger.id)
    _record(service, _JPEG, filename="nobodys.png", owner_user_id=None,
            origin="legacy_import", legacy_seq=90_001,
            imported_at=__import__("datetime").datetime.now(
                __import__("datetime").timezone.utc))

    mine = {e.filename for e in service.gallery_history(user_id=user.id, limit=50)}
    assert "mine.png" in mine
    assert "nobodys.png" in mine, "an unattributed historical entry vanished from the gallery"
    assert "theirs.png" not in mine, "another account's private history leaked"

    theirs = {e.filename for e in service.gallery_history(user_id=stranger.id, limit=50)}
    assert "theirs.png" in theirs and "mine.png" not in theirs
    assert "nobodys.png" in theirs, "the unattributed entries are not one account's property"


def test_reading_history_never_assigns_an_owner(service, user):
    """A read must not create provenance. The unattributed entries stay unattributed however
    often somebody looks at them."""
    import datetime as _dt

    _record(service, _PNG, filename="orphan.png", owner_user_id=None, origin="legacy_import",
            legacy_seq=90_002, imported_at=_dt.datetime.now(_dt.timezone.utc))
    for _ in range(3):
        service.gallery_history(user_id=user.id, limit=50)

    found = [e for e in service.gallery_history(user_id=user.id, limit=50)
             if e.filename == "orphan.png"]
    assert found and found[0].owner_user_id is None


# =====================================================================
# §16 — history survives an archived brand
# =====================================================================

def test_archiving_a_brand_leaves_its_gallery_history_intact(service, user):
    """§16: archival is a FLAG, and `ON DELETE RESTRICT` is what makes that true of evidence too.

    A gallery entry is what the owner actually published. Cascading it away because the active
    brand was archived would delete the record of work that really happened, to tidy a list.
    """
    from db.services import ProfileData, ScrapeSuccess

    domain = f"ga{uuid.uuid4().hex[:8]}_test"
    job = service.create_user_job(owner_user_id=user.id, requested_domain_key=domain)
    service.claim_job(job_id=job.id, worker_id=str(job.id), lease_seconds=600)
    committed = service.commit_scrape_success(ScrapeSuccess(
        domain_key=domain, user_id=user.id, job_id=job.id, worker_id=str(job.id),
        profile=ProfileData(payload={"name": {"value": "GA"}}, schema_version=1),
        display_name="GA", source_url=f"https://{domain}.test/"))

    _blob, entry = _record(service, _PNG, filename="published.png", owner_user_id=user.id,
                           brand_id=committed.brand.id, domain_key=domain)
    assert service.archive_brand(domain_key=domain, user_id=user.id) is True

    surviving = [e for e in service.gallery_history(user_id=user.id, limit=50)
                 if e.id == entry.id]
    assert surviving, "archiving a brand destroyed its published gallery history"
    assert surviving[0].brand_id == committed.brand.id
    assert service.gallery_blob(checksum=entry.checksum) is not None


# =====================================================================
# the repositories say no to what they must not do
# =====================================================================

def test_the_gallery_repositories_expose_no_generic_mutation():
    """§4: no patch, no mutate, no arbitrary JSON update. A blob is identified BY its bytes, so
    'change this blob' is not an operation that means anything."""
    from db.repositories import GalleryBlobRepository, GalleryEntryRepository

    for repository in (GalleryBlobRepository, GalleryEntryRepository):
        exposed = {name for name in dir(repository) if not name.startswith("_")}
        forbidden = exposed & {"update", "patch", "mutate", "set", "upsert", "replace",
                               "delete", "save"}
        assert not forbidden, f"{repository.__name__} exposes {sorted(forbidden)}"


def test_no_route_can_reach_a_unit_of_work_or_sqlalchemy_through_the_gallery_surface():
    """§4/§9: the provider protocol stays domain-shaped."""
    import inspect

    from api.persistence import PersistenceProvider

    for name in ("record_gallery_entry", "gallery_history", "supports_gallery_writes"):
        signature = str(inspect.signature(getattr(PersistenceProvider, name)))
        for leak in ("uow", "UnitOfWork", "Session", "session", "Repository"):
            assert leak not in signature, f"{name} exposes {leak}: {signature}"
