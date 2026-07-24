# -*- coding: utf-8 -*-
"""Every entry point must see `.env` — otherwise a correct credential does nothing.

MEASURED (2026-07-20): the owner completed Meta identity confirmation, generated a working Ad
Library token and put it in `.env` — and the ads pull still reported "META_ADS_TOKEN is not set".
`.env` was loaded in exactly three places (`business_profile/__main__`, `dashboard/run._hitl_env`,
`competitor/places_client`), none of which run before an ANALYSIS. So `autopull_enabled()` read an
empty environment and competitor-ads intel would have stayed empty forever, with no error anywhere
— the worst kind of failure: silent, and indistinguishable from "this brand runs no ads".

One shared loader, called by every entry point. Hermetic."""
from __future__ import annotations

import os
from pathlib import Path

from config_env import load_env


def test_load_env_reads_the_project_dotenv(tmp_path: Path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("SOME_TEST_KEY=hello\n", encoding="utf-8")
    monkeypatch.delenv("SOME_TEST_KEY", raising=False)
    load_env(env_path=env)
    assert os.environ.get("SOME_TEST_KEY") == "hello"


def test_real_environment_wins_over_the_file(tmp_path: Path, monkeypatch):
    # A shell-exported value is the deliberate override (CI, a one-off run) and must not be
    # clobbered by a stale line in .env.
    env = tmp_path / ".env"
    env.write_text("SOME_TEST_KEY=from_file\n", encoding="utf-8")
    monkeypatch.setenv("SOME_TEST_KEY", "from_shell")
    load_env(env_path=env)
    assert os.environ.get("SOME_TEST_KEY") == "from_shell"


def test_missing_file_is_silent(tmp_path: Path):
    load_env(env_path=tmp_path / "nope.env")          # must not raise


def test_every_credential_entry_point_actually_sees_dotenv():
    """The regression guard, measured by BEHAVIOUR rather than by a string in the source.

    A grep for "load_env" passes on `competitor/full_run.py` because it happens to have its own
    `_load_env` helper — the same coincidence that would let this bug back in. So each module is
    imported in a CLEAN subprocess with the key scrubbed from the environment, and must make it
    visible on its own. `META_APP_ID` is used as the probe (present in .env, not a secret to
    compare against — only its presence is asserted, never its value).
    """
    import subprocess
    import sys
    root = Path(__file__).resolve().parents[1]
    if not (root / ".env").is_file():
        import pytest
        pytest.skip("no .env in this environment (CI) — nothing to load")
    env = {k: v for k, v in os.environ.items() if k != "META_APP_ID"}
    env["PYTHONPATH"] = str(root)
    for module in ("scripts.pull_meta_ads", "competitor.full_run",
                   "ads_intel.orchestrator", "dashboard.server"):
        code = (f"import importlib, os; importlib.import_module('{module}'); "
                "print(bool(os.environ.get('META_APP_ID')))")
        r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                           cwd=str(root), env=env, timeout=180)
        assert "True" in r.stdout, (
            f"{module} does not load .env — a correct credential there does nothing "
            f"(stdout={r.stdout!r} stderr={r.stderr[-300:]!r})")
