# -*- coding: utf-8 -*-
"""Regression proof for the Gate 2C demo-database deletion incident."""
from __future__ import annotations

import pytest

from db.test_database_safety import (
    ENV_DEMO_URL,
    ENV_DESTRUCTIVE_TEST_DATABASE,
    UnsafeTestDatabase,
    require_disposable_test_database,
)


def _dsn(database: str) -> str:
    return f"postgresql+psycopg://baseera:secret@127.0.0.1:55432/{database}"


def test_the_deleted_gate2_demo_name_is_no_longer_accepted_as_test_only():
    env = {ENV_DESTRUCTIVE_TEST_DATABASE: "baseera_gate2b_test"}
    with pytest.raises(UnsafeTestDatabase, match="outside the reserved"):
        require_disposable_test_database(_dsn("baseera_gate2b_test"), environ=env)


def test_demo_database_refuses_destructive_setup_before_the_callback_can_run():
    destructive_operation_reached = False
    env = {ENV_DESTRUCTIVE_TEST_DATABASE: "baseera_demo"}

    with pytest.raises(UnsafeTestDatabase, match="outside the reserved"):
        require_disposable_test_database(_dsn("baseera_demo"), environ=env)
        destructive_operation_reached = True

    assert destructive_operation_reached is False


def test_reserved_pytest_database_still_requires_exact_operator_acknowledgement():
    with pytest.raises(UnsafeTestDatabase, match=ENV_DESTRUCTIVE_TEST_DATABASE):
        require_disposable_test_database(_dsn("baseera_pytest"), environ={})


def test_explicit_reserved_pytest_database_is_accepted():
    identity = require_disposable_test_database(
        _dsn("baseera_pytest"),
        environ={ENV_DESTRUCTIVE_TEST_DATABASE: "baseera_pytest"},
    )
    assert identity.database == "baseera_pytest"


def test_test_and_demo_dsns_can_never_identify_the_same_database():
    env = {
        ENV_DESTRUCTIVE_TEST_DATABASE: "baseera_pytest",
        ENV_DEMO_URL: _dsn("baseera_pytest"),
    }
    with pytest.raises(UnsafeTestDatabase, match="identify the same database"):
        require_disposable_test_database(_dsn("baseera_pytest"), environ=env)
