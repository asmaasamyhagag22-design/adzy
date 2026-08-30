"""
Checkpointing so a run that gets rate-limited mid-scrape can resume instead
of restarting from zero (restarting also risks tripping the limiter harder,
per Instaloader's own guidance on not reinstantiating sessions frequently).
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class CheckpointManager:
    def __init__(self, checkpoint_dir: str):
        self.dir = Path(checkpoint_dir)
        self.dir.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        safe_key = key.replace("/", "_")
        return self.dir / f"{safe_key}.checkpoint.json"

    def save(self, key: str, data: dict[str, Any]) -> None:
        path = self._path(key)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, default=str, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(path)
        logger.debug("Checkpoint saved: %s", path)

    def load(self, key: str) -> dict[str, Any] | None:
        path = self._path(key)
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            logger.warning("Corrupt checkpoint at %s, ignoring", path)
            return None

    def clear(self, key: str) -> None:
        path = self._path(key)
        if path.exists():
            path.unlink()
