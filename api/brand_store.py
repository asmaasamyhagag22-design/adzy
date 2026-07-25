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
from pathlib import Path
from typing import Any, Optional

from api.user_auth import webapp_dir

_SAFE_KEY = re.compile(r"[^a-z0-9_]+")

# Serialises the read-modify-write in `update()` and `record_history()`. Same pattern as
# `dashboard/server._INFLIGHT_LOCK`, and a threading lock rather than an asyncio one on purpose:
# FastAPI runs the SYNC endpoints on a threadpool while brand jobs run on the event loop, so the
# competing writers are real OS threads. Single process throughout (no `uvicorn --workers` anywhere
# in the repo), so no file lock and no new dependency is needed.
_WRITE_LOCK = threading.RLock()


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
    """Apply `patch` to `base`, merging dict values one level deep.

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
            merged = dict(prior)
            merged.update(value)
            out[key] = merged
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
# than silent. `python -m api.claim_brands` resolves it deliberately, by a human.
DELETED = "deleted"
NOT_FOUND = "not_found"
FORBIDDEN = "forbidden"
UNCLAIMED = "unclaimed"


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
        try:
            brand = json.loads(self._path(domain_key).read_text(encoding="utf-8"))
        except (OSError, ValueError):
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

    # --- writes ---
    def save(self, brand: dict, *, owner_id: Optional[str] = None) -> dict:
        key = brand.get("domain_key")
        if not key:
            raise ValueError("brand has no domain_key")
        brand.pop("unclaimed", None)                     # a presentation flag, never persisted
        if owner_id and not brand.get("owner_id"):
            brand["owner_id"] = str(owner_id)
        brand.setdefault("created_at", int(time.time()))
        brand["updated_at"] = int(time.time())
        with _WRITE_LOCK:
            _write_atomic(self._path(key), json.dumps(brand, ensure_ascii=False, indent=2))
        return brand

    def update(self, domain_key: str, patch: dict, *,
               for_user: Optional[str] = None) -> Optional[dict]:
        """Merge a patch into the stored brand (the enrichment primitive).

        Returns None when the brand is absent OR belongs to another account — an enrichment job
        must not be able to rewrite someone else's analysis.

        The read and the write are held under one lock: they used to be separate, so two patches
        that read the same version each saved their own complete snapshot and the second silently
        dropped the first. `_merge_one_level` then handles the longer window, where a patch was
        built from a read taken before an unrelated write landed."""
        with _WRITE_LOCK:
            brand = self.get(domain_key, for_user=for_user)
            if brand is None:
                return None
            return self.save(_merge_one_level(brand, patch))

    def delete(self, domain_key: str, *, for_user: Optional[str] = None) -> str:
        """One of DELETED / NOT_FOUND / FORBIDDEN / UNCLAIMED.

        Returns a REASON, not a bool: the route used to answer `200 {"ok": true}` for a key that had
        never existed, and for a delete that failed with an OSError."""
        path = self._path(domain_key)
        try:
            brand = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return NOT_FOUND
        owner = str(brand.get("owner_id") or "")
        if for_user:
            if not owner:
                return UNCLAIMED                         # destructive action needs a proven owner
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
            try:
                brand = json.loads(p.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if brand.get("owner_id"):
                continue
            brand["owner_id"] = str(owner_id)
            self.save(brand)
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
