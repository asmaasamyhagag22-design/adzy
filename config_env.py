# -*- coding: utf-8 -*-
"""One place that loads `.env` — imported by every entry point that reads a credential.

MEASURED FAILURE (2026-07-20) this exists to prevent: the owner finished Meta identity
confirmation, generated a working Ad Library token and wrote it into `.env`, and the ads pull still
said "META_ADS_TOKEN is not set". `.env` was loaded in three unrelated modules, none of which run
before an analysis, so `ads_intel.orchestrator.autopull_enabled()` read an empty environment. The
failure was SILENT and shaped exactly like the truth ("no ads found for this competitor"), which is
the most expensive kind of bug this project can ship.

`override=False` on purpose: a value exported in the shell is a deliberate choice (a one-off run, a
CI secret) and must beat a stale line in the file.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

_ROOT = Path(__file__).resolve().parent
_loaded = False


def load_env(env_path: Optional[Path | str] = None, *, force: bool = False) -> bool:
    """Load the project's `.env` into `os.environ`. Idempotent, never raises.

    Returns True when a file was read. Safe to call from module import: a missing `.env` (CI,
    a container with real env vars) is a no-op, not an error.
    """
    global _loaded
    if _loaded and not force and env_path is None:
        return False
    path = Path(env_path) if env_path is not None else _ROOT / ".env"
    if env_path is None:
        _loaded = True
    if not path.is_file():
        return False
    try:
        from dotenv import load_dotenv
        load_dotenv(path, override=False)
        return True
    except Exception:  # noqa: BLE001 — a parse error must never take down a run
        # Minimal fallback so a missing python-dotenv can't silently disable every credential.
        try:
            for line in path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
            return True
        except Exception:  # noqa: BLE001
            return False
