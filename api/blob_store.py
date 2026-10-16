# -*- coding: utf-8 -*-
"""The object store behind `gallery_blobs` — one contract, two backends.

WHY THIS SEAM EXISTS. `api/gallery_store.py` proved its byte-first ordering against a managed LOCAL
directory. That ordering is a domain rule and does not change here. What changes is WHERE the bytes
land: a directory on one machine cannot be the authority for a project opened from several
machines, so production resolves to Supabase Storage while tests and rehearsals keep the filesystem.

THE SPLIT IS DELIBERATE AND NARROW. Everything that makes a gallery blob correct — the SHA-256
identity, the mime sniff, the storage key, the read-back verification, the idempotence and the
`written` flag — stays in `gallery_store`, in ONE implementation. A backend here is a dumb object
store: put these bytes at this key, give me the bytes at this key, is anything at this key. Copying
the domain rules into each backend is how two stores start disagreeing about what a blob is.

WHAT A BACKEND MUST GUARANTEE.

* `put` returns only when the bytes are DURABLE — accepted-into-a-buffer is not stored. The
  filesystem backend fsyncs and atomically renames; the Supabase backend returns only on a 2xx from
  a completed upload.
* `get` answers `None` for an absent object rather than raising, because a history page must be
  able to say "this entry's bytes are missing" without failing the whole page. A TRANSPORT failure
  is different from absence and does raise — collapsing the two would report an outage as data loss.
* `exists` is a cheap presence probe and is never trusted as proof of CONTENT. The caller still
  reads back and compares the digest, because a truncated earlier write is present and wrong.

NO SILENT FALLBACK. Selecting `supabase` with incomplete configuration raises. It never degrades to
the filesystem: a production process that quietly wrote gallery bytes to a local disk would look
healthy and would be the exact machine-local authority this backend exists to remove.
"""
from __future__ import annotations

import os
import posixpath
import uuid
from pathlib import Path
from typing import Optional, Protocol, runtime_checkable

from .user_auth import webapp_dir

#: Backend selection. `local` is the default because the DEFAULT must be the one that cannot reach
#: a shared production resource by accident — the same rule `PERSISTENCE_BACKEND` follows.
ENV_BACKEND = "BASEERA_BLOB_BACKEND"
BACKEND_LOCAL = "local"
BACKEND_SUPABASE = "supabase"

ENV_SUPABASE_URL = "SUPABASE_URL"
ENV_SUPABASE_KEY = "SUPABASE_SERVICE_ROLE_KEY"
ENV_SUPABASE_BUCKET = "SUPABASE_GALLERY_BUCKET"
ENV_SUPABASE_TIMEOUT = "SUPABASE_STORAGE_TIMEOUT_SECONDS"

#: The bucket is private and server-written. Named here only as the default; the deployment may
#: override it to follow project conventions.
DEFAULT_BUCKET = "baseera-gallery"


class GalleryBytesError(RuntimeError):
    """The bytes could not be made durable, or did not survive the round trip.

    Defined HERE rather than in `gallery_store` so a backend can raise it without importing the
    domain module back — `gallery_store` re-exports it, so every existing caller and every
    `except GalleryBytesError` keeps catching the same class object.
    """


class BlobStoreNotConfigured(GalleryBytesError):
    """A backend was selected by name and its configuration is incomplete.

    A subclass of `GalleryBytesError` on purpose: callers already treat that as "the bytes did not
    become durable", which is exactly what a misconfigured store means for them.
    """


@runtime_checkable
class ObjectStore(Protocol):
    """The whole contract. Three operations, because three is what the gallery actually uses."""

    #: Short, secret-free identity for evidence and error messages. Never contains a key or a DSN.
    backend: str

    def put(self, key: str, payload: bytes, *, mime: str) -> None:
        """Make `payload` durable at `key`. Returns None; raises `GalleryBytesError` on failure."""

    def get(self, key: str) -> Optional[bytes]:
        """The stored bytes, or None when nothing is at `key`. Raises only on transport failure."""

    def exists(self, key: str) -> bool:
        """Whether anything is at `key`. Presence only — never evidence of correct content."""


# ---------------------------------------------------------------------
# key safety — shared, because both backends can be traversed
# ---------------------------------------------------------------------

def safe_key(key: str, *, root_name: str) -> str:
    """Validate a storage key and return it normalised, or refuse it.

    A `storage_key` comes out of the database, and a database value that can be made to name
    `../../etc/passwd` is a path traversal with extra steps. On the filesystem that escapes the
    managed root; against Supabase it escapes the bucket prefix in a URL path. The check is the
    same shape for both, so it lives once, here, rather than at each backend.
    """
    raw = str(key or "")
    if not raw:
        raise GalleryBytesError("empty storage key")
    if raw.startswith("/") or raw.startswith("\\") or (len(raw) > 1 and raw[1] == ":"):
        raise GalleryBytesError(f"storage key must be relative: {key!r}")
    normalised = posixpath.normpath(raw.replace("\\", "/"))
    if normalised.startswith("..") or normalised.startswith("/"):
        raise GalleryBytesError(f"storage key escapes the managed root: {key!r}")
    if not (normalised == root_name or normalised.startswith(root_name + "/")):
        raise GalleryBytesError(f"storage key is outside {root_name!r}: {key!r}")
    return normalised


# ---------------------------------------------------------------------
# filesystem — the proven implementation, unchanged in behaviour
# ---------------------------------------------------------------------

class LocalFilesystemObjectStore:
    """Managed directory under `webapp_dir()`. This is the byte path the rehearsal proved.

    The ordering is preserved verbatim: write a temp file in the destination directory, flush,
    fsync, atomically rename into place, then fsync the directory entry. `os.replace` is atomic on
    both POSIX and Windows, so a reader never observes a half-written object at the real key.
    """

    backend = BACKEND_LOCAL

    def __init__(self, root_name: str) -> None:
        self._root_name = root_name

    def path_for(self, key: str) -> Path:
        """Resolve a stored key under the managed root, refusing anything that escapes it."""
        relative = safe_key(key, root_name=self._root_name)
        base = webapp_dir().resolve()
        candidate = (base / relative).resolve()
        try:
            candidate.relative_to(base / self._root_name)
        except ValueError as exc:      # a symlink or a normalisation quirk got past the string check
            raise GalleryBytesError(f"storage key escapes the managed root: {key!r}") from exc
        return candidate

    def put(self, key: str, payload: bytes, *, mime: str) -> None:
        destination = self.path_for(key)
        destination.parent.mkdir(parents=True, exist_ok=True)
        # SHORT temp name, deliberately. Prefixing the 64-character destination name pushed the
        # staging path past Windows' 260-character limit under a deep managed root, and the failure
        # arrives as a bare ENOENT that reads like a missing directory rather than a too-long name.
        # Unique per process AND per call, so two threads staging identical bytes never collide.
        temporary = destination.with_name(f".{os.getpid():x}{uuid.uuid4().hex[:6]}.part")
        try:
            with open(temporary, "wb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, destination)
            _fsync_directory(destination.parent)
        except OSError as exc:
            raise GalleryBytesError(_write_failure(destination, exc)) from exc
        finally:
            if temporary.exists():
                try:
                    temporary.unlink()
                except OSError:
                    pass

    def get(self, key: str) -> Optional[bytes]:
        try:
            target = self.path_for(key)
        except GalleryBytesError:
            return None
        try:
            return target.read_bytes() if target.is_file() else None
        except OSError:
            return None

    def exists(self, key: str) -> bool:
        try:
            return self.path_for(key).is_file()
        except GalleryBytesError:
            return False


def _write_failure(destination: Path, exc: OSError) -> str:
    """Name the cause when the OS gives an error that reads like a different problem.

    MEASURED: under a deep managed root the 64-character content-addressed filename pushes the path
    past Windows' 260-character limit, and the OS answers "the system cannot find the path
    specified" — which sends a reader looking for a missing directory that is right there. Saying
    which limit was hit is the difference between a five-minute fix and an afternoon.
    """
    detail = f"could not write gallery bytes: {exc}"
    if os.name == "nt" and len(str(destination)) >= 250:
        detail += (f" — the destination path is {len(str(destination))} characters, at or past "
                   f"Windows' 260-character limit; the managed root is too deep")
    return detail


def _fsync_directory(directory: Path) -> None:
    """Durably record the rename itself. Windows has no directory fd, so this is a no-op there —
    stated rather than silently skipped, because `os.replace` is already atomic on both."""
    try:
        fd = os.open(directory, os.O_RDONLY)
    except (OSError, AttributeError):
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


# ---------------------------------------------------------------------
# Supabase Storage — the remote, machine-independent authority
# ---------------------------------------------------------------------

class SupabaseObjectStore:
    """A private Supabase Storage bucket, reached over the Storage REST API with `httpx`.

    NO SDK. The three operations the gallery needs are three HTTP calls, and `httpx` is already a
    dependency. Adding the `supabase` package would pull in a Postgrest client, an auth client and a
    realtime client to move bytes — surface this application does not use and would have to keep
    pinned.

    SERVER-SIDE CREDENTIALS ONLY. The service-role key bypasses row-level security and is read from
    the process environment. It is never returned to a caller, never logged, and never reaches the
    frontend: the browser continues to receive `image_b64` in the history payload exactly as it does
    today, so no signed URL and no public bucket is required to preserve the existing API contract.

    UPSERT IS SAFE HERE, AND ONLY HERE. Keys are content-addressed, so re-writing a key means
    writing identical bytes to the place those bytes already live. That makes a retry after a
    partial failure idempotent instead of a conflict to special-case.
    """

    backend = BACKEND_SUPABASE

    def __init__(self, url: str, service_key: str, bucket: str, *,
                 root_name: str, timeout: float = 30.0, transport=None) -> None:
        if not url or not service_key or not bucket:
            raise BlobStoreNotConfigured(
                f"Supabase blob store needs {ENV_SUPABASE_URL}, {ENV_SUPABASE_KEY} and "
                f"{ENV_SUPABASE_BUCKET}")
        self._base = url.rstrip("/") + "/storage/v1/object"
        self._key = service_key
        self._bucket = bucket
        self._root_name = root_name
        self._timeout = timeout
        # The HTTP transport is injectable so the contract can be exercised without a live project.
        # It is NOT a second backend: the request construction, the status handling and the failure
        # semantics under test are the same code the real client runs.
        self._transport = transport

    # The bucket name is safe to show; the credential is not, and is never part of any message.
    def describe(self) -> str:
        return f"supabase:{self._bucket}"

    def _client(self):
        import httpx
        return httpx.Client(timeout=self._timeout, transport=self._transport, headers={
            "Authorization": f"Bearer {self._key}",
            "apikey": self._key,
        })

    def _url(self, key: str, *, kind: str = "") -> str:
        from urllib.parse import quote
        relative = safe_key(key, root_name=self._root_name)
        base = f"{self._base}/{kind}" if kind else self._base
        return f"{base}/{quote(self._bucket)}/{quote(relative)}"

    @staticmethod
    def _absent(response) -> bool:
        """Whether this response means "no such object" — as opposed to "not allowed to look".

        MEASURED against the live service, and it is not what the status code suggests: a missing
        object answers **HTTP 400**, not 404, with `{"statusCode":"404","error":"not_found",
        "code":"NoSuchKey"}`. A missing or unauthorised BUCKET answers 400 as well, with
        `"code":"NoSuchBucket"` — deliberately, so a private bucket does not confirm its own
        existence to an anonymous caller.

        So the status alone cannot separate the two, and treating every 400 as absence would report
        a wrong credential as "the bytes are gone" — a storage fault disguised as data loss, on
        every entry at once. Only `NoSuchKey` is absence.
        """
        if response.status_code == 404:
            return True
        if response.status_code != 400:
            return False
        try:
            body = response.json()
        except Exception:
            return False
        return str(body.get("code") or "") == "NoSuchKey"

    @staticmethod
    def _fault(response) -> str:
        """The service's own error code, for a message an operator can act on. Never the credential."""
        try:
            body = response.json()
        except Exception:
            return f"HTTP {response.status_code}"
        code = body.get("code") or body.get("error") or ""
        return f"HTTP {response.status_code}" + (f" ({code})" if code else "")

    def put(self, key: str, payload: bytes, *, mime: str) -> None:
        import httpx
        target = self._url(key)
        try:
            with self._client() as client:
                response = client.post(
                    target, content=payload,
                    headers={"Content-Type": mime or "application/octet-stream",
                             "x-upsert": "true"})
        except httpx.HTTPError as exc:
            # The message carries the bucket and the key, never the credential.
            raise GalleryBytesError(
                f"could not upload gallery bytes to {self.describe()}: {exc.__class__.__name__}"
            ) from exc
        if response.status_code >= 300:
            raise GalleryBytesError(
                f"could not upload gallery bytes to {self.describe()}: {self._fault(response)}")

    def get(self, key: str) -> Optional[bytes]:
        import httpx
        try:
            target = self._url(key)
        except GalleryBytesError:
            return None
        try:
            with self._client() as client:
                response = client.get(target)
        except httpx.HTTPError as exc:
            # ABSENCE and OUTAGE are different answers. A network failure reported as "no bytes"
            # would let a history page render an entry as permanently broken during a blip.
            raise GalleryBytesError(
                f"could not read gallery bytes from {self.describe()}: {exc.__class__.__name__}"
            ) from exc
        if self._absent(response):
            return None
        if response.status_code >= 300:
            raise GalleryBytesError(
                f"could not read gallery bytes from {self.describe()}: {self._fault(response)}")
        return response.content

    def exists(self, key: str) -> bool:
        """Presence, via the object INFO endpoint rather than HEAD.

        HEAD answers 400 for a missing object with an EMPTY body, and the body is the only thing
        that separates "no such object" from "no such bucket". A probe that cannot tell those apart
        would report a credential fault as a missing file.
        """
        import httpx
        try:
            target = self._url(key, kind="info")
        except GalleryBytesError:
            return False
        try:
            with self._client() as client:
                response = client.get(target)
        except httpx.HTTPError as exc:
            raise GalleryBytesError(
                f"could not probe gallery bytes in {self.describe()}: {exc.__class__.__name__}"
            ) from exc
        if self._absent(response):
            return False
        if response.status_code >= 300:
            raise GalleryBytesError(
                f"could not probe gallery bytes in {self.describe()}: {self._fault(response)}")
        return True


# ---------------------------------------------------------------------
# selection
# ---------------------------------------------------------------------

def selected_backend() -> str:
    """The configured backend NAME, defaulting to the one that cannot reach production."""
    return (os.environ.get(ENV_BACKEND) or BACKEND_LOCAL).strip().lower() or BACKEND_LOCAL


def object_store_for(root_name: str) -> ObjectStore:
    """Build the configured store. Not cached: the process environment is the configuration, and a
    cached handle would outlive a test that changes it — the same reason `webapp_dir()` re-reads."""
    name = selected_backend()
    if name == BACKEND_LOCAL:
        return LocalFilesystemObjectStore(root_name)
    if name == BACKEND_SUPABASE:
        missing = [v for v in (ENV_SUPABASE_URL, ENV_SUPABASE_KEY)
                   if not (os.environ.get(v) or "").strip()]
        if missing:
            raise BlobStoreNotConfigured(
                f"{ENV_BACKEND}={BACKEND_SUPABASE} but {', '.join(missing)} "
                f"{'is' if len(missing) == 1 else 'are'} not set. "
                f"Refusing to fall back to local storage.")
        try:
            timeout = float(os.environ.get(ENV_SUPABASE_TIMEOUT, "30"))
        except ValueError:
            timeout = 30.0
        return SupabaseObjectStore(
            os.environ[ENV_SUPABASE_URL].strip(),
            os.environ[ENV_SUPABASE_KEY].strip(),
            (os.environ.get(ENV_SUPABASE_BUCKET) or DEFAULT_BUCKET).strip(),
            root_name=root_name, timeout=timeout)
    # An unknown name is a typo in a deployment variable. Guessing which one was meant is how a
    # production process ends up writing to a laptop.
    raise BlobStoreNotConfigured(
        f"unknown {ENV_BACKEND}={name!r}; expected {BACKEND_LOCAL!r} or {BACKEND_SUPABASE!r}")


__all__ = ["ObjectStore", "GalleryBytesError", "BlobStoreNotConfigured",
           "LocalFilesystemObjectStore", "SupabaseObjectStore",
           "safe_key", "selected_backend", "object_store_for",
           "ENV_BACKEND", "BACKEND_LOCAL", "BACKEND_SUPABASE",
           "ENV_SUPABASE_URL", "ENV_SUPABASE_KEY", "ENV_SUPABASE_BUCKET",
           "ENV_SUPABASE_TIMEOUT", "DEFAULT_BUCKET"]
