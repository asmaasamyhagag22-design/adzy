"""ElevenLabs TTS backend (reel/voiceover) — EXPLICIT-only, staged for the owner's Creator upgrade.
Hermetic: the HTTP call is mocked; no network, no spend. Asserts the auto-default is UNCHANGED."""
from __future__ import annotations

import reel.voiceover as vo


def test_resolve_backend_accepts_elevenlabs_only_when_explicit(monkeypatch):
    # VERBATIK_API_KEY is in the real .env (the owner added it for the reel voice, 2026-07-25)
    # and auto-selection now prefers it — so this test must clear it or it measures the
    # developer's environment instead of the resolution logic. Same class as the Kling
    # selection tests earlier today.
    monkeypatch.delenv("VERBATIK_API_KEY", raising=False)
    for k in ("REEL_TTS_BACKEND", "GOOGLE_CLOUD_PROJECT", "GEMINI_API_KEY", "OPENAI_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    assert vo._resolve_backend("elevenlabs") == "elevenlabs"                # explicit arg
    monkeypatch.setenv("REEL_TTS_BACKEND", "elevenlabs")
    assert vo._resolve_backend(None) == "elevenlabs"                        # explicit env
    # auto (no explicit selection, no creds) NEVER picks elevenlabs -> free edge
    monkeypatch.delenv("REEL_TTS_BACKEND", raising=False)
    assert vo._resolve_backend(None) == "edge"


def test_elevenlabs_segment_writes_mp3_from_the_api(monkeypatch, tmp_path):
    class _Resp:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self): return b"ID3\x00\x00fake-mp3-bytes"
    monkeypatch.setattr("urllib.request.urlopen", lambda req, timeout=120: _Resp())
    out = tmp_path / "seg.mp3"
    assert vo._elevenlabs_segment("مرحبا", out, voice="voice_123", model="", api_key="k") is True
    assert out.read_bytes().startswith(b"ID3")


def test_elevenlabs_segment_needs_a_voice_id(tmp_path):
    assert vo._elevenlabs_segment("x", tmp_path / "s.mp3", voice="", model="", api_key="k") is False


def test_synth_voiceover_elevenlabs_without_key_degrades_to_none(monkeypatch):
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
    assert vo.synth_voiceover(["مرحبا"], [3.0], "out.m4a", backend="elevenlabs") is None
