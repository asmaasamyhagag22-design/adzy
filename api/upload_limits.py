# -*- coding: utf-8 -*-
"""Bounded reads for multipart uploads.

MEASURED shape of the problem: the web app's three upload routes called `await file.read()`, which
pulls the entire body into memory with no ceiling — and `/api/edit/inpaint` then base64-expands it
(×1.33) into the JSON response. FastAPI and Starlette impose no body-size limit of their own, and
`api/main.py` installs no size middleware, so one request could allocate several times the uploaded
file's size on a single-process server.

This mirrors the bound `api/auth.enforce_profile_bounds` already places on client-supplied profiles
(P-56): read up to the ceiling, and refuse with 413 rather than truncating — a silently truncated
image is a corrupt image presented as a valid one.
"""
from __future__ import annotations

import os

from fastapi import HTTPException

# Generous for a real logo or product photo, far below anything that threatens the process.
_MAX_UPLOAD_BYTES = int(os.environ.get("API_MAX_UPLOAD_BYTES", str(12 * 1024 * 1024)))  # 12 MiB
_CHUNK = 256 * 1024


async def read_bounded(upload, *, field: str = "file") -> bytes:
    """Read an UploadFile up to the ceiling. Raises 413 the moment the body exceeds it.

    Reading in chunks matters: `await upload.read()` would have already materialised the whole body
    before any size check could run, so a check placed after it would measure a cost already paid."""
    if upload is None:
        return b""
    out = bytearray()
    while True:
        chunk = await upload.read(_CHUNK)
        if not chunk:
            break
        out.extend(chunk)
        if len(out) > _MAX_UPLOAD_BYTES:
            raise HTTPException(
                status_code=413,
                detail=(f"`{field}` exceeds the {_MAX_UPLOAD_BYTES // (1024 * 1024)} MiB upload "
                        "limit."))
    return bytes(out)
