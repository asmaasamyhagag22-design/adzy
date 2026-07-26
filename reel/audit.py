"""Reel per-asset audit trail + the Veo-audio transcription gate (roadmap Phase-1 #1).

The audio surfaces, measured (2026-07-12):
  * CREATIVE/CLI path — Veo audio is STRUCTURALLY STRIPPED: every compositor mux maps the
    assembled video only (`-map 0:v` / `[v]`); the reel's spoken audio is exclusively the
    Ledger-gated TTS voice-over (+ instrumental music). Nothing to transcribe — the
    guarantee is structural and pinned by test.
  * GENERATED path (reel/generate.py, web API) — Veo SPEAKS natively from the prompt
    (`_voiceover_clause`). The intended text is drop-to-grounded gated, but what Veo
    actually SAYS is not guaranteed to match it. THIS is the gate below: extract the
    clip's audio -> Gemini transcription -> ledger.audit_text -> an unsourced hard claim
    MUTES the clip (fail-closed: visuals + gated overlay audio survive; rogue speech
    never ships).

`build_reel_audit` exports the poster-style per-asset trail: coverage scoping + the copy
audit (verbatim claims -> sources) + per-scene motion-QA verdicts + the audio-surface
statement. Everything degrades permissive on infrastructure gaps and never blocks a render.
"""
from __future__ import annotations

import json
import logging
import subprocess
from pathlib import Path
from typing import Any, Optional

from pydantic import BaseModel

logger = logging.getLogger(__name__)


class _Transcript(BaseModel):
    text: str = ""


def extract_audio(clip_path: "str | Path") -> Optional[bytes]:
    """The clip's audio track as m4a bytes; None when the clip has no audio / on failure."""
    try:
        import imageio_ffmpeg
        ff = imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:  # noqa: BLE001
        return None
    p = Path(clip_path)
    if not p.is_file() or p.stat().st_size == 0:
        return None
    import tempfile
    try:
        with tempfile.TemporaryDirectory(prefix="aud_") as td:
            dst = Path(td) / "a.m4a"
            subprocess.run([ff, "-y", "-i", str(p), "-vn", "-c:a", "aac", "-b:a", "96k",
                            str(dst)], capture_output=True, timeout=60)
            if dst.is_file() and dst.stat().st_size > 200:
                return dst.read_bytes()
    except Exception:  # noqa: BLE001
        pass
    return None


def transcribe_clip(clip_path: "str | Path", caller: Any = None) -> Optional[str]:
    """What is actually SPOKEN in the clip (Gemini is natively audio-capable; the caller
    protocol's media parts are mime-agnostic). None = could not check (permissive)."""
    if caller is None:
        return None
    audio = extract_audio(clip_path)
    if not audio:
        return ""                            # a silent clip is an honest empty transcript
    try:
        resp, _u = caller(
            "You are a VERBATIM transcriber. Return ONLY the words actually spoken in the "
            "audio, exactly as pronounced (keep the dialect; do not translate, correct or "
            "summarize). Empty text if there is no speech.",
            "Transcribe the attached clip audio.",
            _Transcript, group_name="reel_audio_transcript",
            images=[(audio, "audio/mp4")],
        )
        return str(getattr(resp, "text", "") or "").strip()
    except Exception as exc:  # noqa: BLE001
        logger.warning("transcription failed (%s) — audio gate skipped", type(exc).__name__)
        return None


def audit_spoken_audio(profile: dict, transcript: Optional[str]) -> dict:
    """Ledger verdict on what was SAID. TRI-STATE `passes` (P6): True = PASS, False = FAIL,
    None = NOT_CHECKED (no transcript / the ledger raised). It used to say `passes: True` about
    audio it had never heard — "cannot verify" spelled as "verified". `gate_clip_audio` already
    requires `checked` before acting, so muting behaviour is unchanged; the RECORD is now honest."""
    if transcript is None:
        return {"checked": False, "passes": None, "transcript": None, "unsourced": []}
    if not transcript:
        return {"checked": True, "passes": True, "transcript": "", "unsourced": []}
    try:
        from grounding import EvidenceLedger
        ledger = EvidenceLedger.from_profile(profile or {})
        uns = [{"kind": v.claim.kind, "text": v.claim.text}
               for v in ledger.audit_text(transcript) if not v.sourced]
        return {"checked": True, "passes": not uns, "transcript": transcript,
                "unsourced": uns}
    except Exception:  # noqa: BLE001 — the ledger is documented never to raise; belt+braces
        return {"checked": False, "passes": None, "transcript": transcript, "unsourced": []}


def mute_clip(clip_path: "str | Path") -> bool:
    """Strip the audio track in place (fail-closed remedy: the rogue speech never ships;
    the gated overlay VO/music added later still plays). False on failure."""
    try:
        import imageio_ffmpeg
        ff = imageio_ffmpeg.get_ffmpeg_exe()
        p = Path(clip_path)
        tmp = p.with_suffix(".muted.mp4")
        subprocess.run([ff, "-y", "-i", str(p), "-c:v", "copy", "-an", str(tmp)],
                       capture_output=True, timeout=120)
        if tmp.is_file() and tmp.stat().st_size > 0:
            tmp.replace(p)
            return True
    except Exception:  # noqa: BLE001
        pass
    return False


def gate_clip_audio(clip_path: "str | Path", profile: dict, caller: Any = None,
                    log=logger.info) -> dict:
    """The generated-path audio gate for ONE clip: transcribe -> audit -> MUTE on an
    unsourced hard claim. Returns the audit record (with 'muted' set when it acted)."""
    verdict = audit_spoken_audio(profile, transcribe_clip(clip_path, caller))
    verdict["muted"] = False
    if verdict["checked"] and not verdict["passes"]:
        kinds = ", ".join(u["kind"] for u in verdict["unsourced"])
        log(f"[audio-gate] unsourced spoken claim(s) ({kinds}) -> muting the clip")
        verdict["muted"] = mute_clip(clip_path)
    return verdict


def build_reel_audit(profile: dict, creative: Any = None, *,
                     render_result: Any = None,
                     audio_verdicts: Optional[list] = None,
                     extra_gates: Optional[list] = None,
                     audio_surface: str = "creative-path: Veo audio structurally stripped; "
                                          "spoken audio = Ledger-gated TTS voice-over only") -> dict:
    """The poster-style per-asset trail for a reel: coverage scoping, the copy audit
    (claim -> source), per-scene motion-QA verdicts, and the audio-surface statement."""
    from grounding.audit import coverage_block
    out: dict = {"asset_type": "reel", "coverage": coverage_block(),
                 "audio_surface": audio_surface,
                 "audio_verdicts": list(audio_verdicts or [])}
    try:
        if creative is not None:
            from reel.grounding import audit_reel_copy, creative_reel_copy_fields
            report = audit_reel_copy(profile or {}, creative_reel_copy_fields(creative))
            out["copy_audit"] = report.export()
            out["copy_passes"] = report.passes
    except Exception:  # noqa: BLE001
        out["copy_audit"] = {"error": "copy audit unavailable"}
    try:
        sq = list(getattr(render_result, "scene_qa", None) or [])
        out["scene_qa"] = sq
        out["fallback_used"] = bool(getattr(render_result, "fallback_used", False))
    except Exception:  # noqa: BLE001
        out["scene_qa"] = []

    # ONE BLOCKING DECISION (directive §6.4). Until now this dict reported `copy_passes: true`
    # beside four failing scenes, a morphing product and `fallback_used: true`, with nothing
    # anywhere that combined them — so a reel whose product turned into a different object could
    # leave the pipeline under a true-valued field. Every part is wrapped in `gate()`, which
    # downgrades a pass carrying no measurement to `null`.
    try:
        from reel.verdicts import artifact_verdict, gate
        gates = list(extra_gates or [])
        if "copy_passes" in out:
            _copy = out.get("copy_audit") or {}
            _n = len(_copy.get("fields") or _copy.get("claims") or [])
            gates.append(gate("copy_passes", value=bool(out["copy_passes"]), method="code",
                              measure="fields_grounded", threshold="all",
                              observed=_n or None, units_checked={"fields": _n},
                              evidence_count=_n))
        scenes = out.get("scene_qa") or []
        if scenes:
            failing = sum(1 for s in scenes
                          if isinstance(s, dict) and s.get("overall_pass") is False)
            gates.append(gate("scene_qa", value=(failing == 0), method="vlm",
                              measure="scenes_failing", threshold=0, observed=failing,
                              units_checked={"scenes": len(scenes)}))
        out["gates"] = gates
        out["artifact_verdict"] = artifact_verdict(
            gates, fallback_used=bool(out.get("fallback_used")))
    except Exception:  # noqa: BLE001 — an audit that cannot decide must say so, not vanish
        out["artifact_verdict"] = {"gate": "artifact_verdict", "value": None,
                                   "reason": "verdict could not be computed"}
    return out


def write_reel_audit(audit: dict, reel_path: "str | Path") -> Optional[Path]:
    """<reel>.audit.json next to the asset (the exportable proof). None on failure."""
    try:
        p = Path(reel_path).with_suffix(".audit.json")
        p.write_text(json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8")
        return p
    except Exception:  # noqa: BLE001
        return None
