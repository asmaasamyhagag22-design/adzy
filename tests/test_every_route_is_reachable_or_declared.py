# -*- coding: utf-8 -*-
"""A route with no way to reach it is a feature nobody has.

This is the structural guard for the failure class that has cost this project more than any bug:
something is built, tested, serialized — and then dies one step before the screen. It has now been
found at four different layers in a single day:

  * a whole second dashboard nobody opened (deleted 2026-07-26),
  * a SWOT computed by the CLI that the webapp could not see,
  * `reel/sfx.py`, committed the hour the account was funded and never called,
  * `POST /api/influencer/another` and `/select` — both working, both with ZERO call sites, which
    left the UI in a state where the server answered an approval with the literal sentence
    "choose one with /api/influencer/select {candidate_id}" and no control could say which.

So: every route either has a call site in the SPA, or is named here with the reason it does not.
Adding a route without wiring it is then a deliberate, written decision instead of an oversight.
"""
from __future__ import annotations

import re
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]

# Routes that are deliberately API-only. Each needs a reason — "we forgot" is not one.
API_ONLY = {
    "/health": "liveness probe; infrastructure, not a user action",
    "/run": "whole-pipeline runner for scripts and cron; the UI drives the steps separately",
    "/poster/from-profile": "stateless profile-in/poster-out variant; the Studio uses /generate "
                            "because it works off a stored brand",
    "/swot/from-profile": "stateless variant of /swot/generate for callers with no stored brand",
}

_ROUTE = re.compile(r'@router\.(get|post|delete|put|patch)\(\s*["\']([^"\']+)')


def _routes():
    out = []
    for p in sorted((_ROOT / "api" / "routes").glob("*.py")):
        src = p.read_text(encoding="utf-8", errors="replace")
        for m in _ROUTE.finditer(src):
            out.append((m.group(1).upper(), m.group(2), p.name))
    return out


def test_every_route_is_either_wired_or_declared_api_only():
    spa = (_ROOT / "frontend" / "index.html").read_text(encoding="utf-8", errors="replace")
    orphans = []
    for verb, path, fname in _routes():
        stem = path.split("{")[0].rstrip("/")
        if stem and stem in spa:
            continue
        if path in API_ONLY:
            continue
        orphans.append(f"{verb} {path} ({fname})")
    assert not orphans, (
        "these routes cannot be reached from the app and are not declared API-only:\n  "
        + "\n  ".join(orphans)
        + "\nEither add a call site in frontend/index.html or add the route to API_ONLY with the "
          "reason it has no UI.")


def test_the_api_only_list_has_no_stale_entries():
    """A declared exception that no longer names a real route is rot — it hides the next one."""
    live = {path for _v, path, _f in _routes()}
    stale = sorted(set(API_ONLY) - live)
    assert not stale, f"API_ONLY names routes that no longer exist: {stale}"


def test_every_api_only_route_states_why():
    thin = sorted(p for p, why in API_ONLY.items() if len(why.split()) < 4)
    assert not thin, f"these exceptions have no real reason recorded: {thin}"
