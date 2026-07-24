"""API auth gate for the PAID generation routes (P-54).

Before this, `/api/run`, `/api/poster/from-profile`, `/api/reel/from-profile` and
`/api/swot/from-profile` were reachable by anyone with no key and no rate limit — each spends real
money (reel drives Veo, ~71% of all historical spend). `spend_tripwire.py` guards only the two paid
CLIs, NOT these HTTP routes. This dependency gates them behind an API key and, when no key is
configured, FAILS CLOSED for remote callers so a deploy that forgets to set one can't be drained
from the internet — while keeping local dev and the test suite working.
"""
from __future__ import annotations

import hmac
import json
import os
import time
from collections import deque
from typing import Any, Optional

from fastapi import Header, HTTPException, Request


def _configured_key() -> str:
    return (os.environ.get("BASEERA_API_KEY") or "").strip()


def _is_loopback(request: Request) -> bool:
    host = (request.client.host if request.client else "") or ""
    return host in ("127.0.0.1", "::1", "localhost") or host.startswith("127.")


# --- lightweight in-process rate limit (defense-in-depth even with a valid key) ---
_RATE_MAX = int(os.environ.get("API_RATE_MAX_PER_MIN", "30"))
_RATE_WINDOW_S = 60.0
_hits: dict[str, deque] = {}


def _rate_limited(bucket: str) -> bool:
    now = time.monotonic()
    dq = _hits.setdefault(bucket, deque())
    while dq and now - dq[0] > _RATE_WINDOW_S:
        dq.popleft()
    if len(dq) >= _RATE_MAX:
        return True
    dq.append(now)
    return False


async def require_api_key(request: Request, x_api_key: Optional[str] = Header(None)) -> None:
    """Gate a paid route. Requires `X-API-Key == BASEERA_API_KEY` when a key is configured; when it
    is NOT configured, allows only local dev / the test suite and rejects remote callers."""
    key = _configured_key()
    in_tests = bool(os.environ.get("PYTEST_CURRENT_TEST"))

    if key:
        if not x_api_key or not hmac.compare_digest(str(x_api_key), key):
            raise HTTPException(status_code=401, detail="Invalid or missing API key.")
    elif not (in_tests or _is_loopback(request)):
        # No key set AND the caller is remote -> refuse rather than spend for anyone on the internet.
        raise HTTPException(
            status_code=503,
            detail="This endpoint is disabled until BASEERA_API_KEY is configured on the server.")

    # Rate limit per key (or per client IP when running key-less on loopback). Skipped under pytest.
    if not in_tests:
        bucket = x_api_key or (request.client.host if request.client else "anon")
        if _rate_limited(str(bucket)):
            raise HTTPException(status_code=429,
                                detail=f"Rate limit exceeded ({_RATE_MAX}/min). Slow down.")


# --- P-56: bound the client-supplied profile before it is trusted as grounding evidence ---
# The poster/reel/swot routes accept a `profile` dict straight from the request body and feed it to
# the Evidence Ledger. The auth key (above) already restricts WHO can submit one; this bounds its
# SIZE/DEPTH so a hand-crafted mega-payload can't exhaust memory. (Provenance — that the profile
# actually came from our own scrape — is a deeper design point disclosed in the defense brief.)
_MAX_PROFILE_BYTES = int(os.environ.get("API_MAX_PROFILE_BYTES", str(2 * 1024 * 1024)))  # 2 MiB
_MAX_PROFILE_DEPTH = 24


def _depth(obj: Any, level: int = 0) -> int:
    if level > _MAX_PROFILE_DEPTH:
        return level
    if isinstance(obj, dict):
        return max((_depth(v, level + 1) for v in obj.values()), default=level)
    if isinstance(obj, (list, tuple)):
        return max((_depth(v, level + 1) for v in obj), default=level)
    return level


def enforce_profile_bounds(profile: Any) -> None:
    """Reject an oversized or pathologically-nested client profile with HTTP 413."""
    if not isinstance(profile, dict):
        raise HTTPException(status_code=422, detail="`profile` must be an object.")
    try:
        size = len(json.dumps(profile, ensure_ascii=False))
    except (TypeError, ValueError):
        raise HTTPException(status_code=422, detail="`profile` is not JSON-serializable.")
    if size > _MAX_PROFILE_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"Profile too large ({size} bytes > {_MAX_PROFILE_BYTES}).")
    if _depth(profile) > _MAX_PROFILE_DEPTH:
        raise HTTPException(status_code=413, detail="Profile nesting too deep.")
