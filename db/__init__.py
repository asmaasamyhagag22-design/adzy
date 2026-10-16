"""Baseera persistence layer — PostgreSQL schema foundation.

NOT WIRED. Nothing in `api/` imports this package yet, and importing it opens no connection.
BrandStore, JobStore and UserStore remain the live authorities; cutover is a later, explicit
phase. This package exists so the schema can be reviewed as code before anything depends on it.

LAYERING (the C7 lesson, applied before the cycle can form): this package may be imported BY the
api/orchestration layer and must never import FROM it. The dependency runs

    api / routes  ->  domain services  ->  db

and never back. `tests/test_db_foundation.py` asserts that direction rather than trusting it.
"""
from __future__ import annotations

__all__ = ["Base", "metadata"]


def __getattr__(name: str):
    # Lazy re-export so `import db` costs nothing and needs no driver installed.
    if name in ("Base", "metadata"):
        from db.base import Base, metadata
        return {"Base": Base, "metadata": metadata}[name]
    raise AttributeError(name)
