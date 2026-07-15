# -*- coding: utf-8 -*-
"""Reel per-asset audit trail + the Veo-audio transcription gate (roadmap Phase-1 #1) —
hermetic. The generated path makes Veo SPEAK natively (keep_audio=True), so what is
actually SAID must pass the Ledger; an unsourced spoken claim MUTES the clip. The creative
path's guarantee is structural (Veo audio stripped) and stated on the trail."""
from __future__ import annotations

import json

import reel.audit as ra
from reel.audit import (
    audit_spoken_audio, build_reel_audit, gate_clip_audio, transcribe_clip, write_reel_audit,
)


def test_spoken_audio_verdicts():
    # a fabricated award against an EMPTY profile -> unsourced -> fails
    bad = audit_spoken_audio({}, "حاصلين على جايزة أفضل معهد في مصر 2026")
    assert bad["checked"] and not bad["passes"] and bad["unsourced"]
    # pure emotive speech passes; silence IS checked and passes (nothing said -> nothing unsourced)
    assert audit_spoken_audio({}, "جودة بتفرق معاك")["passes"]
    assert audit_spoken_audio({}, "")["passes"] is True
    # TRI-STATE (P6): no transcript = NOT_CHECKED. It must NOT read as a pass — the record used to
    # say `passes: True` about audio it had never heard.
    unchecked = audit_spoken_audio({}, None)
    assert unchecked["passes"] is None and not unchecked["checked"]


def test_gate_mutes_a_clip_with_rogue_speech(monkeypatch, tmp_path):
    clip = tmp_path / "c.mp4"
    clip.write_bytes(b"\x00")
    monkeypatch.setattr(ra, "transcribe_clip",
                        lambda p, caller=None: "حاصلين على جايزة أفضل معهد في مصر 2026")
    muted = {"n": 0}
    monkeypatch.setattr(ra, "mute_clip", lambda p: muted.__setitem__("n", muted["n"] + 1) or True)
    v = gate_clip_audio(clip, {}, caller=object())
    assert v["checked"] and not v["passes"] and v["muted"] and muted["n"] == 1
    # clean speech: no mute
    monkeypatch.setattr(ra, "transcribe_clip", lambda p, caller=None: "تدريب عملي بجد")
    v2 = gate_clip_audio(clip, {}, caller=object())
    assert v2["passes"] and not v2["muted"] and muted["n"] == 1


def test_transcriber_uses_the_caller_with_audio_mime(monkeypatch, tmp_path):
    monkeypatch.setattr(ra, "extract_audio", lambda p: b"AUDIOBYTES")
    seen = {}

    class _Caller:
        def __call__(self, system, user, model, group_name="", images=None):
            seen["group"] = group_name
            seen["mime"] = images[0][1]
            assert "VERBATIM" in system
            return model(text="أهلا بيكم في المعهد"), None

    t = transcribe_clip(tmp_path / "c.mp4", caller=_Caller())
    assert t == "أهلا بيكم في المعهد"
    assert seen["group"] == "reel_audio_transcript" and seen["mime"] == "audio/mp4"
    # degrade: no caller -> None (permissive); no audio -> honest ""
    assert transcribe_clip(tmp_path / "c.mp4", caller=None) is None
    monkeypatch.setattr(ra, "extract_audio", lambda p: None)
    assert transcribe_clip(tmp_path / "c.mp4", caller=_Caller()) == ""


def test_reel_audit_trail_exports_everything(tmp_path):
    from reel.creative_director import CreativeReel, CreativeScene
    creative = CreativeReel(
        concept="c", hook="ابني مستقبلك", cta="قدم دلوقتي", language="ar",
        images=["https://x/0.jpg"],
        scenes=[CreativeScene(image_index=0, veo_prompt="p",
                              voiceover="تدريب عملي", duration_s=4.0)])

    class _RR:
        scene_qa = [{"scene": 0, "overall_pass": True, "reason": "clean"}]
        fallback_used = False

    audit = build_reel_audit({}, creative, render_result=_RR())
    assert audit["asset_type"] == "reel"
    assert any("reel" in s for s in audit["coverage"]["covered_surfaces"])
    assert audit["scene_qa"][0]["reason"] == "clean"
    assert "structurally stripped" in audit["audio_surface"]
    assert "copy_audit" in audit and audit["copy_passes"] is not None

    p = write_reel_audit(audit, tmp_path / "r.mp4")
    assert p and p.name == "r.audit.json"
    assert json.loads(p.read_text(encoding="utf-8"))["asset_type"] == "reel"
