# -*- coding: utf-8 -*-
"""The Gallery byte store against a REMOTE object store, and the refusals that keep it honest.

WHAT THIS FILE PROVES, AND WHAT IT DOES NOT. The rehearsal proved the gallery's byte-first ordering
against a managed LOCAL directory. Production moves those bytes to Supabase Storage, and a proof
measured on a filesystem does not automatically cover a network. This file exercises the REAL
`SupabaseObjectStore` request construction, status handling and failure semantics over an injected
HTTP transport, so the contract and every refusal are measured rather than assumed.

It does NOT prove the live service. Latency, SSL, bucket policy, RLS, quota and the real API's exact
responses are only knowable against a configured project, and those proofs are listed in the runbook
as prerequisites for the actual cutover. Nothing here should be read as "Supabase is proven".

THE ONE INVARIANT EVERY TEST SERVES: physical bytes durable and verified BEFORE any relational row
claims they exist. Bytes without a row is a recoverable orphan; a row without bytes is a gallery
entry that renders broken forever and cannot be told apart from a real one.
"""
from __future__ import annotations

import hashlib

import pytest

httpx = pytest.importorskip("httpx")

PNG = b"\x89PNG\r\n\x1a\n" + b"remote-gallery-bytes" * 8
OTHER = b"\x89PNG\r\n\x1a\n" + b"a-different-image" * 8

URL = "https://project-ref.supabase.co"
KEY = "service-role-key-value-that-must-never-be-logged"
BUCKET = "baseera-gallery"

_PREFIX = f"/storage/v1/object/{BUCKET}/"
_INFO_PREFIX = f"/storage/v1/object/info/{BUCKET}/"

#: MEASURED against the live service on 2026-08-22, and deliberately not what the status suggests.
#: A missing OBJECT answers HTTP 400 — not 404 — carrying `code: NoSuchKey`. A missing or
#: unauthorised BUCKET answers HTTP 400 too, carrying `code: NoSuchBucket`, so that a private bucket
#: never confirms its own existence to an anonymous caller. The status alone cannot separate them.
NO_SUCH_KEY = {"statusCode": "404", "error": "not_found",
               "message": "Object not found", "code": "NoSuchKey"}
NO_SUCH_BUCKET = {"statusCode": "404", "error": "Bucket not found",
                  "message": "Bucket not found", "code": "NoSuchBucket"}


def _bucket_transport(objects: dict, *, put_status: int = 200, raise_on: str = "",
                      serve: bytes | None = None, deny: bool = False):
    """A fake Supabase Storage bucket: a dict reached over HTTP, answering the way the real one does.

    `objects` is shared deliberately — two stores built over the same dict are two processes
    reaching one remote bucket, which is the property the multi-machine requirement turns on.

    `deny=True` reproduces a wrong credential: every route answers `NoSuchBucket`, which must be
    told apart from absence rather than read as "the bytes are gone".
    """
    def handler(request: httpx.Request) -> httpx.Response:
        if raise_on and raise_on == request.method:
            raise httpx.ConnectError("simulated network failure", request=request)
        if deny:
            return httpx.Response(400, json=NO_SUCH_BUCKET)

        path = request.url.path
        if path.startswith(_INFO_PREFIX):
            key = path[len(_INFO_PREFIX):]
            if key not in objects:
                return httpx.Response(400, json=NO_SUCH_KEY)
            return httpx.Response(200, json={"name": key, "size": len(objects[key])})

        assert path.startswith(_PREFIX), f"unexpected path {path!r}"
        key = path[len(_PREFIX):]

        if request.method == "POST":
            if put_status >= 300:
                return httpx.Response(put_status, json={"code": "AccessDenied"})
            objects[key] = bytes(request.content)
            return httpx.Response(200, json={"Key": key})

        if request.method in ("GET", "HEAD"):
            if key not in objects:
                # HEAD carries no body on the real service; GET carries the discriminating code.
                return httpx.Response(400, json=NO_SUCH_KEY) if request.method == "GET" \
                    else httpx.Response(400)
            body = serve if serve is not None else objects[key]
            return httpx.Response(200, content=(b"" if request.method == "HEAD" else body))

        return httpx.Response(405)
    return httpx.MockTransport(handler)


@pytest.fixture
def remote(monkeypatch, tmp_path):
    """Select the remote backend by CONFIGURATION, and inject only the HTTP transport.

    Selection, credential checks and store construction all run for real; the socket is the single
    thing replaced. `BASEERA_WEBAPP_DIR` points at an empty temp directory so any stray filesystem
    write becomes visible instead of landing in the developer's real managed root.
    """
    from api import blob_store

    monkeypatch.setenv("BASEERA_WEBAPP_DIR", str(tmp_path / "webapp"))
    monkeypatch.setenv(blob_store.ENV_BACKEND, blob_store.BACKEND_SUPABASE)
    monkeypatch.setenv(blob_store.ENV_SUPABASE_URL, URL)
    monkeypatch.setenv(blob_store.ENV_SUPABASE_KEY, KEY)
    monkeypatch.setenv(blob_store.ENV_SUPABASE_BUCKET, BUCKET)

    objects: dict[str, bytes] = {}
    real = blob_store.SupabaseObjectStore

    def install(**kwargs):
        transport = _bucket_transport(objects, **kwargs)

        def factory(*a, **k):
            k.setdefault("transport", transport)
            return real(*a, **k)
        monkeypatch.setattr(blob_store, "SupabaseObjectStore", factory)
        return objects

    install()
    return type("Remote", (), {"objects": objects, "install": staticmethod(install),
                               "webapp": tmp_path / "webapp"})


# =====================================================================
# the contract
# =====================================================================

def test_upload_read_back_and_checksum_identity(remote):
    """Store, then read back through the same key, and prove the bytes are the ones claimed."""
    from api import gallery_store

    blob = gallery_store.store(PNG)

    assert blob.checksum == hashlib.sha256(PNG).hexdigest()
    assert blob.byte_size == len(PNG)
    assert blob.mime == "image/png"
    assert blob.written is True
    # Content-addressed key, fanned out — the same shape the local store produces, because the key
    # is a domain decision and not a backend one.
    assert blob.storage_key == f"gallery/{blob.checksum[:2]}/{blob.checksum}.png"
    assert remote.objects[blob.storage_key] == PNG

    loaded = gallery_store.load(blob.storage_key)
    assert loaded == PNG
    assert hashlib.sha256(loaded).hexdigest() == blob.checksum


def test_identical_bytes_reuse_the_one_remote_object(remote):
    """A re-import must not produce a second copy, and must SAY it did not write."""
    from api import gallery_store

    first = gallery_store.store(PNG)
    second = gallery_store.store(PNG)

    assert first.storage_key == second.storage_key
    assert first.written is True and second.written is False
    assert len(remote.objects) == 1


def test_two_logical_entries_share_one_physical_object(remote):
    """28 logical / 25 physical is a real property of the corpus: logical history must never
    collapse just because two publications carry identical bytes."""
    from api import gallery_store

    a = gallery_store.store(PNG)
    b = gallery_store.store(PNG)
    c = gallery_store.store(OTHER)

    assert a.storage_key == b.storage_key != c.storage_key
    assert len(remote.objects) == 2          # two bodies
    # Both logical entries resolve to real bytes; neither is lost to the dedup.
    assert gallery_store.load(a.storage_key) == PNG
    assert gallery_store.load(b.storage_key) == PNG
    assert gallery_store.load(c.storage_key) == OTHER


def test_retry_after_an_orphan_object_is_idempotent(remote):
    """A crash between upload and commit leaves an orphan. The retry must adopt it, not duplicate
    it — content addressing is what makes that safe, and it is verified rather than assumed."""
    from api import gallery_store

    orphan_key = f"gallery/{hashlib.sha256(PNG).hexdigest()[:2]}/{hashlib.sha256(PNG).hexdigest()}.png"
    remote.objects[orphan_key] = PNG          # the previous run's upload, with no row behind it

    blob = gallery_store.store(PNG)

    assert blob.storage_key == orphan_key
    assert blob.written is False              # reused, not rewritten
    assert len(remote.objects) == 1


def test_a_second_process_sees_the_same_objects(remote):
    """The multi-machine requirement, at the byte layer: nothing about a stored object lives in the
    process that stored it. A store built fresh — a different process, a different machine — reads
    what the first one wrote, with no volume copy and no directory sync."""
    from api import blob_store, gallery_store

    blob = gallery_store.store(PNG)

    second_process = blob_store.object_store_for(gallery_store.ROOT_NAME)
    assert second_process is not None
    assert second_process.get(blob.storage_key) == PNG
    assert second_process.exists(blob.storage_key) is True


# =====================================================================
# the refusals
# =====================================================================

def test_nothing_touches_the_filesystem_under_the_remote_backend(remote):
    """The point of the whole exercise. A production process that quietly wrote gallery bytes to a
    local disk would look healthy and would be exactly the machine-local authority this removes."""
    from api import gallery_store

    gallery_store.store(PNG)
    gallery_store.store(OTHER)

    stray = [p for p in remote.webapp.rglob("*") if p.is_file()]
    assert stray == [], f"the remote backend wrote to the filesystem: {stray}"
    assert len(remote.objects) == 2


def test_a_failed_upload_never_reports_success(remote):
    """A refused upload must raise. The caller's next line opens a transaction, and a row written
    after a silent failure is the broken-image-forever case."""
    from api import gallery_store

    objects = remote.install(put_status=500)

    with pytest.raises(gallery_store.GalleryBytesError):
        gallery_store.store(PNG)
    assert objects == {}


def test_bytes_that_do_not_survive_the_round_trip_are_refused(remote):
    """The read-back is not ceremony. A store that accepts an upload and serves back something else
    fails HERE, before any relational row claims those bytes exist."""
    from api import gallery_store

    remote.install(serve=b"\x89PNG\r\n\x1a\n" + b"corrupted-on-the-way-back")

    with pytest.raises(gallery_store.GalleryBytesError, match="did not survive"):
        gallery_store.store(PNG)


def test_absence_answers_none_but_an_outage_raises(remote):
    """Absence and outage are different answers. Reporting a network blip as "no bytes" would render
    every entry as permanently broken during a fault that resolves itself."""
    from api import blob_store, gallery_store

    missing = f"gallery/aa/{'a' * 64}.png"
    assert gallery_store.load(missing) is None          # a real 404

    remote.install(raise_on="GET")
    with pytest.raises(blob_store.GalleryBytesError):
        gallery_store.load(missing)


def test_a_wrong_credential_is_never_read_as_absence(remote):
    """The sharpest edge the live service exposed.

    A missing object and a wrong credential BOTH answer HTTP 400. Only the JSON `code` separates
    `NoSuchKey` from `NoSuchBucket`. If the store keyed off the status it would report a credential
    fault as "the bytes are gone" — on every entry at once — and an operator would go looking for a
    lost byte store instead of a bad key.
    """
    from api import blob_store, gallery_store

    remote.install(deny=True)
    store = blob_store.object_store_for("gallery")
    key = f"gallery/aa/{'a' * 64}.png"

    with pytest.raises(blob_store.GalleryBytesError, match="NoSuchBucket"):
        gallery_store.load(key)
    with pytest.raises(blob_store.GalleryBytesError, match="NoSuchBucket"):
        store.exists(key)
    with pytest.raises(blob_store.GalleryBytesError):
        gallery_store.store(PNG)


def test_absence_uses_the_service_code_not_the_status(remote):
    """A genuinely missing object answers 400/NoSuchKey and MUST read as absence, not as an error —
    otherwise a history page fails outright instead of listing the entry without its bytes."""
    from api import blob_store, gallery_store

    store = blob_store.object_store_for("gallery")
    missing = f"gallery/bb/{'b' * 64}.png"

    assert gallery_store.load(missing) is None
    assert store.exists(missing) is False

    stored = gallery_store.store(PNG)
    assert store.exists(stored.storage_key) is True


def test_a_traversing_key_cannot_escape_the_bucket(remote):
    """A storage_key comes out of the database; one that can be made to name another bucket prefix
    is a traversal with extra steps. The check is the same shape for a URL as for a path."""
    from api import blob_store

    store = blob_store.object_store_for("gallery")
    for bad in ("../secrets/key.png", "/etc/passwd", "generated/x.png", ""):
        with pytest.raises(blob_store.GalleryBytesError):
            store.put(bad, PNG, mime="image/png")
    assert remote.objects == {}


def test_the_remote_backend_never_falls_back_to_local(monkeypatch, tmp_path):
    """Selecting `supabase` with incomplete configuration must FAIL, not degrade. A fallback here
    would put production bytes on whichever machine happened to run the process."""
    from api import blob_store

    monkeypatch.setenv("BASEERA_WEBAPP_DIR", str(tmp_path / "webapp"))
    monkeypatch.setenv(blob_store.ENV_BACKEND, blob_store.BACKEND_SUPABASE)
    monkeypatch.delenv(blob_store.ENV_SUPABASE_URL, raising=False)
    monkeypatch.delenv(blob_store.ENV_SUPABASE_KEY, raising=False)

    with pytest.raises(blob_store.BlobStoreNotConfigured) as seen:
        blob_store.object_store_for("gallery")
    assert blob_store.ENV_SUPABASE_URL in str(seen.value)

    monkeypatch.setenv(blob_store.ENV_SUPABASE_URL, URL)
    with pytest.raises(blob_store.BlobStoreNotConfigured):
        blob_store.object_store_for("gallery")          # key still missing


def test_an_unknown_backend_name_is_refused_not_guessed(monkeypatch, tmp_path):
    """A typo in a deployment variable is how a production process ends up writing to a laptop."""
    from api import blob_store

    monkeypatch.setenv("BASEERA_WEBAPP_DIR", str(tmp_path / "webapp"))
    monkeypatch.setenv(blob_store.ENV_BACKEND, "supabse")
    with pytest.raises(blob_store.BlobStoreNotConfigured):
        blob_store.object_store_for("gallery")


def test_the_local_backend_remains_the_default(monkeypatch, tmp_path):
    """The DEFAULT must be the one that cannot reach a shared production resource by accident."""
    from api import blob_store

    monkeypatch.setenv("BASEERA_WEBAPP_DIR", str(tmp_path / "webapp"))
    monkeypatch.delenv(blob_store.ENV_BACKEND, raising=False)

    assert blob_store.selected_backend() == blob_store.BACKEND_LOCAL
    assert isinstance(blob_store.object_store_for("gallery"),
                      blob_store.LocalFilesystemObjectStore)


def test_the_two_backends_are_observationally_identical(monkeypatch, tmp_path):
    """PARITY, measured rather than asserted.

    The local store is what the rehearsal proved; the remote store is what production will run. If
    the two disagree about identity, key, mime, dedup or the bytes that come back, then the
    rehearsal's evidence does not transfer and the cutover would be standing on a different system
    than the one that was tested. So the same sequence runs against both and the observations must
    match exactly.
    """
    from api import blob_store, gallery_store

    def observe():
        first = gallery_store.store(PNG)
        second = gallery_store.store(PNG)          # identical bytes: must dedup
        third = gallery_store.store(OTHER)
        return {
            "key": first.storage_key,
            "checksum": first.checksum,
            "mime": first.mime,
            "byte_size": first.byte_size,
            "written_first": first.written,
            "written_second": second.written,
            "second_key": second.storage_key,
            "other_key": third.storage_key,
            "bytes": gallery_store.load(first.storage_key),
            "other_bytes": gallery_store.load(third.storage_key),
            "absent": gallery_store.load(f"gallery/aa/{'a' * 64}.png"),
        }

    monkeypatch.setenv("BASEERA_WEBAPP_DIR", str(tmp_path / "local"))
    monkeypatch.delenv(blob_store.ENV_BACKEND, raising=False)
    local = observe()

    objects: dict[str, bytes] = {}
    transport = _bucket_transport(objects)
    real = blob_store.SupabaseObjectStore
    monkeypatch.setattr(blob_store, "SupabaseObjectStore",
                        lambda *a, **k: real(*a, **{**k, "transport": transport}))
    monkeypatch.setenv("BASEERA_WEBAPP_DIR", str(tmp_path / "remote"))
    monkeypatch.setenv(blob_store.ENV_BACKEND, blob_store.BACKEND_SUPABASE)
    monkeypatch.setenv(blob_store.ENV_SUPABASE_URL, URL)
    monkeypatch.setenv(blob_store.ENV_SUPABASE_KEY, KEY)
    monkeypatch.setenv(blob_store.ENV_SUPABASE_BUCKET, BUCKET)
    remote_observation = observe()

    assert local == remote_observation
    # …and the ONE thing that must differ: where the bytes physically ended up.
    assert [p for p in (tmp_path / "local").rglob("*") if p.is_file()] != []
    assert [p for p in (tmp_path / "remote").rglob("*") if p.is_file()] == []
    assert len(objects) == 2


def test_the_service_role_credential_never_reaches_an_error_message(remote):
    """Errors get pasted into tickets. The bucket is safe to name; the key that bypasses row-level
    security is not, in any failure path."""
    from api import blob_store, gallery_store

    remote.install(put_status=403)
    with pytest.raises(gallery_store.GalleryBytesError) as denied:
        gallery_store.store(PNG)
    assert KEY not in str(denied.value)
    assert BUCKET in str(denied.value)

    remote.install(raise_on="GET")
    with pytest.raises(blob_store.GalleryBytesError) as outage:
        gallery_store.load(f"gallery/aa/{'a' * 64}.png")
    assert KEY not in str(outage.value)
