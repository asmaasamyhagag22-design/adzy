# -*- coding: utf-8 -*-
"""One reader for a social snapshot, whichever puller wrote it.

`runs/social_snapshots/{slug}_ig.json` is written by TWO different tools into TWO different
shapes: `social_intel/ig_autopull.py` saves Apify's `{"items": [...]}`, and
`scripts/pull_instagram.py` saves the Graph API's `{"media": [...], "is_own_brand": ...}`. Every
reader in the codebase understands only `["items"]`, so a Graph-shaped snapshot silently yields
zero signals — the file is on disk, the analysis says the brand has no social presence, and
nothing anywhere reports a mismatch.

FIXED ON READ, DELIBERATELY NOT ON WRITE. Renaming the files or normalising the writers would
orphan the snapshots already collected, and those are the only real engagement data this system
has. A reader that accepts both costs nothing and loses nothing.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


def load_ig_snapshot(path: "str | Path") -> tuple[list, dict]:
    """(rows, meta) for a snapshot in either shape. ([], {}) when there is nothing readable.

    Rows come back in APIFY shape — the one every downstream reader already speaks — so a Graph
    row's `like_count` / `comments_count` / `caption` are renamed onto the Apify keys rather than
    every consumer learning both vocabularies.
    """
    p = Path(str(path))
    if not p.is_file():
        return [], {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 — an unreadable snapshot is not a crash
        logger.info("[social] %s could not be parsed", p.name)
        return [], {}
    if not isinstance(data, dict):
        return [], {}

    meta = {k: v for k, v in data.items() if k not in ("items", "media")}
    items = data.get("items")
    if isinstance(items, list) and items:
        return items, meta

    media = data.get("media")
    if isinstance(media, list) and media:
        rows = []
        for m in media:
            if not isinstance(m, dict):
                continue
            rows.append({
                "url": m.get("permalink") or m.get("url") or "",
                "caption": m.get("caption") or "",
                "likesCount": m.get("like_count"),
                "commentsCount": m.get("comments_count"),
                "timestamp": m.get("timestamp"),
                "ownerUsername": m.get("username") or meta.get("username") or "",
                "latestComments": m.get("comments") or [],
            })
        logger.info("[social] %s is a Graph-shaped snapshot — %d row(s) normalised", p.name,
                    len(rows))
        return rows, meta
    return [], meta


def default_snapshot_dir() -> Path:
    """The snapshots live at the REPO ROOT, not wherever the process happens to be standing.

    `Path("runs/social_snapshots")` resolves against the CURRENT WORKING DIRECTORY, so the answer
    depended on which folder the server was launched from. MEASURED 2026-08-01: called directly
    from the repo the analysis returned `checked: True` with 15 ranked posts; called through the
    running API it returned `{}`, and the dashboard rendered "no social snapshot collected for
    this brand" over a file that was sitting right there. Same bug class `competitor/registry.py`
    already fixed for the registry, and that `tests/test_webapp_paths_are_anchored.py` exists for.

    `BASEERA_SNAPSHOT_DIR` overrides it for tests and for a machine that stores run state
    elsewhere — the same door the registry offers.
    """
    import os
    override = os.environ.get("BASEERA_SNAPSHOT_DIR", "").strip()
    if override:
        return Path(override)
    return Path(__file__).resolve().parent.parent / "runs" / "social_snapshots"


def snapshot_path_for(profile: dict, root: "str | Path | None" = None) -> Optional[Path]:
    """Where this brand's snapshot would live, or None when the profile has no source URL."""
    try:
        from api.brand_translate import domain_key_from_url
        src = (profile or {}).get("source_url")
        if isinstance(src, dict):
            src = src.get("value")
        key = domain_key_from_url(str(src or ""))
        if not key:
            return None
        return Path(root) if root else (default_snapshot_dir() / f"{key}_ig.json")
    except Exception:  # noqa: BLE001
        return None
