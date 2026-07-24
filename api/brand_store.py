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
import re
import time
from pathlib import Path
from typing import Any, Optional

from api.user_auth import webapp_dir

_SAFE_KEY = re.compile(r"[^a-z0-9_]+")


def _brands_dir() -> Path:
    d = webapp_dir() / "brands"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _safe(domain_key: str) -> str:
    """Filesystem-safe form of a domain_key (already `_`-separated; guard anyway)."""
    return _SAFE_KEY.sub("_", (domain_key or "").lower()).strip("_") or "brand"


class BrandStore:
    def __init__(self, base: Optional[Path] = None) -> None:
        self._dir = Path(base) if base else _brands_dir()
        self._dir.mkdir(parents=True, exist_ok=True)

    def _path(self, domain_key: str) -> Path:
        return self._dir / f"{_safe(domain_key)}.json"

    # --- reads ---
    def get(self, domain_key: str) -> Optional[dict]:
        try:
            return json.loads(self._path(domain_key).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    def list(self) -> list[dict]:
        """All brands, newest first. Corrupt files are skipped, never fatal."""
        out = []
        for p in self._dir.glob("*.json"):
            try:
                out.append(json.loads(p.read_text(encoding="utf-8")))
            except (OSError, ValueError):
                continue
        out.sort(key=lambda b: b.get("updated_at", 0), reverse=True)
        return out

    # --- writes ---
    def save(self, brand: dict) -> dict:
        key = brand.get("domain_key")
        if not key:
            raise ValueError("brand has no domain_key")
        brand.setdefault("created_at", int(time.time()))
        brand["updated_at"] = int(time.time())
        path = self._path(key)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(brand, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(path)
        return brand

    def update(self, domain_key: str, patch: dict) -> Optional[dict]:
        """Shallow-merge a patch into the stored brand (the enrichment primitive)."""
        brand = self.get(domain_key)
        if brand is None:
            return None
        brand.update(patch)
        return self.save(brand)

    def delete(self, domain_key: str) -> bool:
        try:
            self._path(domain_key).unlink()
            return True
        except OSError:
            return False


# ---------------------------------------------------------------------
# generated-image history (backs GET /api/history)
# ---------------------------------------------------------------------

def _history_path() -> Path:
    return webapp_dir() / "history.json"


def record_history(item: dict, *, limit: int = 500) -> None:
    """Prepend a generated-image record. Each item carries at least image_b64 + metadata the UI
    renders (filename, brand_name, domain_key, content_type, style)."""
    path = _history_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else []
    except (OSError, ValueError):
        data = []
    item = {**item, "created_at": int(time.time())}
    data.insert(0, item)
    del data[limit:]
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def read_history(limit: int = 100) -> list[dict]:
    path = _history_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else []
    except (OSError, ValueError):
        data = []
    return data[: max(0, int(limit))]
