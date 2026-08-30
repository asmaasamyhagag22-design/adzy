# -*- coding: utf-8 -*-
"""A poster that FAILED the gate must never become the delivered file.

OWNER-FACING FAILURE (Volume Qatar, 2026-07-21): the delivered poster read "speciaity coffee
coffee" and "Mall of Maqur". The verbatim-text gate had correctly REJECTED that render
(fidelity 0.667 < 1.0) — but the canonical sink was refreshed from every attempt the moment it was
generated, so the rejected image overwrote the good poster on disk anyway.

The sink exists for a real reason (a later timeout must still leave a served poster, البند 1), so
it is kept — but it now only ever receives renders that CLEARED the gates. A rejected attempt stays
in `_attempts/` where it can be inspected, and the previous good deliverable survives untouched."""
from __future__ import annotations

from pathlib import Path

from poster.pipeline import _CANONICAL_SINK, _refresh_canonical


def _setup(tmp_path: Path) -> tuple[Path, Path]:
    canonical = tmp_path / "brand_poster.png"
    canonical.write_bytes(b"GOOD-POSTER")
    attempt = tmp_path / "_attempts" / "try2.png"
    attempt.parent.mkdir(parents=True, exist_ok=True)
    attempt.write_bytes(b"REJECTED-RENDER")
    _CANONICAL_SINK["path"] = canonical
    return canonical, attempt


def test_a_gate_rejected_attempt_never_replaces_the_deliverable(tmp_path: Path):
    canonical, attempt = _setup(tmp_path)
    try:
        _refresh_canonical(attempt, passed=False)
        assert canonical.read_bytes() == b"GOOD-POSTER"
    finally:
        _CANONICAL_SINK["path"] = None


def test_a_passing_attempt_does_replace_it(tmp_path: Path):
    canonical, attempt = _setup(tmp_path)
    try:
        _refresh_canonical(attempt, passed=True)
        assert canonical.read_bytes() == b"REJECTED-RENDER"   # the new, PASSING render
    finally:
        _CANONICAL_SINK["path"] = None


def test_the_first_render_still_lands_when_nothing_exists_yet(tmp_path: Path):
    # The sink's original purpose: a later timeout must still leave SOMETHING served. With no
    # previous deliverable, a not-yet-verified render is better than an empty page — but only
    # then, and the caller's status note says it is unverified.
    canonical = tmp_path / "brand_poster.png"
    attempt = tmp_path / "_attempts" / "try1.png"
    attempt.parent.mkdir(parents=True, exist_ok=True)
    attempt.write_bytes(b"FIRST")
    _CANONICAL_SINK["path"] = canonical
    try:
        _refresh_canonical(attempt, passed=False)
        assert canonical.is_file() and canonical.read_bytes() == b"FIRST"
    finally:
        _CANONICAL_SINK["path"] = None


def test_no_sink_configured_is_a_noop(tmp_path: Path):
    _CANONICAL_SINK["path"] = None
    _refresh_canonical(tmp_path / "nope.png", passed=True)      # must not raise
