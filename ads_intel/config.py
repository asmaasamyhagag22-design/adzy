"""ش2 config — the deterministic ad-facts thresholds, env-overridable (the media_plan/config.py
house pattern). Two DISTINCT longevity tiers: WINNER (≥30d active — the pitch's "nobody keeps a
losing ad running a month") and PROVEN (≥60d — the stricter split-test verdict ad_presence has
always used). Both are config, never inline magic numbers."""
from __future__ import annotations

import os

PROVEN_DAYS = 60                 # the historic ad_presence threshold (unchanged semantics)
COLLATION_PREFIX_CHARS = 120     # variation-collation key = the first N normalized chars


def winner_days() -> int:
    """Active days after which an ad counts as a WINNER (market's own verdict)."""
    return int(os.getenv("ADS_WINNER_DAYS", "30"))


def fatigue_window_days() -> int:
    """The sliding window for variation bursts."""
    return int(os.getenv("ADS_FATIGUE_WINDOW_DAYS", "7"))


def fatigue_min_variations() -> int:
    """Variations of the SAME collated ad inside one window that spell Fatigue/Iterating."""
    return int(os.getenv("ADS_FATIGUE_MIN_VARIATIONS", "3"))
