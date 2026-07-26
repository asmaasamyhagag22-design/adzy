# -*- coding: utf-8 -*-
"""Tier 1+2 of the reel directive: the container, the loudness, and honest verdicts.

DIRECTIVE §2 — «craft is worthless if the container is wrong». MEASURED on
abuauf_agency_v2.mp4: 772x1372 (not 1080x1920), pix_fmt yuvj420p (full-range JPEG variant),
color_range pc, colorspace bt470bg (obsolete PAL), primaries/transfer unknown, 1.65 Mbps,
44.1 kHz audio. A file tagged full-range with an undefined transfer is read as BT.709 limited
by the platform: blacks crush, highlights clip — on a reel whose mean luma is already 72.5/255.

DIRECTIVE §5 — integrated loudness -28.3 LUFS against a -14 target, true peak -14.7 dBTP
(14.7 dB of unused headroom, so it is a GAIN problem, not limiting), LRA 2.2, 1.57 s of tail
silence over the CTA.

DIRECTIVE §6 — the defect that made the audit unreliable rather than merely incomplete:
`faces_intact_across_motion: true` on five scenes where 0/65 frames contained a face. «`true`
meaning "I verified this" and `true` meaning "there was nothing to verify" are
indistinguishable. That makes the field worthless.»

Hermetic: pure functions + the bundled ffmpeg on tiny synthetic clips.
"""
from __future__ import annotations

import subprocess


def _ffmpeg() -> str:
    import imageio_ffmpeg
    return imageio_ffmpeg.get_ffmpeg_exe()


# --- §2 the platform envelope --------------------------------------------------------------

def test_the_encoder_flags_carry_the_full_colour_tag_set():
    from reel.encode_profile import video_encode_args
    args = " ".join(video_encode_args())
    for flag in ("-color_primaries bt709", "-color_trc bt709", "-colorspace bt709",
                 "-color_range tv", "-pix_fmt yuv420p", "-profile:v high"):
        assert flag in args, f"missing {flag}: {args}"
    assert "-b:v" in args


def test_the_audio_args_are_platform_grade():
    from reel.encode_profile import audio_encode_args
    args = " ".join(audio_encode_args())
    assert "-ar 48000" in args and "-ac 2" in args and "aac" in args
    assert "loudnorm=I=-14" in args and "TP=-1" in args


def test_the_canvas_is_exactly_the_platform_size():
    from reel.encode_profile import TARGET_H, TARGET_W, scale_filter
    assert (TARGET_W, TARGET_H) == (1080, 1920)
    f = scale_filter()
    assert "1080:1920" in f and "lanczos" in f and "format=yuv420p" in f


def test_a_wrong_container_is_rejected_with_numbers(tmp_path):
    from reel.encode_profile import check_envelope
    out = tmp_path / "bad.mp4"
    subprocess.run([_ffmpeg(), "-y", "-loglevel", "error", "-f", "lavfi",
                    "-i", "color=c=black:s=772x1372:d=1", "-r", "30",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", str(out)],
                   check=True, capture_output=True)
    v = check_envelope(out)
    assert v["value"] is False
    assert v["observed"]["width"] == 772 and v["observed"]["height"] == 1372
    assert any("1080" in str(i) for i in v["issues"]), v["issues"]


def test_an_unreadable_file_is_null_not_false(tmp_path):
    from reel.encode_profile import check_envelope
    bad = tmp_path / "x.mp4"
    bad.write_bytes(b"junk")
    v = check_envelope(bad)
    assert v["value"] is None and v["reason"]


# --- §6 verdict semantics ------------------------------------------------------------------

def test_a_gate_result_carries_its_measurement():
    from reel.verdicts import gate
    g = gate("cut_rhythm", value=False, method="code", measure="stdev_s",
             threshold=0.35, observed=0.077, units_checked={"cuts": 7})
    for k in ("gate", "value", "method", "measure", "threshold", "observed",
              "units_checked", "evidence_count"):
        assert k in g, k
    assert g["value"] is False and g["observed"] == 0.077


def test_nothing_to_verify_is_null_never_true():
    from reel.verdicts import gate
    g = gate("faces_intact", value=True, method="code", measure="cosine_min",
             threshold=0.85, observed=None, units_checked={"frames": 65, "frames_with_face": 0})
    assert g["value"] is None, (
        "a pass with nothing measured is still reported as a pass — the exact defect that made "
        "five scenes' face verdicts uninterpretable")
    assert "nothing" in (g.get("reason") or "").lower() or g.get("reason")


def test_a_pass_without_an_observation_is_downgraded():
    from reel.verdicts import gate
    g = gate("clean", value=True, method="code", measure="", threshold=None, observed=None,
             units_checked={})
    assert g["value"] is None


def test_a_vlm_only_verdict_cannot_pass_a_safety_gate():
    from reel.verdicts import gate
    g = gate("third_party_mark", value=True, method="vlm", measure="judgement",
             threshold=None, observed="clean", units_checked={"frames": 8}, safety=True)
    assert g["value"] is None and "vlm" in (g.get("reason") or "").lower()


def test_the_artifact_verdict_is_a_single_blocking_decision():
    from reel.verdicts import artifact_verdict, gate
    gates = [
        gate("copy_passes", value=True, method="code", measure="tokens", threshold=1.0,
             observed=1.0, units_checked={"fields": 8}),
        gate("scene_qa", value=False, method="vlm", measure="scenes_failing", threshold=0,
             observed=4, units_checked={"scenes": 8}),
    ]
    v = artifact_verdict(gates, fallback_used=True)
    assert v["value"] is False
    assert "scene_qa" in " ".join(v["blocking"])
    assert v["fallback_used"] is True


def test_all_null_gates_make_the_artifact_unverified_not_passed():
    from reel.verdicts import artifact_verdict, gate
    gates = [gate("x", value=None, method="code", measure="m", threshold=1, observed=None,
                  units_checked={})]
    v = artifact_verdict(gates, fallback_used=False)
    assert v["value"] is None
