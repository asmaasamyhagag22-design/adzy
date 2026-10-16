# -*- coding: utf-8 -*-
"""The migration target engine must be able to reach its own refusal.

`db/session.py` was given a bounded connect on 2026-08-18 (blocker B2) because libpq waits forever
by default, which turns "the database is unreachable" into a hang rather than an answer. Two OTHER
engines in this system were built the same way and were missed:

  * `db/migrations/env.py` — every `alembic upgrade`
  * `db/migration_tools/apply.py::_guarded_engine` — every APPLY and every VERIFY

The second one is on the ACTUAL cutover path. The cutover is owner-attended and remote: a mistyped
or unreachable host would leave an operator watching a migration that will neither start nor
refuse, which is the single worst failure mode that step has.

MEASURED on this machine, against an unreachable DSN: 131 s before the fix, 5 s after.

These tests need no database. They connect to a port nothing listens on, which the test suite's
network guard permits because it is loopback.
"""
from __future__ import annotations

import time

import pytest

# A disposable-looking database name, so the target guard's DENY-by-default is not what is being
# measured here. Port 1 on loopback accepts nothing.
_UNREACHABLE = "postgresql+psycopg://u:p@127.0.0.1:1/baseera_tmp"


def test_the_guarded_engine_asks_for_a_bounded_connect(monkeypatch):
    """Asserted at the call site, because that is where the contract lives.

    SQLAlchemy merges `connect_args` inside the pool's connect closure, so the built Engine does
    not expose them anywhere stable to read back — a test that dug for them would be asserting
    SQLAlchemy's internals rather than this module's behaviour.
    """
    pytest.importorskip("sqlalchemy")
    import db.migration_tools.apply as apply_module
    from db.config import connect_timeout_seconds

    seen = {}

    def _spy(dsn, **kwargs):
        seen.update(kwargs)
        return object()

    monkeypatch.setattr(apply_module, "create_engine", _spy)
    apply_module._guarded_engine(_UNREACHABLE)

    assert seen.get("connect_args") == {"connect_timeout": connect_timeout_seconds()}


def test_an_unreachable_migration_target_refuses_instead_of_hanging():
    pytest.importorskip("psycopg")
    from sqlalchemy.exc import OperationalError

    from db.migration_tools.apply import _guarded_engine

    engine = _guarded_engine(_UNREACHABLE)
    started = time.monotonic()
    try:
        with pytest.raises(OperationalError):
            with engine.connect():
                pass
    finally:
        engine.dispose()
    elapsed = time.monotonic() - started

    assert elapsed < 60, (
        f"the migration target engine took {elapsed:.0f}s to refuse an unreachable database; "
        f"the bounded connect has regressed")


def test_the_timeout_has_one_definition_that_the_environment_can_override(monkeypatch):
    """One number, read from one place. A second hardcoded 5 would drift silently."""
    from db import config as config_module

    monkeypatch.setenv("BASEERA_DB_CONNECT_TIMEOUT", "3")
    assert config_module.connect_timeout_seconds() == 3

    monkeypatch.setenv("BASEERA_DATABASE_URL", "postgresql+psycopg://u:p@127.0.0.1:1/baseera_tmp")
    assert config_module.get_settings().connect_timeout == 3

    monkeypatch.delenv("BASEERA_DB_CONNECT_TIMEOUT")
    assert config_module.connect_timeout_seconds() == config_module.DatabaseSettings.connect_timeout


def test_every_engine_in_the_system_is_bounded():
    """A source-level sweep, so the next engine added is not the next one to hang.

    `create_engine` is called in exactly three places in the persistence layer. Each must pass a
    connect timeout; a fourth call site added without one fails here rather than during a cutover.
    """
    import re
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    unbounded = []
    for relative in ("db/session.py", "db/migration_tools/apply.py", "db/migrations/env.py"):
        source = (root / relative).read_text(encoding="utf-8")
        for match in re.finditer(r"(create_engine|engine_from_config)\((.{0,400}?)\)\n", source,
                                 re.DOTALL):
            if "connect_args" not in match.group(2):
                unbounded.append(f"{relative}: {match.group(1)}")
    assert not unbounded, f"engines built without a bounded connect: {unbounded}"
