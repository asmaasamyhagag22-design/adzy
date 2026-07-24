# -*- coding: utf-8 -*-
"""P-87/88/05/89 (2026-07-18): the reel forced EGYPTIAN Arabic on EVERY Arabic brand across the
voice path — the TTS system-prompt dialect line, the FREE edge voice (ar-EG-Salma), the ElevenLabs
voice id, the director's [ONE VOICE] register, and the on-screen/spoken language labels. A
Saudi/Gulf brand's reel was narrated in Masri. Register + native voice must follow the brand's
EVIDENCED country (poster.locale), universally — exactly like the poster/calendar already do.
Hermetic — pure string/selection logic; no TTS, no network, no render."""
from __future__ import annotations

import reel.voiceover as vo
from poster.locale import arabic_dialect_for, arabic_region_of

_SA = {"source_url": "https://brand.sa/"}
_EG = {"source_url": "https://brand.com.eg/"}
_SY = {"source_url": "https://brand.sy/"}


def test_region_bucket_from_evidence():
    assert arabic_region_of(_SA) == "gulf"
    assert arabic_region_of(_EG) == "egypt"
    assert arabic_region_of(_SY) == "levant"
    assert arabic_region_of({}) == ""            # unknown -> neutral, NEVER defaults to Egypt


def test_tts_instructions_follow_the_brand_region_not_hardcoded_egypt():
    sa = vo._instructions_for(["مرحبا بالعالم"], "", "", dialect=arabic_dialect_for(_SA))
    assert ("Khaleeji" in sa) or ("خليجية" in sa)
    assert "EGYPTIAN" not in sa.upper()          # a Saudi reel is NOT narrated in Masri
    eg = vo._instructions_for(["مرحبا بالعالم"], "", "", dialect=arabic_dialect_for(_EG))
    assert "Egyptian" in eg                       # the pilot market still reads Egyptian


def test_elevenlabs_voice_id_is_per_region(monkeypatch):
    monkeypatch.delenv("REEL_ELEVENLABS_VOICE", raising=False)
    assert vo._elevenlabs_voice_for(_SA) == "t9akNmCDhz230CEXOYmn"   # owner's Saudi/Gulf voice
    assert vo._elevenlabs_voice_for(_EG) == "QvNF0qyyt1Tuy1YAmnzH"   # owner's Egyptian voice
    assert vo._elevenlabs_voice_for(_SY) == "Wim44P0dU9HtjyzNnFsv"   # owner's Syrian/Levant voice
    assert vo._elevenlabs_voice_for({}) == ""                        # unknown -> neutral (no id)
    monkeypatch.setenv("REEL_ELEVENLABS_VOICE", "explicit_id")
    assert vo._elevenlabs_voice_for(_SA) == "explicit_id"            # env override always wins


def test_edge_native_voice_is_per_region():
    assert vo._edge_voice_for("مرحبا", None, _SA).startswith("ar-SA")   # not ar-EG
    assert vo._edge_voice_for("مرحبا", None, _EG) == "ar-EG-SalmaNeural"
    assert vo._edge_voice_for("hello", None, _SA) == vo._EDGE_VOICE_EN  # non-Arabic -> English


def test_locked_register_follows_profile_region():
    from reel.creative_director import _locked_register
    sa = _locked_register("ar", _SA)
    assert ("Khaleeji" in sa) or ("خليجية" in sa)
    assert "Egyptian" in _locked_register("ar")        # no profile -> pilot default unchanged
    assert _locked_register("en").startswith("conversational")


def test_reel_language_labels_follow_profile_region():
    from reel.art_director import _copy_language_label, _spoken_language_label
    cap = _copy_language_label("ar", _SA)
    assert ("خليجية" in cap) or ("Khaleeji" in cap)
    spk = _spoken_language_label("ar", _SA)
    assert ("Khaleeji" in spk) or ("خليجية" in spk)
    assert "مصرية" in _copy_language_label("ar")        # no profile -> unchanged pilot default
    assert "Masri" in _spoken_language_label("ar")
