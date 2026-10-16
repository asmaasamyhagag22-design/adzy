# -*- coding: utf-8 -*-
"""A caller may hand Alembic its own Connection, and the migration goes where that connection points.

WHY THIS EXISTS — measured against the live Supabase Session Pooler on 2026-08-23.

Every startup-packet route for pinning `search_path` failed through Supavisor: the `options`
connect argument, the same parameter on the DSN, and `PGOPTIONS`. All three deliver the setting in
the client's startup packet, and Supavisor does not forward that packet verbatim to PostgreSQL — it
hands the client one of its own pooled backend connections. So a hosted rehearsal cannot be isolated
into a disposable schema at connect time.

A `SET search_path` issued as an ordinary statement DOES hold: the same live run proved that a
session setting made in one transaction is still readable in the next on the same connection. That
leaves exactly one way to isolate a hosted migration — run it on a connection the CALLER has already
prepared and verified — and Alembic's documented programmatic pattern is `config.attributes`.

Before the change, `env.py` ignored an injected connection and built its own engine. MEASURED: a
connection whose `current_schema()` was a disposable schema, with `public` absent from its search
path, still produced 18 tables in `public`. On the hosted target that is the production database.

The CLI path is unchanged and is covered by the existing migration suites; `config.attributes` is
empty for a command-line invocation, so nothing here can be reached from `alembic upgrade`.
"""
from __future__ import annotations

import os
import uuid
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]

pytestmark = pytest.mark.skipif(
    not os.environ.get("BASEERA_TEST_DATABASE_URL"),
    reason="BASEERA_TEST_DATABASE_URL is not set — no disposable PostgreSQL to prove against",
)

pytest.importorskip("sqlalchemy", reason="SQLAlchemy is not installed")
pytest.importorskip("alembic", reason="alembic is not installed")


def _guarded_url() -> str:
    from db.config import database_url
    from db.test_database_safety import require_disposable_test_database

    url = database_url(test=True)
    require_disposable_test_database(url)
    return url


@pytest.fixture()
def disposable_schema(monkeypatch):
    """A throwaway schema in the disposable test database, dropped however the test ends."""
    from sqlalchemy import create_engine, text

    url = _guarded_url()
    # env.py reads the application variable; point it at the same disposable database so the
    # non-injected branch could not reach anything else even if it were taken.
    monkeypatch.setenv("BASEERA_DATABASE_URL", url)
    monkeypatch.delenv("PGOPTIONS", raising=False)

    name = "adzy_pytest_" + uuid.uuid4().hex[:8]
    engine = create_engine(url, connect_args={"connect_timeout": 5})
    with engine.begin() as conn:
        conn.execute(text('CREATE SCHEMA "%s"' % name))
    try:
        yield engine, name
    finally:
        with engine.begin() as conn:
            conn.execute(text('DROP SCHEMA IF EXISTS "%s" CASCADE' % name))
        engine.dispose()


def test_a_migration_lands_in_the_schema_the_injected_connection_points_at(disposable_schema):
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import text

    from db.migration_tools.records import EXPECTED_SCHEMA_HEAD

    engine, schema = disposable_schema
    connection = engine.connect()
    try:
        connection.execute(text('SET search_path TO "%s"' % schema))
        connection.commit()          # a session-scoped SET survives the commit; SET LOCAL would not

        assert connection.execute(text("SELECT current_schema()")).scalar() == schema
        effective = connection.execute(text("SELECT current_setting('search_path')")).scalar()
        assert "public" not in [p.strip().strip('"') for p in effective.split(",")], (
            "the isolation is only real while public is off the effective search path")

        config = Config(str(_ROOT / "alembic.ini"))
        config.attributes["connection"] = connection
        command.upgrade(config, "head")
        connection.commit()
    finally:
        connection.close()

    with engine.connect() as conn:
        in_schema = conn.execute(text(
            "SELECT count(*) FROM pg_tables WHERE schemaname = :s"), {"s": schema}).scalar()
        head_lives_in = conn.execute(text(
            "SELECT schemaname FROM pg_tables WHERE tablename = 'alembic_version' "
            "AND schemaname = :s"), {"s": schema}).scalar()
        head = conn.execute(text(
            'SELECT version_num FROM "%s".alembic_version' % schema)).scalar()

    assert in_schema > 1, "the migration did not run against the injected connection"
    assert head_lives_in == schema, "alembic_version was written outside the disposable schema"
    assert head == EXPECTED_SCHEMA_HEAD


def test_the_injected_connection_is_the_one_used(disposable_schema):
    """No second connection is opened behind the caller's back.

    The point of injecting is that the caller has already proven what that connection can reach.
    A migration that quietly opened its own would be isolated by nothing.
    """
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import text

    engine, schema = disposable_schema
    connection = engine.connect()
    try:
        connection.execute(text('SET search_path TO "%s"' % schema))
        connection.commit()
        pid = connection.execute(text("SELECT pg_backend_pid()")).scalar()

        config = Config(str(_ROOT / "alembic.ini"))
        config.attributes["connection"] = connection
        command.upgrade(config, "0001_initial_schema")
        connection.commit()

        after = connection.execute(text("SELECT pg_backend_pid()")).scalar()
    finally:
        connection.close()

    assert pid == after
    with engine.connect() as conn:
        owner = conn.execute(text(
            "SELECT schemaname FROM pg_tables WHERE tablename = 'users' AND schemaname = :s"),
            {"s": schema}).scalar()
    assert owner == schema


def test_without_an_injected_connection_env_py_still_builds_its_own_engine():
    """The command-line path must not change. `config.attributes` is empty for a CLI run."""
    source = (_ROOT / "db" / "migrations" / "env.py").read_text(encoding="utf-8")

    assert "engine_from_config(" in source, "the CLI path must still build an engine"
    assert 'config.attributes.get("connection")' in source
    assert "connect_args={\"connect_timeout\": connect_timeout_seconds()}" in source, (
        "the CLI engine must keep its bounded connect")
