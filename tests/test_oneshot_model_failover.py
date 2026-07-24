# -*- coding: utf-8 -*-
"""A retired image model must not kill the poster.

MEASURED (2026-07-21, project radiant-octane-501919-v1): every Imagen model AND
`gemini-3-pro-image-preview` — the model this machine's `.env` pins via ONESHOT_IMAGE_MODEL —
return 404 NOT_FOUND. `gemini-2.5-flash-image` (nano banana) serves normally. So the owner's
requested poster died with "404 NOT_FOUND" on a live client deliverable, purely because a single
configured model id had been retired underneath us.

A 404 means "this model no longer exists here": retrying it is pointless and the right move is the
NEXT model, not failure. Distinct from a 429/503, which is a temporary burst and IS worth retrying
on the same model (that behaviour is unchanged). Hermetic — the client is injected."""
from __future__ import annotations

from pathlib import Path

import pytest

from poster.oneshot import _MODEL_CHAIN, generate_oneshot_poster


class _Err(Exception):
    def __init__(self, code, msg="boom"):
        super().__init__(f"ClientError {code} {msg}")
        self.code = code


class _FakeModels:
    def __init__(self, behaviour):
        self.behaviour = behaviour
        self.tried: list[str] = []

    def generate_content(self, *, model, contents, config):
        self.tried.append(model)
        out = self.behaviour.get(model, "404")
        if out == "404":
            raise _Err(404, "Publisher model not found")
        if out == "429":
            raise _Err(429, "RESOURCE_EXHAUSTED")

        class _Inline:
            mime_type = "image/png"
            data = b"\x89PNG\r\n\x1a\n"

        class _Part:
            inline_data = _Inline()

        class _Content:
            parts = [_Part()]

        class _Cand:
            content = _Content()

        class _Resp:
            candidates = [_Cand()]
        return _Resp()


class _FakeClient:
    def __init__(self, behaviour):
        self.models = _FakeModels(behaviour)


def test_a_retired_model_falls_through_to_a_live_one(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("vertex_spend.guard_image", lambda *a, **k: 0.0)
    monkeypatch.setattr("vertex_spend.record", lambda *a, **k: None)
    cl = _FakeClient({"dead-model": "404", "gemini-2.5-flash-image": "ok"})
    out = generate_oneshot_poster("p", tmp_path / "o.png", model="dead-model", client=cl)
    assert Path(out).is_file()
    assert cl.models.tried[0] == "dead-model"            # the configured model is still tried FIRST
    assert "gemini-2.5-flash-image" in cl.models.tried   # then a known-live one


def test_a_404_is_not_retried_on_the_same_model(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("vertex_spend.guard_image", lambda *a, **k: 0.0)
    monkeypatch.setattr("vertex_spend.record", lambda *a, **k: None)
    cl = _FakeClient({"dead-model": "404", "gemini-2.5-flash-image": "ok"})
    generate_oneshot_poster("p", tmp_path / "o.png", model="dead-model", client=cl)
    assert cl.models.tried.count("dead-model") == 1      # retrying a retired model wastes minutes


def test_every_model_dead_raises_naming_what_was_tried(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("vertex_spend.guard_image", lambda *a, **k: 0.0)
    monkeypatch.setattr("vertex_spend.record", lambda *a, **k: None)
    cl = _FakeClient({})                                  # everything 404s
    with pytest.raises(Exception) as e:
        generate_oneshot_poster("p", tmp_path / "o.png", model="dead-model", client=cl)
    assert "dead-model" in str(e.value)                   # the message lists what was attempted
    assert len(cl.models.tried) >= 2


def test_transient_errors_still_retry_the_same_model(tmp_path: Path, monkeypatch):
    # A 429 is a burst, not a retirement — the existing back-off must survive this change.
    monkeypatch.setattr("vertex_spend.guard_image", lambda *a, **k: 0.0)
    monkeypatch.setattr("vertex_spend.record", lambda *a, **k: None)
    monkeypatch.setattr("time.sleep", lambda *_: None)
    cl = _FakeClient({"m": "429"})
    with pytest.raises(Exception):
        generate_oneshot_poster("p", tmp_path / "o.png", model="m", client=cl)
    assert cl.models.tried.count("m") >= 3


def test_the_chain_contains_a_model_verified_live():
    assert "gemini-2.5-flash-image" in _MODEL_CHAIN
