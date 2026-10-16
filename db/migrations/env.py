# -*- coding: utf-8 -*-
"""Alembic environment.

The DSN comes from `db.config`, never from alembic.ini, so there is exactly one place a database
URL is read and no credentials live in a tracked file.
"""
from __future__ import annotations

from alembic import context
from sqlalchemy import engine_from_config, pool

from db.base import metadata as target_metadata  # noqa: F401
from db.config import connect_timeout_seconds, database_url
from db.models import Base  # noqa: F401  — importing registers every table on the metadata

config = context.config
# `set_main_option` hands the value to ConfigParser, which applies pyformat interpolation when the
# section is read back — so a RAW `%` in a DSN raises `ValueError: invalid interpolation syntax`
# before a connection is ever attempted. A password containing a URL-reserved character (`#`, `/`,
# `?`, `@`, `:`) must be percent-encoded to survive the URL, which puts a literal `%` in the DSN,
# so this is not an exotic case: it is what any correctly-encoded hosted password looks like.
# Alembic's own documentation states the escape; without it `alembic upgrade` dies at config
# parsing and the operator sees an interpolation error instead of a database error.
config.set_main_option("sqlalchemy.url", database_url().replace("%", "%%"))


def run_migrations_offline() -> None:
    context.configure(
        url=database_url(), target_metadata=target_metadata,
        literal_binds=True, dialect_opts={"paramstyle": "named"},
        compare_type=True, compare_server_default=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def _configure_and_run(connection) -> None:
    """One definition of how a revision is applied, whoever owns the connection."""
    context.configure(
        connection=connection, target_metadata=target_metadata,
        compare_type=True, compare_server_default=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    # A caller may supply its own Connection — Alembic's documented programmatic pattern. This is
    # INERT on the command line: `config.attributes` is empty for a CLI invocation, so
    # `alembic upgrade` takes the branch below and behaves exactly as it did before.
    #
    # It exists because the hosted target is reached through Supavisor, which does not forward the
    # client's startup packet to PostgreSQL. MEASURED against the live pooler on 2026-08-23: the
    # `options` connect argument, the same parameter on the DSN, and `PGOPTIONS` ALL failed to move
    # `search_path`, while a `SET` issued as an ordinary statement held for the session. So the only
    # way to run a hosted migration inside a disposable schema is on a connection the caller has
    # already prepared and VERIFIED — which is also the only form of isolation that can be checked
    # before the first DDL rather than hoped for.
    injected = config.attributes.get("connection")
    if injected is not None:
        _configure_and_run(injected)
        return

    # The same bounded-connect rule `db/session.py` applies, for the same reason: libpq waits
    # forever by default, so `alembic upgrade` against a wrong or unreachable host HANGS instead
    # of reporting that it cannot reach the database. During a remote cutover that is the worst
    # possible failure mode — an operator watching a migration that will never start or refuse.
    # The value is read from `db.config`, so there is one definition of it, not a second number.
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.", poolclass=pool.NullPool,
        connect_args={"connect_timeout": connect_timeout_seconds()},
    )
    with connectable.connect() as connection:
        _configure_and_run(connection)


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
