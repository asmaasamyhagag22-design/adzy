# -*- coding: utf-8 -*-
"""Where the web app stores its data, and where it reads credentials from, must not depend on the
directory uvicorn happened to be started in.

TWO measured defects, one root — a path anchored on the CWD instead of on the repo:

1. STORAGE. `webapp_dir()` defaulted to the RELATIVE "outputs/webapp". Measured on this machine:
   from the repo it resolves to `…/scraper_v01/outputs/webapp`; from `C:/Users/Admin` it resolves to
   `C:/Users/Admin/outputs/webapp`. Everything lives there — users.json, the signing secret, the
   brand catalog, generated images. Start the server one directory over and every account "does not
   exist" (401 on login), a fresh random secret invalidates every issued token, the brand catalog
   reads empty, and previously generated images 404. Nothing errors; it just silently looks like a
   new install. And `api/main.py` explicitly advertises being run from elsewhere.

2. CREDENTIALS. `config_env.load_env` exists because a real 2026-07-20 failure — a valid Meta token
   in `.env` that the pipeline could not see — was traced to `.env` being loaded from inconsistent
   places. It anchors on `__file__`. `api/main.py`, the project's most-used entry point, skipped it
   and hand-rolled a loader that searched the CWD FIRST and stopped at the first `.env` it found,
   so a stray `.env` in the launch directory silently shadows the project's own. Same failure class,
   in the module that boots everything.

Both fixes anchor on the repository, so the answer no longer depends on the question's location.
Hermetic — no server, no network."""
from __future__ import annotations

import os
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]


def test_the_storage_dir_is_the_same_from_any_working_directory(monkeypatch, tmp_path):
    from api.user_auth import webapp_dir

    monkeypatch.delenv("BASEERA_WEBAPP_DIR", raising=False)
    monkeypatch.chdir(_REPO)
    from_repo = webapp_dir().resolve()
    monkeypatch.chdir(tmp_path)
    from_elsewhere = webapp_dir().resolve()
    assert from_repo == from_elsewhere, (
        f"the web app's users/brands/images move with the CWD: {from_repo} vs {from_elsewhere}")


def test_the_storage_dir_lives_under_the_repository(monkeypatch, tmp_path):
    from api.user_auth import webapp_dir

    monkeypatch.delenv("BASEERA_WEBAPP_DIR", raising=False)
    monkeypatch.chdir(tmp_path)
    assert _REPO in webapp_dir().resolve().parents, "web-app state escaped the project folder"


def test_an_explicit_override_still_wins(monkeypatch, tmp_path):
    """Tests and containers point this elsewhere on purpose — that must keep working."""
    from api.user_auth import webapp_dir

    target = tmp_path / "isolated"
    monkeypatch.setenv("BASEERA_WEBAPP_DIR", str(target))
    assert webapp_dir().resolve() == target.resolve()


def test_a_relative_override_is_still_honoured_relative_to_the_repo(monkeypatch, tmp_path):
    from api.user_auth import webapp_dir

    monkeypatch.setenv("BASEERA_WEBAPP_DIR", "outputs/custom_webapp")
    monkeypatch.chdir(tmp_path)
    assert webapp_dir().resolve() == (_REPO / "outputs" / "custom_webapp").resolve()


def test_the_api_entry_point_loads_env_through_the_shared_loader():
    """Falsifiable: re-introduce a bespoke CWD-first loader in main.py and this fails."""
    src = (_REPO / "api" / "main.py").read_text(encoding="utf-8")
    assert "config_env" in src, "api/main.py does not use the one shared .env loader"
    assert "Path.cwd()" not in src, (
        "api/main.py still searches the CWD for a .env — a stray file in the launch directory "
        "silently shadows the project's credentials (the 2026-07-20 failure class)")


def test_the_shared_loader_reads_the_repos_env_regardless_of_cwd(monkeypatch, tmp_path):
    """The behaviour behind the assertion above: a decoy .env in the CWD must not win."""
    import config_env

    (tmp_path / ".env").write_text("BASEERA_PATH_PROBE=decoy\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("BASEERA_PATH_PROBE", raising=False)
    config_env.load_env(force=True)
    assert os.environ.get("BASEERA_PATH_PROBE") != "decoy", "the decoy .env in the CWD was loaded"
