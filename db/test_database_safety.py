# -*- coding: utf-8 -*-
"""Fail-closed identity checks for PostgreSQL databases that tests may destroy.

This module intentionally does not connect to PostgreSQL.  It must run before an engine is
created and before any DROP/TRUNCATE statement can be reached.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Mapping
from urllib.parse import unquote, urlsplit

from db.config import ENV_TEST_URL, ENV_URL, safe_dsn

ENV_DESTRUCTIVE_TEST_DATABASE = "BASEERA_DESTRUCTIVE_TEST_DATABASE"
ENV_DEMO_URL = "BASEERA_DEMO_DATABASE_URL"

# A generic ``*_test`` suffix was the exact hole that destroyed the Gate 2 demo database.
# Destructive pytest databases must live in this deliberately narrow namespace.
_PYTEST_DATABASE_NAME = re.compile(r"^baseera_pytest(?:_[a-z0-9]+)?$")


class UnsafeTestDatabase(RuntimeError):
    """Raised before destructive setup when the target is not explicitly test-only."""


@dataclass(frozen=True)
class DatabaseIdentity:
    scheme: str
    host: str
    port: int | None
    database: str


def database_identity(dsn: str) -> DatabaseIdentity:
    parsed = urlsplit((dsn or "").strip())
    database = unquote(parsed.path.lstrip("/")).strip().lower()
    if not parsed.scheme.startswith("postgresql") or not parsed.hostname or not database:
        raise UnsafeTestDatabase(
            "destructive PostgreSQL tests require an explicit PostgreSQL DSN with host and "
            "database name")
    return DatabaseIdentity(
        scheme="postgresql",
        host=parsed.hostname.lower(),
        port=parsed.port,
        database=database,
    )


def require_disposable_test_database(
    dsn: str,
    *,
    environ: Mapping[str, str] | None = None,
) -> DatabaseIdentity:
    """Return the verified identity, or refuse before any destructive database operation.

    Three independent facts are required:
      1. the target name is in the reserved ``baseera_pytest[_suffix]`` namespace;
      2. an operator explicitly acknowledges that exact database name for destruction;
      3. neither the application DSN nor the dedicated demo DSN identifies the same target.
    """
    env = os.environ if environ is None else environ
    identity = database_identity(dsn)
    if not _PYTEST_DATABASE_NAME.fullmatch(identity.database):
        raise UnsafeTestDatabase(
            f"refusing destructive test setup for {safe_dsn(dsn)}: database name "
            f"{identity.database!r} is outside the reserved baseera_pytest namespace")

    acknowledgement = (env.get(ENV_DESTRUCTIVE_TEST_DATABASE) or "").strip().lower()
    if acknowledgement != identity.database:
        raise UnsafeTestDatabase(
            f"refusing destructive test setup for {safe_dsn(dsn)}: set "
            f"{ENV_DESTRUCTIVE_TEST_DATABASE} to the exact database name "
            f"{identity.database!r}")

    for variable in (ENV_DEMO_URL, ENV_URL):
        protected_dsn = (env.get(variable) or "").strip()
        if not protected_dsn:
            continue
        try:
            protected = database_identity(protected_dsn)
        except UnsafeTestDatabase:
            raise UnsafeTestDatabase(
                f"refusing destructive test setup: protected {variable} is not a valid "
                "PostgreSQL DSN") from None
        if protected == identity:
            raise UnsafeTestDatabase(
                f"refusing destructive test setup: {ENV_TEST_URL} and protected {variable} "
                f"identify the same database {identity.database!r}")
    return identity


def require_configured_disposable_test_database(
    *, environ: Mapping[str, str] | None = None,
) -> DatabaseIdentity | None:
    """Guard the configured pytest DSN; return ``None`` when PostgreSQL tests are disabled."""
    env = os.environ if environ is None else environ
    dsn = (env.get(ENV_TEST_URL) or "").strip()
    if not dsn:
        return None
    return require_disposable_test_database(dsn, environ=env)
