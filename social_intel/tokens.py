"""ONE Apify-token accessor — the repo had TWO env names (`APIFY_TOKEN` in facebook_collector,
`APIFY_API_TOKEN` in instagram_intelligence) and a pull could silently miss the configured one.
The canonical name wins; the legacy name is accepted as a fallback."""
from __future__ import annotations

import os
from typing import Optional


def apify_token() -> Optional[str]:
    return os.getenv("APIFY_TOKEN") or os.getenv("APIFY_API_TOKEN") or None
