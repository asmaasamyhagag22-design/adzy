# -*- coding: utf-8 -*-
"""Persistence-domain failures.

These errors deliberately carry no HTTP status.  Phase 2G may translate them at the route
boundary; repositories and services must remain usable by workers and command-line callers.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import NoReturn


class PersistenceError(RuntimeError):
    """Base class for expected persistence/domain failures."""


class NotFound(PersistenceError):
    pass


class NotVisible(PersistenceError):
    pass


class ForeignOwner(PersistenceError):
    pass


class UnclaimedBrand(PersistenceError):
    pass


class ArchivedBrand(PersistenceError):
    pass


class ClaimLost(PersistenceError):
    pass


class InvalidTransition(PersistenceError):
    pass


class TerminalState(InvalidTransition):
    pass


class ApprovalMissing(PersistenceError):
    pass


class ApprovalIneligible(PersistenceError):
    pass


class StaleApprovalSubject(PersistenceError):
    pass


class ConcurrencyConflict(PersistenceError):
    pass


class AlreadyExists(PersistenceError):
    pass


class AssetNotDurable(PersistenceError):
    pass


class AmbiguousCurrentRun(PersistenceError):
    pass


def sqlstate(error: BaseException) -> str:
    """Return psycopg's SQLSTATE without depending on psycopg at import time."""
    original = getattr(error, "orig", error)
    return str(getattr(original, "sqlstate", "") or getattr(original, "pgcode", "") or "")


def constraint_name(error: BaseException) -> str:
    """Return the PostgreSQL constraint name when the driver supplied one."""
    original = getattr(error, "orig", error)
    diagnostic = getattr(original, "diag", None)
    return str(getattr(diagnostic, "constraint_name", "") or "")


def raise_mapped_integrity(
    error: BaseException,
    expected: Mapping[tuple[str, str], tuple[type[PersistenceError], str]],
) -> NoReturn:
    """Map only an explicitly named SQLSTATE/constraint pair; never relabel unknown defects."""
    mapped = expected.get((sqlstate(error), constraint_name(error)))
    if mapped is None:
        raise error
    error_type, message = mapped
    raise error_type(message) from error


__all__ = [
    "AlreadyExists", "AmbiguousCurrentRun", "ApprovalIneligible", "ApprovalMissing",
    "StaleApprovalSubject", "ArchivedBrand",
    "AssetNotDurable", "ClaimLost", "ConcurrencyConflict", "ForeignOwner",
    "InvalidTransition", "NotFound", "NotVisible", "PersistenceError", "TerminalState",
    "UnclaimedBrand", "constraint_name", "raise_mapped_integrity", "sqlstate",
]
