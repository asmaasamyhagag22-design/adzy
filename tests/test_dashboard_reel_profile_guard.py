# -*- coding: utf-8 -*-
"""P-37 (2026-07-18): dashboard.generate_reel only checked `P["profile"].is_file()`, so an EMPTY
scrape ('{}' on disk — which is a file) launched a full Veo reel subprocess and fabricated a reel
for a brand it knows nothing about. generate_poster already refuses via `_profile_is_usable`
(P-43); the reel must apply the same last-line guard and spawn NO subprocess. Hermetic — the
subprocess runner is stubbed and asserted never to fire."""
from __future__ import annotations

import json

import dashboard.run as run


def _write_profile(tmp_path, slug, payload):
    P = run.paths(slug, str(tmp_path))
    P["profile"].parent.mkdir(parents=True, exist_ok=True)
    P["profile"].write_text(json.dumps(payload), encoding="utf-8")
    return P


def test_empty_profile_generates_no_reel_and_no_subprocess(tmp_path, monkeypatch):
    ran = {"n": 0}
    monkeypatch.setattr(run, "_run", lambda *a, **k: ran.__setitem__("n", ran["n"] + 1) or (True, ""))
    _write_profile(tmp_path, "empty", {})                       # empty scrape: a file, but unusable
    out = run.generate_reel("empty", out_dir=str(tmp_path))
    assert out is None
    assert ran["n"] == 0, "an empty profile must NOT launch the reel subprocess"


def test_usable_profile_still_launches_the_reel(tmp_path, monkeypatch):
    ran = {"n": 0}

    def _fake_run(cmd, **k):
        ran["n"] += 1
        # simulate the subprocess writing the reel artifact so the is_file() check passes
        P = run.paths("good", str(tmp_path))
        P["reel"].parent.mkdir(parents=True, exist_ok=True)
        P["reel"].write_bytes(b"\x00")
        return True, ""

    monkeypatch.setattr(run, "_run", _fake_run)
    _write_profile(tmp_path, "good", {"name": {"value": "Baseera Clinic"}})
    out = run.generate_reel("good", out_dir=str(tmp_path))
    assert out is not None and ran["n"] == 1
