# -*- coding: utf-8 -*-
"""Engine and session factory — created on FIRST USE, never at import.

Importing this module must stay free: `tests/test_db_foundation.py` imports it in a fresh process
with no DSN configured and asserts that nothing connects. An engine built at import time would
turn a missing environment variable into a crash at application start, and would make the DB layer
impossible to import for schema inspection.

EXECUTION MODEL (2C / C3). This package uses synchronous SQLAlchemy + psycopg, matching the
existing SQLAlchemy models, migrations and PostgreSQL proof stack. A future async route must move
the ENTIRE synchronous application-service call to a worker thread. The Session is created, used
and closed inside that synchronous call; it must never be created on the event loop and then
passed across an ``asyncio.to_thread`` boundary.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from db.config import DatabaseSettings, get_settings

if TYPE_CHECKING:  # import-time cost stays zero; the driver is only needed when used
    from sqlalchemy import Engine
    from sqlalchemy.orm import Session, sessionmaker

_engines: dict[bool, "Engine"] = {}
_session_factories: dict[bool, "sessionmaker[Session]"] = {}


def create_engine_from(settings: DatabaseSettings) -> "Engine":
    """Build an engine WITHOUT connecting. SQLAlchemy connects lazily on first checkout.

    The connect timeout is applied here rather than left to the driver default. libpq waits
    indefinitely by default, so an unreachable host makes the FIRST checkout block forever —
    which silently converts every fail-closed guarantee above this layer into a hang. A caller
    that means to refuse a bad database has to be able to reach its own refusal.
    """
    from sqlalchemy import create_engine

    return create_engine(
        settings.url,
        echo=settings.echo,
        pool_size=settings.pool_size,
        max_overflow=settings.max_overflow,
        pool_pre_ping=settings.pool_pre_ping,
        future=True,
        # libpq parameter, so it is only meaningful for a PostgreSQL DSN. `database_url()`
        # already refuses anything else, but the guard keeps this function safe to call with a
        # hand-built settings object in tests.
        connect_args=({"connect_timeout": settings.connect_timeout}
                      if settings.is_postgres else {}),
    )


def get_engine(*, test: bool = False) -> "Engine":
    key = bool(test)
    if key not in _engines:
        _engines[key] = create_engine_from(get_settings(test=test))
    return _engines[key]


def get_session_factory(*, test: bool = False) -> "sessionmaker[Session]":
    key = bool(test)
    if key not in _session_factories:
        from sqlalchemy.orm import sessionmaker

        _session_factories[key] = sessionmaker(
            bind=get_engine(test=test), expire_on_commit=False, future=True)
    return _session_factories[key]


def session_factory_from(engine: "Engine") -> "sessionmaker[Session]":
    """Build an explicit factory for a supplied Engine (tests and application composition)."""
    from sqlalchemy.orm import sessionmaker

    return sessionmaker(bind=engine, expire_on_commit=False, future=True)


def dispose() -> None:
    """Drop the engine and its pool. For tests and shutdown."""
    for engine in _engines.values():
        engine.dispose()
    _engines.clear()
    _session_factories.clear()
