"""Offline legacy-to-PostgreSQL migration tooling.

Nothing imports this package from the live application.  Operators invoke the explicit CLI in
``db.migration_tools.cli`` against a source snapshot, a staging root, and a disposable database.
"""
from __future__ import annotations

__all__: list[str] = []
