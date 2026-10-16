# -*- coding: utf-8 -*-
"""Deciding whether a DSN may be written to must not require the competitor domain.

MEASURED on the live hosted Phase C run, 2026-08-23. The cutover target gate reads two constant
sets out of `db/migration_tools/apply.py`, and from a standalone `db/` bundle that import died:

    phase_c                      from db.migration_tools.apply import _REQUIRED_CONSTRAINTS
      db/migration_tools/apply.py     :19  from db.migration_tools.assets import ...
        db/migration_tools/assets.py  :9   from db.migration_tools.inventory import ...
          db/migration_tools/inventory.py :10  from competitor.registry import ...
    ModuleNotFoundError: No module named 'competitor'

`competitor/registry.py` itself is stdlib-only, but importing it runs `competitor/__init__.py`,
which is a façade over `.models`, `.peer_match`, `.places_client`, `.discovery`, `.matrix`,
`.swot`, `.tows`, `.themes`, `.business_type` and `.router`. So the alternative to this fix was
shipping the entire competitor domain and its transitive dependencies in order to read fourteen
constraint names.

Both helpers were used inside function bodies only — the coupling was import-time and nothing else,
which is why the fix is to import them where they are used. Nothing about the guard's BEHAVIOUR
changed, and this file proves that by exercising it, not merely importing it.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]

# Runs in a fresh interpreter so nothing a previous import already cached can hide the coupling.
_PROBE = '''
import sys


class _BlockCompetitor:
    """Make the competitor package unimportable, the way a standalone db bundle does."""

    def find_spec(self, name, path=None, target=None):
        if name == "competitor" or name.startswith("competitor."):
            raise ModuleNotFoundError("No module named %r (blocked by the test)" % name)
        return None


sys.meta_path.insert(0, _BlockCompetitor())

from db.migration_tools.apply import (            # noqa: E402
    _REQUIRED_CONSTRAINTS, _REQUIRED_INDEXES, _guarded_engine,
)
from db.migration_tools.records import TargetSafetyError   # noqa: E402

assert "competitor" not in sys.modules, "the competitor package was imported after all"

results = []
try:
    _guarded_engine("postgresql+psycopg://u:p@h:5432/postgres")
    results.append("production-looking DSN was ALLOWED")
except TargetSafetyError as exc:
    results.append("no-override:%s" % exc)
try:
    _guarded_engine("postgresql+psycopg://u:p@h:5432/postgres", production_target="wrong")
    results.append("mismatched override was ALLOWED")
except TargetSafetyError as exc:
    results.append("wrong-override:%s" % exc)
engine = _guarded_engine("postgresql+psycopg://u:p@h:5432/baseera_tmp")
results.append("disposable:ALLOWED")
engine.dispose()

print("CONSTRAINTS=%d INDEXES=%d" % (len(_REQUIRED_CONSTRAINTS), len(_REQUIRED_INDEXES)))
print(" | ".join(results))
'''


def _run_probe() -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-c", _PROBE], cwd=str(_ROOT),
                          capture_output=True, text=True, timeout=180)


def test_the_target_guard_imports_and_runs_without_the_competitor_package():
    result = _run_probe()
    assert result.returncode == 0, (
        "the cutover target gate still needs the competitor domain:\n"
        + (result.stdout + result.stderr)[-2000:])
    assert "CONSTRAINTS=14 INDEXES=1" in result.stdout, result.stdout
    # The guard must still REFUSE by default and still refuse a mismatched override. An import fix
    # that quietly opened the door would pass a test that only checked the import.
    assert "no-override:TARGET_DATABASE_IS_NOT_DECLARED_DISPOSABLE" in result.stdout, result.stdout
    assert "wrong-override:PRODUCTION_TARGET_DOES_NOT_MATCH_THE_DSN_DATABASE" in result.stdout, \
        result.stdout
    assert "disposable:ALLOWED" in result.stdout, result.stdout


def test_apply_does_not_import_the_corpus_readers_at_module_scope():
    """A static guard, so a re-coupling is reported where it happens.

    The behavioural test above is the real proof; this one names the two lines that must stay out
    of module scope, because the next person to add `from db.migration_tools.inventory import ...`
    at the top of this file would not otherwise learn why it breaks a hosted run.
    """
    source = (_ROOT / "db" / "migration_tools" / "apply.py").read_text(encoding="utf-8")
    module_scope, _, _ = source.partition("\ndef ")

    for offender in ("from db.migration_tools.assets import",
                     "from db.migration_tools.inventory import"):
        assert offender not in module_scope, (
            f"{offender!r} is back at module scope in apply.py; that re-imports the competitor "
            f"domain for every caller of the target guard")
        assert offender in source, (
            f"{offender!r} disappeared entirely — the helpers must still be imported where used")


def test_the_deferred_imports_still_resolve():
    """Deferring an import trades an import-time failure for a call-time one; it must not fail."""
    from db.migration_tools.assets import verify_source_binding, verify_staged_assets
    from db.migration_tools.inventory import SourceLayout

    assert callable(verify_source_binding)
    assert callable(verify_staged_assets)
    assert hasattr(SourceLayout, "resolve")
