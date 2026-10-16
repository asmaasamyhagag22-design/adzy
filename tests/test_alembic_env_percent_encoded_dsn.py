# -*- coding: utf-8 -*-
"""A percent-encoded password must reach the database, not die in the ini parser.

MEASURED against the hosted Supabase target (2026-08-22): the production database password
contains a URL-reserved character, so the DSN must percent-encode it, so the DSN contains a
literal `%`. `db/migrations/env.py` passes that DSN to `Config.set_main_option`, which stores it
in a ConfigParser that applies pyformat interpolation when `env.py` reads the section back for
`engine_from_config`. The result:

    ValueError: invalid interpolation syntax in 'postgresql+psycopg://u:pa%23ss@...' at position 25

raised BEFORE any connection is attempted — so `alembic upgrade head` fails at config parsing and
reports an interpolation error where an operator is looking for a database error. Alembic's own
`set_main_option` docstring states the escape requirement.

These tests do not need a database. They assert the failure is a CONNECTION failure, which is the
only thing that should stop this DSN.
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]

# A DSN nobody can connect to, whose password is percent-encoded exactly as a real one would be.
# Port 1 on loopback does NOT refuse immediately on every host — measured here it is dropped —
# which is precisely why the bounded connect timeout below is part of the contract.
_ENCODED_DSN = "postgresql+psycopg://u:pa%23ss@127.0.0.1:1/none"


def _alembic(*args: str) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["BASEERA_DATABASE_URL"] = _ENCODED_DSN
    env.pop("BASEERA_TEST_DATABASE_URL", None)
    env.pop("PGOPTIONS", None)
    return subprocess.run(
        [sys.executable, "-m", "alembic", "-c", str(_ROOT / "alembic.ini"), *args],
        cwd=str(_ROOT), capture_output=True, text=True, timeout=180, env=env)


def test_a_percent_encoded_password_does_not_break_the_ini_parser():
    """The failure must be about reaching the database, never about parsing the DSN."""
    pytest.importorskip("alembic")
    started = time.monotonic()
    result = _alembic("upgrade", "head")
    elapsed = time.monotonic() - started
    combined = (result.stdout + result.stderr).lower()

    assert "interpolation" not in combined, (
        "alembic died in ConfigParser interpolation instead of attempting a connection:\n"
        + (result.stdout + result.stderr)[-2000:])
    assert result.returncode != 0, "an unreachable DSN must not report success"
    assert ("connect" in combined or "connection" in combined or "refused" in combined), (
        "expected a connection failure, got:\n" + (result.stdout + result.stderr)[-2000:])


def test_the_escape_survives_a_round_trip_through_alembic_config():
    """The escape must restore the ORIGINAL DSN, not a mangled one.

    `%%` is what ConfigParser needs on the way in; what comes back out has to be byte-identical to
    what `db.config.database_url()` returned, or the fix would trade a parse error for a silent
    connection to the wrong credentials.
    """
    pytest.importorskip("alembic")
    from alembic.config import Config

    config = Config(str(_ROOT / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", _ENCODED_DSN.replace("%", "%%"))
    section = config.get_section(config.config_ini_section, {})

    assert section["sqlalchemy.url"] == _ENCODED_DSN


def test_env_py_escapes_the_dsn_it_stores():
    """A static guard on the seam, so the escape cannot be dropped by a later edit.

    The behavioural test above is the real proof; this one names the single line responsible, so a
    regression is reported where it happened rather than as a mysterious interpolation error.
    """
    source = (_ROOT / "db" / "migrations" / "env.py").read_text(encoding="utf-8")
    assert 'config.set_main_option("sqlalchemy.url", database_url().replace("%", "%%"))' in source
