from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml


class ConfigError(Exception):
    pass


def load_config(path: str = "config/config.yaml") -> dict[str, Any]:
    cfg_path = Path(path)
    if not cfg_path.exists():
        raise ConfigError(f"Config file not found: {path}")

    with open(cfg_path, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)

    _validate(config)
    return config


def _validate(config: dict[str, Any]) -> None:
    weights = config.get("competitor_scoring", {}).get("weights", {})
    if weights:
        total = sum(weights.values())
        if not (0.98 <= total <= 1.02):
            raise ConfigError(
                f"competitor_scoring.weights must sum to ~1.0, got {total:.3f}"
            )

    volume = config.get("scraping", {}).get("volume", {})
    sample = volume.get("full_comment_sample_size", 0)
    posts = volume.get("posts_per_profile", 0)
    if sample > posts:
        raise ConfigError(
            "full_comment_sample_size cannot exceed posts_per_profile "
            f"({sample} > {posts})"
        )


def load_lexicon(path: str) -> dict[str, list[str]]:
    p = Path(path)
    if not p.exists():
        raise ConfigError(f"Lexicon file not found: {path}")
    with open(p, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}
