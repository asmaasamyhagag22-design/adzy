# -*- coding: utf-8 -*-
"""Poster quota resilience + honest failure surface (owner's failed run 2026-07-12:
google.genai ClientError after 239s while a Veo reel rendered concurrently on the same
Dynamic-Shared-Quota project) — hermetic, no network.

Pins: 429/RESOURCE_EXHAUSTED bursts are WAITED OUT (capped backoff) instead of dying
instantly; non-retryable 4xx still fails fast; the CLI prints ONE readable reason line
(the studio shows the log tail); switching products in the studio resets the stale
poster/reel stages."""
from __future__ import annotations

import time
from types import SimpleNamespace

import pytest

from poster.oneshot import _is_retryable, generate_oneshot_poster


class _Err(Exception):
    def __init__(self, status_code, msg):
        super().__init__(msg)
        self.status_code = status_code


def _img_resp() -> SimpleNamespace:
    part = SimpleNamespace(inline_data=SimpleNamespace(mime_type="image/png", data=b"PNG!"))
    return SimpleNamespace(candidates=[SimpleNamespace(content=SimpleNamespace(parts=[part]))])


class _FlakyClient:
    """Raises the given errors in order, then returns a good image response."""
    def __init__(self, errors):
        self._errors = list(errors)
        self.calls = 0
        self.models = self

    def generate_content(self, **kw):
        self.calls += 1
        if self._errors:
            raise self._errors.pop(0)
        return _img_resp()


def test_quota_burst_is_waited_out(tmp_path, monkeypatch):
    slept: list[float] = []
    monkeypatch.setattr(time, "sleep", lambda s: slept.append(s))
    monkeypatch.setattr("vertex_spend.DEFAULT_LEDGER", tmp_path / "v.jsonl")   # isolate the spend ledger
    cl = _FlakyClient([_Err(429, "RESOURCE_EXHAUSTED: quota"), _Err(429, "RESOURCE_EXHAUSTED")])
    out = generate_oneshot_poster("p", tmp_path / "o.png", client=cl)
    assert out.read_bytes() == b"PNG!"
    assert cl.calls == 3
    assert slept == [20, 45]          # capped backoff, not a hot loop


def test_non_retryable_400_fails_fast(tmp_path, monkeypatch):
    monkeypatch.setattr(time, "sleep", lambda s: pytest.fail("must not sleep on a 400"))
    cl = _FlakyClient([_Err(400, "INVALID_ARGUMENT: bad image")])
    with pytest.raises(_Err):
        generate_oneshot_poster("p", tmp_path / "o.png", client=cl)
    assert cl.calls == 1


def test_retryable_classifier():
    assert _is_retryable(_Err(429, "x")) and _is_retryable(_Err(503, "x"))
    assert _is_retryable(Exception("RESOURCE_EXHAUSTED by DSQ"))
    assert not _is_retryable(_Err(400, "INVALID_ARGUMENT"))
    assert not _is_retryable(Exception("some parsing error"))


def test_cli_failure_line_is_readable():
    from poster.__main__ import _fail_reason
    r = _fail_reason(_Err(429, "RESOURCE_EXHAUSTED: rate"))
    assert "429" in r and "Regenerate" in r          # names the cause + the action
    assert "Traceback" not in r
    assert "hiccuped" in _fail_reason(_Err(503, "UNAVAILABLE"))
    generic = _fail_reason(ValueError("boom"))
    assert "ValueError" in generic and "boom" in generic


def test_studio_pick_resets_stale_creatives():
    # Owner: scraping/choosing a NEW product must not keep showing the LAST poster/reel.
    from dashboard.server import _studio_js
    js = _studio_js("brand", False, False)
    pick_src = js.split("function pick(")[1].split("function loadProducts")[0]
    assert "setEmpty(" in pick_src                   # both stages reset on selection change
    assert "is-gen" in pick_src                      # ...but never clobber a live render
    assert "Generate '+k" in pick_src                # buttons drop back to 'Generate'
