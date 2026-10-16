"""Persistent brand store — the heart of the web app.

The frontend accumulates results onto ONE brand object keyed by `domain_key` and re-reads it after
every step. This store persists that object as a JSON file per brand (mirroring
`competitor/registry.py`'s slug→JSON-file pattern, incl. corrupt-file tolerance), and keeps a small
generated-image history for `GET /api/history`.

Single-process, no external deps. Location is `BASEERA_WEBAPP_DIR` (default `outputs/webapp`), so
tests get an isolated tmp dir.
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Iterator, Optional

from api.user_auth import webapp_dir

_SAFE_KEY = re.compile(r"[^a-z0-9_]+")

# Serialises threads before they contend on the per-file OS lock below.  The OS lock is the
# correctness boundary for separate BrandStore instances and separate web-worker processes;
# `_WRITE_LOCK` alone cannot provide either guarantee.
_WRITE_LOCK = threading.RLock()


@contextmanager
def _process_file_lock(path: Path) -> Iterator[None]:
    """Exclusive cross-process lock for one persisted JSON document.

    The lock file is deliberately stable and is never unlinked: deleting it while another process
    has the old inode/handle locked can let a third process lock a different file and enter the
    critical section concurrently.  OS process teardown releases the held lock after a crash.
    """
    lock_path = path.with_suffix(path.suffix + ".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+b") as handle:
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"\0")
            handle.flush()
        handle.seek(0)
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
            try:
                yield
            finally:
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


@contextmanager
def _storage_lock(path: Path) -> Iterator[None]:
    """One ordering for every logical read-modify-write on ``path``."""
    with _WRITE_LOCK:
        with _process_file_lock(path):
            yield


def _tmp_path(path: Path) -> Path:
    """A temp file private to THIS writer.

    Every writer used to stage through the same `<name>.json.tmp`. Two of them interleaving could
    publish half-written bytes, or raise PermissionError on Windows where `replace` cannot touch a
    file another thread still holds open — a worse outcome than the lost update this guards."""
    return path.with_suffix(f".json.{os.getpid()}.{threading.get_ident()}.tmp")


def _write_atomic(path: Path, payload: str) -> None:
    tmp = _tmp_path(path)
    try:
        tmp.write_text(payload, encoding="utf-8")
        tmp.replace(path)
    finally:
        if tmp.exists():                      # `replace` consumed it on success; clean up a failure
            try:
                tmp.unlink()
            except OSError:
                pass


def _merge_one_level(base: dict, patch: dict) -> dict:
    """Apply `patch` to `base`, merging DICT values at every depth. Lists and scalars replace.

    MEASURED LIVE (2026-07-25): with the merge stopping after one level, generating a second
    influencer candidate left the brand holding ONE — the patch
    `{"ai_influencer": {"candidates": {c2: …}}}` replaced the whole candidates map rather than
    adding to it. The owner would pay for a second face and lose the first.

    The principle the one-level version got wrong: a patch is a statement about the keys it NAMES.
    Silently replacing a sibling key the patch never mentioned is not merging, it is deleting.
    Recursing on dicts states exactly what was said and nothing more; lists still replace, so a
    regenerated calendar overwrites the old one rather than accumulating onto it.

    A plain `dict.update` loses data even when the writes are perfectly serialised. The enrichment
    jobs read the brand at T0 and build their patch from that copy — `webapp_influencer` does
    `ai = dict(brand.get("ai_influencer") or {})` and returns `{"ai_influencer": ai}` — so a patch
    landing at T2 carries a whole subtree as it looked at T0. A portrait approval written at T1 was
    silently erased by a job that never saw it.

    One level only, and only for dicts: scalars and LISTS still replace outright, so a regenerated
    calendar overwrites the old one instead of accumulating onto it. Safe here because every patch
    in this codebase is additive enrichment — none of them means "remove this nested key"."""
    out = dict(base)
    for key, value in patch.items():
        prior = out.get(key)
        if isinstance(value, dict) and isinstance(prior, dict):
            out[key] = _merge_one_level(prior, value)      # recurse: dicts merge at every depth
        else:
            out[key] = value
    return out


def _brands_dir() -> Path:
    d = webapp_dir() / "brands"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _safe(domain_key: str) -> str:
    """Filesystem-safe form of a domain_key (already `_`-separated; guard anyway)."""
    return _SAFE_KEY.sub("_", (domain_key or "").lower()).strip("_") or "brand"


# A brand created before ownership existed carries no `owner_id`. Who created it is not recorded
# anywhere, so the store never guesses: such a brand is surfaced as UNCLAIMED — readable (it is the
# operator's own working catalog) but never deletable, and flagged so the state is visible rather
# than silent.
#
# Two paths resolve that state, and neither guesses. `python -m api.claim_brands` assigns the whole
# unclaimed backlog deliberately, by a human. A successful authenticated RE-SCRAPE claims the one
# brand it just rebuilt (see `upsert`) — the caller proved which brand they mean by paying to
# rebuild it, so recording them as its manager is evidence, not inference. `owner_id` means the
# account managing this record in the product; it says nothing about who owns the real company.
DELETED = "deleted"
NOT_FOUND = "not_found"
FORBIDDEN = "forbidden"
UNCLAIMED = "unclaimed"


class CorruptBrandError(RuntimeError):
    """An existing brand document cannot be read safely enough to update."""


class BrandStore:
    """Per-brand JSON files, scoped by owner.

    Every read/write takes an OPTIONAL `for_user`. Passing it enforces ownership; omitting it keeps
    the unscoped behaviour used by internal callers (background jobs that were already authorised
    when the request came in, and the CLIs). The scoping lives here rather than in the routes so a
    future endpoint cannot forget it — MEASURED 2026-07-25: with no owner recorded at all, a brand
    new account could list, fully read and delete every brand in the catalog.
    """

    def __init__(self, base: Optional[Path] = None) -> None:
        self._dir = Path(base) if base else _brands_dir()
        self._dir.mkdir(parents=True, exist_ok=True)

    def _path(self, domain_key: str) -> Path:
        return self._dir / f"{_safe(domain_key)}.json"

    @staticmethod
    def _read_path(path: Path) -> Optional[dict]:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        return value if isinstance(value, dict) else None

    @staticmethod
    def _visible_to(brand: dict, user_id: Optional[str]) -> bool:
        if not user_id:
            return True                                  # unscoped caller (internal)
        owner = str(brand.get("owner_id") or "")
        return not owner or owner == str(user_id)        # mine, or not yet claimed by anyone

    @staticmethod
    def _mark(brand: dict) -> dict:
        """Disclose an unowned brand instead of letting it pass as owned."""
        if not brand.get("owner_id"):
            brand = {**brand, "unclaimed": True}
        return brand

    # --- reads ---
    def get(self, domain_key: str, *, for_user: Optional[str] = None) -> Optional[dict]:
        brand = self._read_path(self._path(domain_key))
        if brand is None:
            return None
        if not self._visible_to(brand, for_user):
            return None                                  # another account's brand: as if absent
        return self._mark(brand)

    def list(self, *, for_user: Optional[str] = None) -> list[dict]:
        """Brands visible to `for_user`, newest first. Corrupt files are skipped, never fatal."""
        out = []
        for p in self._dir.glob("*.json"):
            try:
                brand = json.loads(p.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if self._visible_to(brand, for_user):
                out.append(self._mark(brand))
        out.sort(key=lambda b: b.get("updated_at", 0), reverse=True)
        return out

    def owner_of(self, domain_key: str) -> Optional[str]:
        """Who manages this brand: the owner id, `""` when unclaimed, None when nothing is stored.

        Unscoped, and it returns the owner id and NOTHING ELSE.  Its one caller is the scrape
        preflight, which has to tell "another account manages this" apart from "nothing is stored" —
        a distinction `get(for_user=...)` collapses into None on purpose, so that read path cannot
        answer the question without also handing over the document.  Returning the id alone keeps a
        cost gate from turning into a way to read someone else's analysis.

        A COST GATE, NEVER THE CONCURRENCY AUTHORITY: this answer is stale the moment it is read,
        and two callers can both be told "unclaimed".  `upsert` re-reads the owner under the storage
        lock and is what actually decides.  An unreadable document answers None (nothing usable is
        stored); `upsert` then raises CorruptBrandError rather than overwriting it.
        """
        brand = self._read_path(self._path(domain_key))
        if brand is None:
            return None
        return str(brand.get("owner_id") or "")

    # --- writes ---
    @staticmethod
    def _prepare_for_write(brand: dict, *, owner_id: Optional[str] = None) -> dict:
        out = dict(brand)
        out.pop("unclaimed", None)                       # presentation flag, never persisted
        if owner_id and not out.get("owner_id"):
            out["owner_id"] = str(owner_id)
        out.setdefault("created_at", int(time.time()))
        out["updated_at"] = int(time.time())
        return out

    def _persist_locked(self, brand: dict, *, owner_id: Optional[str] = None) -> dict:
        """Persist while the caller holds this brand's storage lock."""
        out = self._prepare_for_write(brand, owner_id=owner_id)
        _write_atomic(self._path(str(out["domain_key"])),
                      json.dumps(out, ensure_ascii=False, indent=2))
        return out

    def save(self, brand: dict, *, owner_id: Optional[str] = None) -> dict:
        """Replace a complete document atomically.

        This is the creation/explicit-replacement primitive, not a stale-snapshot update.  Product
        flows that modify part of an existing brand use ``update`` or ``mutate`` instead.
        """
        key = brand.get("domain_key")
        if not key:
            raise ValueError("brand has no domain_key")
        path = self._path(str(key))
        with _storage_lock(path):
            return self._persist_locked(brand, owner_id=owner_id)

    def upsert(self, brand: dict, *, owner_id: Optional[str] = None,
               preserve_existing: Optional[set[str]] = None) -> Optional[dict]:
        """Create a brand, or replace only the named top-level fields on its latest state.

        A fresh scrape is a complete projection of the base profile fields it names, so those
        fields replace.  ``preserve_existing`` names placeholders emitted by that projection which
        are not deletion requests (analysis/strategy/creative state).

        OWNERSHIP.  A brand ANOTHER account manages is never overwritten and never changes hands.
        A brand nobody manages yet is CLAIMED by this write when it succeeds — created or
        unclaimed, the caller that lands the successful result becomes its manager.  The decision
        is made against the state re-read inside this lock, so when two callers both saw the brand
        as unclaimed, the second one to arrive here sees the first as its owner and loses.
        """
        key = str(brand.get("domain_key") or "")
        if not key:
            raise ValueError("brand has no domain_key")
        path = self._path(key)
        with _storage_lock(path):
            exists = path.exists()
            current = self._read_path(path)
            if exists and current is None:
                raise CorruptBrandError(f"stored brand JSON is unreadable: {path.name}")
            if current is None:
                candidate = dict(brand)
                candidate.pop("owner_id", None)
                return self._persist_locked(candidate, owner_id=owner_id)

            current_owner = str(current.get("owner_id") or "")
            if owner_id and current_owner and current_owner != str(owner_id):
                return None                              # someone else manages it: never ours
            patch = dict(brand)
            patch.pop("owner_id", None)
            for field in (preserve_existing or set()):
                patch.pop(str(field), None)
            candidate = {**current, **patch}             # named base fields replace; extras survive
            # CLAIM-ON-SUCCESS.  `owner_id` rides the SAME locked write as the result, so an
            # unclaimed brand becomes this caller's in one operation — never "claim it, then persist
            # it", which would leave a window where the brand is owned but still holds the old
            # result, and would strand ownership if the second write failed.  `_prepare_for_write`
            # only fills an ABSENT owner, so a re-scrape of my own brand keeps it unchanged.
            return self._persist_locked(candidate, owner_id=owner_id)

    def mutate(self, domain_key: str, mutator: Callable[[dict], dict], *,
               for_user: Optional[str] = None) -> Optional[dict]:
        """Apply ``mutator`` to the latest stored document under its storage lock.

        The callback expresses the exact list/map/delete semantics needed by its caller.  It must
        return the complete next document; schema-free enrichment keys remain supported.  Identity
        is immutable here: neither ``domain_key`` nor ``owner_id`` can be changed through a patch.
        """
        path = self._path(domain_key)
        with _storage_lock(path):
            exists = path.exists()
            brand = self._read_path(path)
            if brand is None:
                if exists:
                    raise CorruptBrandError(f"stored brand JSON is unreadable: {path.name}")
                return None
            if not self._visible_to(brand, for_user):
                return None
            original_owner = str(brand.get("owner_id") or "")
            updated = mutator(dict(brand))
            if not isinstance(updated, dict):
                raise TypeError("BrandStore mutator must return a dict")
            if str(updated.get("domain_key") or "") != str(brand.get("domain_key") or ""):
                raise ValueError("BrandStore mutation cannot change domain_key")
            if str(updated.get("owner_id") or "") != original_owner:
                raise ValueError("BrandStore mutation cannot change owner_id")
            if updated == brand:
                return self._mark(updated)
            return self._persist_locked(updated)

    def update(self, domain_key: str, patch: dict, *,
               for_user: Optional[str] = None) -> Optional[dict]:
        """Merge a patch into the stored brand (the enrichment primitive).

        Returns None when the brand is absent OR belongs to another account — an enrichment job
        must not be able to rewrite someone else's analysis.

        The read and the write are held under one lock: they used to be separate, so two patches
        that read the same version each saved their own complete snapshot and the second silently
        dropped the first. `_merge_one_level` then handles the longer window, where a patch was
        built from a read taken before an unrelated write landed."""
        return self.mutate(domain_key, lambda brand: _merge_one_level(brand, patch),
                           for_user=for_user)

    def replace_block(self, domain_key: str, block: str, value: dict, *,
                      for_user: Optional[str] = None) -> Optional[dict]:
        """REPLACE one top-level block outright — the write `update` deliberately cannot express.

        `_merge_one_level` recurses into dicts, so patching `{"ai_influencer": {"candidates": {}}}`
        merges an empty dict and removes NOTHING. Its docstring says as much: "none of them means
        'remove this nested key'". That is right for enrichment and wrong for "start over", which
        is exactly what the influencer reset needed — MEASURED: the reset left both candidates in
        place, so the second portrait click produced a two-candidate state the UI has no control
        for, and the fourth hit MAX_CANDIDATES and surfaced as "Generation step failed."

        Same ownership rule and same lock as `update`: returns None when the brand is absent or
        belongs to another account. Destructive by design — only for a block the user asked to
        discard.
        """
        def replace(brand: dict) -> dict:
            brand[block] = value
            return brand

        return self.mutate(domain_key, replace, for_user=for_user)

    def add_nested(self, domain_key: str, block: str, collection: str, item_id: str,
                   item: dict, *, for_user: Optional[str] = None) -> Optional[dict]:
        """Add ONE entry to `brand[block][collection][item_id]`, read and written under one lock.

        `update()` cannot express this safely. Its merge is one level deep, so a patch carrying
        `{"ai_influencer": {"candidates": {...}}}` REPLACES the whole candidates map — and the
        enrichment jobs build their patches from a read taken before the work started, so two
        concurrent additions each ship the map as it looked at T0 and the second erases the first.
        For influencer candidates that means the owner pays for a portrait, asks for another, and
        silently loses the one she was meant to be choosing between.

        Returns the stored brand, or None if it is absent or belongs to another account."""
        def add(brand: dict) -> dict:
            container = dict(brand.get(block) or {})
            entries = dict(container.get(collection) or {})
            entries[str(item_id)] = item
            container[collection] = entries
            brand[block] = container
            return brand

        return self.mutate(domain_key, add, for_user=for_user)

    def delete(self, domain_key: str, *, for_user: Optional[str] = None) -> str:
        """One of DELETED / NOT_FOUND / FORBIDDEN / UNCLAIMED.

        Returns a REASON, not a bool: the route used to answer `200 {"ok": true}` for a key that had
        never existed, and for a delete that failed with an OSError."""
        path = self._path(domain_key)
        with _storage_lock(path):
            brand = self._read_path(path)
            if brand is None:
                return NOT_FOUND
            owner = str(brand.get("owner_id") or "")
            if for_user:
                if not owner:
                    return UNCLAIMED                     # destructive action needs a proven owner
                if owner != str(for_user):
                    return FORBIDDEN
            try:
                path.unlink()
            except OSError:
                return NOT_FOUND
            return DELETED

    def claim_unowned(self, owner_id: str) -> int:
        """Assign every brand that has no owner to `owner_id`. Returns how many were claimed.

        Deliberately explicit and human-triggered: the store will not infer an owner for data that
        pre-dates ownership, because a guessed owner is invented evidence."""
        claimed = 0
        for p in sorted(self._dir.glob("*.json")):
            with _storage_lock(p):
                brand = self._read_path(p)
                if brand is None or brand.get("owner_id"):
                    continue
                brand["owner_id"] = str(owner_id)
                self._persist_locked(brand)
                claimed += 1
        return claimed


# ---------------------------------------------------------------------
# generated-image history (backs GET /api/history)
# ---------------------------------------------------------------------

def _history_path() -> Path:
    return webapp_dir() / "history.json"


def record_history(item: dict, *, limit: int = 500, owner_id: Optional[str] = None) -> None:
    """Prepend a generated-image record. Each item carries at least image_b64 + metadata the UI
    renders (filename, brand_name, domain_key, content_type, style), plus who generated it — the
    gallery is one file shared by every account, so without an owner it leaks images across them."""
    path = _history_path()
    item = {**item, "created_at": int(time.time())}
    if owner_id:
        item["owner_id"] = str(owner_id)
    # Read, insert and write under one lock. `/api/generate` is a sync endpoint (threadpool) while
    # the multipart routes are async, so two images finishing together really did read the same
    # list, each insert only its own entry, and the second overwrite the first. That entry is the
    # ONLY copy of the image — this path never writes a PNG to disk — so a lost update lost the
    # picture for good.
    with _WRITE_LOCK:
        try:
            data = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else []
        except (OSError, ValueError):
            data = []
        data.insert(0, item)
        del data[limit:]
        _write_atomic(path, json.dumps(data, ensure_ascii=False))


def read_history(limit: int = 100, *, for_user: Optional[str] = None) -> list[dict]:
    """The image gallery. Scoped like the brands: mine, plus entries recorded before ownership
    existed (flagged `unclaimed` rather than attributed to a guess)."""
    path = _history_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else []
    except (OSError, ValueError):
        data = []
    if for_user:
        out = []
        for it in data:
            owner = str(it.get("owner_id") or "")
            if not owner:
                out.append({**it, "unclaimed": True})
            elif owner == str(for_user):
                out.append(it)
        data = out
    return data[: max(0, int(limit))]
