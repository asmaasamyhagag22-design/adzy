"""P-43: a poster must never be fabricated from an empty/contentless profile (an empty scrape).
That is how Marasim (a media agency) got a furniture "Summer Sale" poster while a good 11-day-old
scrape sat on disk. The studio generate entry must refuse a bare `{}` profile."""
from __future__ import annotations

import json

import dashboard.run as run
from dashboard.run import _profile_is_usable, generate_poster, paths


def _write(tmp_path, slug, obj):
    p = paths(slug, str(tmp_path))
    p["profile"].parent.mkdir(parents=True, exist_ok=True)
    p["profile"].write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")
    return p["profile"]


def test_empty_profile_is_not_usable(tmp_path):
    assert _profile_is_usable(_write(tmp_path, "empty", {})) is False


def test_named_or_offering_profile_is_usable(tmp_path):
    assert _profile_is_usable(_write(tmp_path, "named", {"name": {"value": "Marasim"}})) is True
    assert _profile_is_usable(_write(tmp_path, "offer", {"offerings": [{"name": "x"}]})) is True
    # nested under a "profile" key (competitor result shape)
    assert _profile_is_usable(_write(tmp_path, "nested", {"profile": {"name": {"value": "X"}}})) is True


def test_invalid_json_is_not_usable(tmp_path):
    p = paths("bad", str(tmp_path))["profile"]
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("{not json", encoding="utf-8")
    assert _profile_is_usable(p) is False


def test_generate_poster_refuses_empty_profile_without_spawning(tmp_path, monkeypatch):
    _write(tmp_path, "marasim", {})   # the empty-scrape profile
    called = {"ran": False}
    monkeypatch.setattr(run, "_run", lambda *a, **k: called.__setitem__("ran", True) or (True, ""))
    out = generate_poster("marasim", out_dir=str(tmp_path))
    assert out is None
    assert called["ran"] is False     # the poster subprocess was NEVER launched
