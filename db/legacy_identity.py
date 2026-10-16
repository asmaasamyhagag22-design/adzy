# -*- coding: utf-8 -*-
"""Deterministic identity bridge for the pre-PostgreSQL user store.

This module does not authenticate users and is not an auth cutover. It only owns the stable mapping
that the migration and the later session bridge must agree on.
"""
from __future__ import annotations

import re
import uuid

MIGRATION_NAMESPACE = uuid.UUID("6f5b9a1e-0d3c-4f21-9a7e-0b2c1d4e5f60")
LEGACY_USER_NAMESPACE = uuid.uuid5(MIGRATION_NAMESPACE, "user")
_LEGACY_USER_ID = re.compile(r"^[0-9a-f]{16}$")


def legacy_user_uuid(legacy_user_id: str) -> uuid.UUID:
    """Map one canonical ``secrets.token_hex(8)`` id to its permanent database UUID."""
    value = str(legacy_user_id)
    if not _LEGACY_USER_ID.fullmatch(value):
        raise ValueError("legacy_user_id must be exactly 16 lowercase hexadecimal characters")
    return uuid.uuid5(LEGACY_USER_NAMESPACE, value)


__all__ = ["LEGACY_USER_NAMESPACE", "MIGRATION_NAMESPACE", "legacy_user_uuid"]
