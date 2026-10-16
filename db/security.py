# -*- coding: utf-8 -*-
"""Persistence-safe job capability derivation; raw bearer tokens never leave this function."""
from __future__ import annotations

import hashlib

_CAPABILITY_DOMAIN = b"baseera-job-capability\0"


def capability_digest(raw_token: str) -> str:
    value = str(raw_token or "")
    if not value:
        raise ValueError("capability token is required")
    return hashlib.sha256(_CAPABILITY_DOMAIN + value.encode("utf-8")).hexdigest()


__all__ = ["capability_digest"]
