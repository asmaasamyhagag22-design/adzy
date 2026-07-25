# -*- coding: utf-8 -*-
"""Kling as an image-to-video provider — the owner's new video stack.

OWNER RULING 2026-07-25: «kling هعمل بيه فيديوهات بدل veo هطلع صور من نانو بانانا لايت واحركها».
Stills from Nano Banana Lite, animated by Kling, instead of Veo text-to-video. The reason is cost:
Veo bills $0.40/SECOND, and an uncapped Veo renderer is what produced this project's
$228-in-four-days incident.

WHAT THIS BUILDS. `reel/video_provider.py` already defines ONE protocol every provider implements
(`generate(prompt, out_path, duration_s, width, height, palette, reference_image)`) and a
`select_provider()` that picks between Veo, the AIML gateway, and the stub. Kling joins that
protocol — it does not get a parallel path. Everything downstream (the compositor, its fallback to
Ken Burns, the QA gates) then works unchanged.

KLING IS IMAGE-TO-VIDEO FIRST. That is the whole point of the owner's plan, and it makes
`reference_image` load-bearing rather than optional here: with a still to animate we send it; with
no still we can still submit text-to-video, but the still is the intended path.

AUTH. Kling's open platform signs each request with a JWT built from an access/secret PAIR — not a
bearer key. `KLING_ACCESS_KEY` + `KLING_SECRET_KEY`, following this project's `<SERVICE>_*`
convention.

SPEND. Every paid provider in this codebase writes to a ledger. The caps were lifted on the owner's
instruction but the METERS were not, and a new paid path that does not record itself would be the
one place spend becomes invisible — exactly what the ledgers exist to prevent.

Hermetic: the HTTP transport is injected. No network, no key, no spend.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest


class _FakeTransport:
    """Stands in for Kling's REST API: submit a task, poll it, then serve the mp4."""

    def __init__(self, *, fail_submit=False, never_ready=False):
        self.calls = []
        self.fail_submit = fail_submit
        self.never_ready = never_ready
        self.polls = 0

    def post(self, url, *, headers=None, json_body=None):
        self.calls.append(("POST", url, headers or {}, json_body or {}))
        if self.fail_submit:
            raise RuntimeError("HTTP 401")
        return {"data": {"task_id": "task_abc"}}

    def get(self, url, *, headers=None):
        self.calls.append(("GET", url, headers or {}, None))
        self.polls += 1
        if self.never_ready:
            return {"data": {"task_status": "processing"}}
        return {"data": {"task_status": "succeed",
                         "task_result": {"videos": [{"url": "https://cdn.kling.test/v/1.mp4"}]}}}

    def download(self, url, dest: Path):
        self.calls.append(("GET-BIN", url, {}, None))
        Path(dest).write_bytes(b"\x00\x00\x00\x18ftypmp42fake-mp4-bytes")
        return dest


@pytest.fixture
def _provider(monkeypatch, tmp_path):
    monkeypatch.setenv("KLING_ACCESS_KEY", "ak_test")
    monkeypatch.setenv("KLING_SECRET_KEY", "sk_test")
    monkeypatch.setenv("KLING_SPEND_LEDGER", str(tmp_path / "kling.jsonl"))
    from reel.video_provider import KlingProvider
    return KlingProvider


def _still(tmp_path) -> str:
    p = tmp_path / "still.png"
    p.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 64)
    return str(p)


# --- it satisfies the one protocol every provider satisfies ---------------------------------------

def test_kling_is_a_video_provider(_provider):
    assert getattr(_provider, "name", "") == "kling"
    assert hasattr(_provider, "generate")


def test_it_animates_a_still_and_returns_the_saved_mp4(_provider, tmp_path):
    tr = _FakeTransport()
    out = tmp_path / "clip.mp4"
    got = _provider(transport=tr).generate(
        "a slow push-in on the product", out_path=out, duration_s=5, width=1080, height=1920,
        reference_image=_still(tmp_path))
    assert Path(got).exists() and Path(got).stat().st_size > 0
    submits = [c for c in tr.calls if c[0] == "POST"]
    assert submits, "no task was submitted"


def test_the_still_is_what_gets_animated(_provider, tmp_path):
    """Image-to-video is the owner's whole plan — a Kling call with no image attached would be
    text-to-video wearing the same name."""
    tr = _FakeTransport()
    _provider(transport=tr).generate("push in", out_path=tmp_path / "c.mp4", duration_s=5,
                                     width=1080, height=1920, reference_image=_still(tmp_path))
    body = json.dumps([c[3] for c in tr.calls if c[0] == "POST"])
    assert "image" in body, "the still was never sent; this is not image-to-video"


def test_it_polls_until_the_task_is_ready(_provider, tmp_path):
    tr = _FakeTransport()
    _provider(transport=tr).generate("x", out_path=tmp_path / "c.mp4", duration_s=5,
                                     width=1080, height=1920, reference_image=_still(tmp_path))
    assert tr.polls >= 1, "the task was never polled"


def test_a_task_that_never_finishes_gives_up_instead_of_hanging(_provider, tmp_path):
    tr = _FakeTransport(never_ready=True)
    with pytest.raises(Exception):
        _provider(transport=tr, poll_seconds=0, max_wait_seconds=0).generate(
            "x", out_path=tmp_path / "c.mp4", duration_s=5, width=1080, height=1920,
            reference_image=_still(tmp_path))


def test_a_submit_failure_raises_so_the_compositor_can_fall_back(_provider, tmp_path):
    """The compositor already falls back to Ken Burns on a provider error. Swallowing the failure
    and returning a path to nothing is what would break it."""
    tr = _FakeTransport(fail_submit=True)
    with pytest.raises(Exception):
        _provider(transport=tr).generate("x", out_path=tmp_path / "c.mp4", duration_s=5,
                                         width=1080, height=1920,
                                         reference_image=_still(tmp_path))


# --- credentials -----------------------------------------------------------------------------

def test_missing_credentials_fail_loudly_not_silently(monkeypatch, tmp_path):
    # All three, for the same reason as the selection test below: the real .env carries
    # KLING_API_KEY, so clearing only the pair tests the developer's environment.
    for _k in ("KLING_API_KEY", "KLING_ACCESS_KEY", "KLING_SECRET_KEY"):
        monkeypatch.delenv(_k, raising=False)
    from reel.video_provider import KlingProvider
    with pytest.raises(Exception) as exc:
        KlingProvider(transport=_FakeTransport()).generate(
            "x", out_path=tmp_path / "c.mp4", duration_s=5, width=1080, height=1920)
    assert "KLING" in str(exc.value).upper()


def test_the_request_is_signed_not_bearer_keyed(_provider, tmp_path):
    """Kling authenticates with a JWT built from the access/secret pair. A raw secret in the header
    would both fail and leak the long-lived credential."""
    tr = _FakeTransport()
    _provider(transport=tr).generate("x", out_path=tmp_path / "c.mp4", duration_s=5,
                                     width=1080, height=1920, reference_image=_still(tmp_path))
    auth = " ".join(str(c[2].get("Authorization", "")) for c in tr.calls)
    assert auth.strip(), "no Authorization header at all"
    assert "sk_test" not in auth, "the secret key was sent verbatim"


# --- spend is metered, like every other paid path ----------------------------------------------

def test_every_clip_is_recorded_in_the_ledger(_provider, tmp_path):
    tr = _FakeTransport()
    _provider(transport=tr).generate("x", out_path=tmp_path / "c.mp4", duration_s=5,
                                     width=1080, height=1920, reference_image=_still(tmp_path))
    led = tmp_path / "kling.jsonl"
    assert led.exists(), "a paid render left no trace in any ledger"
    row = json.loads(led.read_text(encoding="utf-8").strip().splitlines()[-1])
    assert row.get("duration_s") == 5
    assert "usd" in row


# --- selection --------------------------------------------------------------------------------

def test_the_backend_can_be_selected_explicitly(monkeypatch):
    monkeypatch.setenv("REEL_VIDEO_BACKEND", "kling")
    monkeypatch.setenv("KLING_ACCESS_KEY", "ak")
    monkeypatch.setenv("KLING_SECRET_KEY", "sk")
    from reel.video_provider import default_video_provider
    assert default_video_provider().name == "kling"


def test_kling_wins_auto_selection_when_its_keys_are_present(monkeypatch):
    """The owner's ruling is that Kling REPLACES Veo. With Kling credentials present it is the
    default, so nothing has to remember to set a backend variable before every run."""
    monkeypatch.delenv("REEL_VIDEO_BACKEND", raising=False)
    monkeypatch.delenv("REEL_FORCE_STUB", raising=False)
    monkeypatch.setenv("KLING_ACCESS_KEY", "ak")
    monkeypatch.setenv("KLING_SECRET_KEY", "sk")
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "some-project")   # Veo would otherwise win
    from reel.video_provider import default_video_provider
    assert default_video_provider().name == "kling"


def test_veo_still_wins_when_kling_is_not_configured(monkeypatch):
    monkeypatch.delenv("REEL_VIDEO_BACKEND", raising=False)
    monkeypatch.delenv("REEL_FORCE_STUB", raising=False)
    # ALL THREE. This test deleted the pair and not `KLING_API_KEY`, which the single-token mode
    # added afterwards and which the owner's real .env now sets — so it passed alone and failed in
    # the suite, measuring the environment instead of the selection logic.
    for _k in ("KLING_API_KEY", "KLING_ACCESS_KEY", "KLING_SECRET_KEY"):
        monkeypatch.delenv(_k, raising=False)
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "some-project")
    from reel.video_provider import default_video_provider
    assert default_video_provider().name == "veo"


# --- a single token, because that is what the console issued -------------------------------------

def test_a_single_secret_is_accepted(monkeypatch, tmp_path):
    """OWNER: «هو سكرت بس». Some Kling consoles issue one token instead of an access/secret pair.
    Inventing an access key to sign with would produce a JWT that fails every call for no visible
    reason, so a single value is sent as a plain bearer instead."""
    monkeypatch.delenv("KLING_ACCESS_KEY", raising=False)
    monkeypatch.delenv("KLING_SECRET_KEY", raising=False)
    monkeypatch.setenv("KLING_API_KEY", "single_token_value")
    monkeypatch.setenv("KLING_SPEND_LEDGER", str(tmp_path / "k.jsonl"))
    from reel.video_provider import KlingProvider
    tr = _FakeTransport()
    KlingProvider(transport=tr).generate("x", out_path=tmp_path / "c.mp4", duration_s=5,
                                         width=1080, height=1920,
                                         reference_image=_still(tmp_path))
    auth = " ".join(str(c[2].get("Authorization", "")) for c in tr.calls)
    assert "single_token_value" in auth


def test_the_pair_still_signs_when_both_are_present(monkeypatch, tmp_path):
    monkeypatch.setenv("KLING_ACCESS_KEY", "ak")
    monkeypatch.setenv("KLING_SECRET_KEY", "sk_secret")
    monkeypatch.setenv("KLING_SPEND_LEDGER", str(tmp_path / "k.jsonl"))
    from reel.video_provider import KlingProvider
    tr = _FakeTransport()
    KlingProvider(transport=tr).generate("x", out_path=tmp_path / "c.mp4", duration_s=5,
                                         width=1080, height=1920,
                                         reference_image=_still(tmp_path))
    auth = " ".join(str(c[2].get("Authorization", "")) for c in tr.calls)
    assert "sk_secret" not in auth, "the secret leaked instead of signing"
    assert auth.count(".") >= 2, "not a JWT"


def test_a_single_token_is_enough_for_auto_selection(monkeypatch):
    monkeypatch.delenv("REEL_VIDEO_BACKEND", raising=False)
    monkeypatch.delenv("REEL_FORCE_STUB", raising=False)
    monkeypatch.delenv("KLING_ACCESS_KEY", raising=False)
    monkeypatch.delenv("KLING_SECRET_KEY", raising=False)
    monkeypatch.setenv("KLING_API_KEY", "tok")
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "p")
    from reel.video_provider import default_video_provider
    assert default_video_provider().name == "kling"
