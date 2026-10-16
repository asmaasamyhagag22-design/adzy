# -*- coding: utf-8 -*-
"""C7: every api module must import on its own, in a process that has imported nothing else.

`api.webapp_images` imported `selected_face_bytes` from `api.webapp_influencer`, while
`api.webapp_influencer` imported `api.webapp_images`. Each needed the other to be finished first.
Whichever was imported first in a fresh process handed the second a half-built module, so
`import api.webapp_influencer` raised ImportError on its own — and `import api.main` worked, purely
because it happened to reach `webapp_images` first. A test that ran after any other api test would
find the cycle already resolved and see nothing wrong.

Which is why every check here runs in a SUBPROCESS. Import order is global, one-shot process
state; asserting on it from inside a suite that has already imported half the package proves
nothing. `-s` keeps the user's site-packages out; the repo root is put on the path explicitly
rather than with `-I`, which would also strip the working directory and make every import fail for
the wrong reason.

The cycle is gone, not hidden: `webapp_influencer -> webapp_images`, one way, with the function
defined where the assets it reads actually live.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]

# Every module under `api/`, discovered rather than listed, so a new one cannot quietly skip this.
_MODULES = sorted(
    ".".join(p.relative_to(_ROOT).with_suffix("").parts)
    for p in (_ROOT / "api").rglob("*.py")
    if "__pycache__" not in p.parts and p.name != "__init__.py"
)

# Modules that legitimately need something this environment does not configure. Empty today, and
# deliberately a CLASSIFICATION rather than a blanket try/except: a module that stops importing
# must land here with a reason, in review, not disappear into a silent skip.
_NEEDS_OPTIONAL_CONFIG: dict[str, str] = {}


def _fresh(code: str) -> subprocess.CompletedProcess:
    """Run `code` in a brand-new interpreter that has imported nothing from this package yet."""
    env = {**os.environ, "PYTHONPATH": str(_ROOT), "PYTHONDONTWRITEBYTECODE": "1"}
    return subprocess.run(
        [sys.executable, "-s", "-c", code],
        cwd=str(_ROOT), capture_output=True, text=True, timeout=180, env=env,
    )


def _import_in_fresh_process(module: str) -> subprocess.CompletedProcess:
    return _fresh(f"import {module}")


def test_the_module_list_was_actually_discovered():
    assert len(_MODULES) >= 15, f"discovery found only {len(_MODULES)} api modules"
    assert "api.webapp_images" in _MODULES
    assert "api.webapp_influencer" in _MODULES


@pytest.mark.parametrize("module", _MODULES)
def test_every_api_module_imports_alone_in_a_fresh_process(module):
    if module in _NEEDS_OPTIONAL_CONFIG:
        pytest.skip(f"{module}: {_NEEDS_OPTIONAL_CONFIG[module]}")
    proc = _import_in_fresh_process(module)
    assert proc.returncode == 0, (
        f"`import {module}` fails in a fresh process:\n{proc.stderr.strip()[-1500:]}")


@pytest.mark.parametrize("first,second", [
    ("api.webapp_images", "api.webapp_influencer"),
    ("api.webapp_influencer", "api.webapp_images"),
    ("api.routes.influencer", "api.routes.generate"),
    ("api.routes.generate", "api.routes.influencer"),
])
def test_the_pair_imports_in_either_order(first, second):
    """The specific cycle, from both directions. Order must not decide whether the app starts."""
    proc = _fresh(f"import {first}; import {second}")
    assert proc.returncode == 0, (
        f"`import {first}` then `import {second}` fails:\n{proc.stderr.strip()[-1500:]}")


def test_the_dependency_runs_one_way_only():
    """webapp_images must not import back from its own consumer."""
    src = (_ROOT / "api" / "webapp_images.py").read_text(encoding="utf-8")
    assert "from api.webapp_influencer import" not in src, "the cycle is back"
    assert "from .webapp_influencer import" not in src, "the cycle is back"
    assert "import api.webapp_influencer" not in src, "the cycle is back"


def test_selected_face_bytes_is_reachable_from_both_module_paths():
    """The move must not break either import path callers already use."""
    proc = _fresh(
        "from api.webapp_images import selected_face_bytes as a;"
        "from api.webapp_influencer import selected_face_bytes as b;"
        "assert a is b, 'the re-export drifted from the definition';"
        "print('ok')")
    assert proc.returncode == 0, proc.stderr.strip()[-1500:]
    assert "ok" in proc.stdout


def test_selected_face_bytes_still_behaves():
    """Moved, not rewritten: strictly the SELECTED candidate, and honest about a missing file."""
    from api import webapp_images as images

    assert images.selected_face_bytes({}) == (None, "")
    assert images.selected_face_bytes(
        {"ai_influencer": {"candidates": {"c1": {"url": "/api/generated/x.png"}},
                           "selected_id": ""}}) == (None, ""), "an unselected face was returned"
    assert images.selected_face_bytes(
        {"ai_influencer": {"candidates": {"c1": {"url": "/api/generated/missing.png"}},
                           "selected_id": "c1"}}) == (None, ""), "a missing file was not reported"
