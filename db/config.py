# -*- coding: utf-8 -*-
"""Database configuration. Reading it has no side effects and opens no connection.

There is no default DSN pointing anywhere. A missing `BASEERA_DATABASE_URL` raises when someone
asks for it, rather than quietly resolving to localhost — an accidental connection to whatever
happens to be listening is exactly the failure this avoids.
"""
from __future__ import annotations

import os
from dataclasses import dataclass

ENV_URL = "BASEERA_DATABASE_URL"
# Integration tests bind to their OWN variable, never to the production one, so a test run can
# never be pointed at a live database by inheriting a single shared setting.
ENV_TEST_URL = "BASEERA_TEST_DATABASE_URL"


class DatabaseNotConfigured(RuntimeError):
    """Raised when a DSN is needed and none is set. Never substituted with a guess."""


@dataclass(frozen=True)
class DatabaseSettings:
    url: str
    echo: bool = False
    # Conservative bounded default for this small application: five steady connections and at
    # most five short bursts per process (hard ceiling 10). Deployments with measured concurrency
    # may override both values; the default deliberately does not scale with request count.
    pool_size: int = 5
    max_overflow: int = 5
    pool_pre_ping: bool = True          # a recycled connection must not surface as a request error
    # Bounded so that "the database is unreachable" is an ANSWER rather than a wait. libpq's
    # default is no timeout, which turns a fail-closed check into an indefinite hang: startup
    # under PERSISTENCE_BACKEND=postgres with a wrong host would never reach its own refusal,
    # and a refusal that never arrives is indistinguishable from a healthy slow start.
    connect_timeout: int = 5

    @property
    def is_postgres(self) -> bool:
        return self.url.startswith("postgresql")


def _flag(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    return default if raw is None else raw.strip().lower() in ("1", "true", "yes", "on")


def database_url(*, test: bool = False) -> str:
    var = ENV_TEST_URL if test else ENV_URL
    url = (os.environ.get(var) or "").strip()
    if not url:
        raise DatabaseNotConfigured(
            f"{var} is not set. Set it to a PostgreSQL DSN "
            f"(postgresql+psycopg://user:pass@host/dbname).")
    if not url.startswith("postgresql"):
        # SQLite cannot demonstrate row locks, composite-FK behaviour under concurrency, or JSONB.
        # Allowing it here would let a green test suite claim guarantees PostgreSQL provides and
        # SQLite does not.
        raise DatabaseNotConfigured(
            f"{var} must be a PostgreSQL DSN; got {url.split(':', 1)[0]!r}. "
            f"This schema depends on PostgreSQL semantics and is not portable to SQLite.")
    return url


def connect_timeout_seconds() -> int:
    """ONE definition of the bounded connect, for every engine in the system.

    `get_settings()` cannot serve the migration tooling, because that tooling is handed a DSN on
    the command line and must work with no `BASEERA_DATABASE_URL` set at all. Without this
    function the tooling would carry its own copy of the number, and two copies of a timeout drift
    the moment one of them is tuned.
    """
    return int(os.environ.get("BASEERA_DB_CONNECT_TIMEOUT",
                              str(DatabaseSettings.connect_timeout)))


def get_settings(*, test: bool = False) -> DatabaseSettings:
    return DatabaseSettings(
        url=database_url(test=test),
        echo=_flag("BASEERA_DB_ECHO"),
        pool_size=int(os.environ.get("BASEERA_DB_POOL_SIZE", "5")),
        max_overflow=int(os.environ.get("BASEERA_DB_MAX_OVERFLOW", "5")),
        connect_timeout=connect_timeout_seconds(),
    )


def safe_dsn(url: str) -> str:
    """A DSN with its password removed, for logs and errors.

    Connection strings carry credentials, and the one place they reliably leak is an exception
    message that someone pasted into a ticket.
    """
    if "@" not in url or "//" not in url:
        return url
    scheme, rest = url.split("//", 1)
    creds, host = rest.rsplit("@", 1)
    user = creds.split(":", 1)[0]
    return f"{scheme}//{user}:***@{host}"
