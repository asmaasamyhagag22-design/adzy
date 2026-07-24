# -*- coding: utf-8 -*-
"""Old creative must never be presented as if it were current.

Owner (2026-07-20, NTI): the analysis was re-run TODAY while the poster and reel on the page were
from July 12 — eight days old, from a superseded plan — with nothing saying so. She read the page as
"it brought old stuff", and rightly.

Universal rule: an artifact OLDER than the analysis it sits next to is STALE. The studio says so on
the artifact itself, and offers a fresh start that ARCHIVES (never deletes) the old files. Hermetic."""
from __future__ import annotations

import time
from pathlib import Path

from dashboard.run import archive_creative
from dashboard.war_room import _staleness_note


def _touch(p: Path, when: float) -> Path:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"x")
    import os
    os.utime(p, (when, when))
    return p


def test_creative_older_than_the_analysis_is_marked_stale(tmp_path: Path):
    now = time.time()
    result = _touch(tmp_path / "b_result.json", now)
    art = _touch(tmp_path / "posters" / "b_poster.png", now - 8 * 86400)
    note = _staleness_note(art, result)
    assert note and ("قديم" in note or "أقدم" in note)
    assert "8" in note                                     # how old, in days — not a vague warning


def test_creative_newer_than_the_analysis_is_not_marked(tmp_path: Path):
    now = time.time()
    result = _touch(tmp_path / "b_result.json", now - 3600)
    art = _touch(tmp_path / "posters" / "b_poster.png", now)
    assert _staleness_note(art, result) == ""


def test_missing_files_never_produce_a_warning(tmp_path: Path):
    assert _staleness_note(tmp_path / "nope.png", tmp_path / "b_result.json") == ""
    assert _staleness_note(None, None) == ""


def test_archive_moves_creative_out_without_deleting(tmp_path: Path):
    poster = _touch(tmp_path / "posters" / "b_poster.png", time.time())
    reel = _touch(tmp_path / "reels" / "b_reel.mp4", time.time())
    plan = _touch(tmp_path / "b_reel_plan.json", time.time())
    moved = archive_creative("b", out_dir=str(tmp_path))
    assert len(moved) >= 3
    assert not poster.exists() and not reel.exists() and not plan.exists()
    archived = list((tmp_path / "_archive" / "b").rglob("*"))
    names = {p.name for p in archived if p.is_file()}
    assert {"b_poster.png", "b_reel.mp4", "b_reel_plan.json"} <= names   # kept, not destroyed


def test_archive_is_a_noop_when_there_is_nothing_to_clear(tmp_path: Path):
    assert archive_creative("b", out_dir=str(tmp_path)) == []
    assert not (tmp_path / "_archive").exists()            # no empty folders left behind
