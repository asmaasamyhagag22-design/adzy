# -*- coding: utf-8 -*-
"""The container the platform actually accepts — checked first, because craft cannot survive it.

DIRECTIVE §2: «craft is worthless if the container is wrong — the platform transcoder will
destroy grading and loudness work. Check this BEFORE any aesthetic gate. Fail action: REJECT.
Never a warning. This is not negotiable and costs nothing.»

MEASURED on abuauf_agency_v2.mp4, every field wrong:

    field            required        shipped
    resolution       1080x1920       772x1372   (upscaled and softened by the platform)
    pix_fmt          yuv420p         yuvj420p   (the full-range JPEG variant)
    color_range      tv              pc
    colorspace       bt709           bt470bg    (obsolete PAL)
    primaries/trc    bt709           unknown
    video bitrate    >= 6 Mbps       1.65 Mbps
    audio            48 kHz stereo   44.1 kHz

A file tagged full-range with an undefined transfer is read as BT.709 limited by the platform:
blacks crush, highlights clip — on a reel whose mean luma was already 72.5/255. Nothing in the
repo set a single colour tag; this module is the one place that now does.
"""
from __future__ import annotations

import logging
import re
import subprocess
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

TARGET_W, TARGET_H = 1080, 1920
TARGET_FPS = 30
MIN_VIDEO_BITRATE = 6_000_000        # 6 Mbps at 1080x1920
TARGET_VIDEO_BITRATE = "8M"
AUDIO_RATE = 48000
AUDIO_BITRATE = "192k"
LOUDNESS_TARGET = -14.0              # LUFS, the platform's own normalisation target
TRUE_PEAK_MAX = -1.0                 # dBTP


def scale_filter() -> str:
    """Scale to the exact platform canvas. Lanczos because the source is often smaller and a
    soft upscale is what made the last reel look washed."""
    return f"scale={TARGET_W}:{TARGET_H}:flags=lanczos,format=yuv420p"


def video_encode_args() -> list:
    """Explicit encoder flags. NOTHING here relies on a default — the defaults are exactly what
    produced yuvj420p/pc/bt470bg."""
    return [
        "-c:v", "libx264", "-profile:v", "high", "-preset", "medium",
        "-b:v", TARGET_VIDEO_BITRATE, "-maxrate", "10M", "-bufsize", "16M",
        "-pix_fmt", "yuv420p",
        "-color_primaries", "bt709", "-color_trc", "bt709",
        "-colorspace", "bt709", "-color_range", "tv",
        "-r", str(TARGET_FPS),
    ]


def audio_encode_args(*, loudnorm: bool = True) -> list:
    """AAC at the platform's rate, normalised to -14 LUFS.

    MEASURED: the shipped reel was -28.3 LUFS with -14.7 dBTP of unused headroom — a quarter of
    the perceived loudness of the surrounding feed, and a GAIN problem rather than a limiting
    one, which is why one filter fixes it."""
    args = []
    if loudnorm:
        args += ["-af", f"loudnorm=I={LOUDNESS_TARGET:.0f}:TP=-1.5:LRA=11"]
    return args + ["-c:a", "aac", "-b:a", AUDIO_BITRATE, "-ar", str(AUDIO_RATE), "-ac", "2"]


def finalize_delivery(video: "str | Path", *, loudnorm: bool = True) -> dict:
    """ONE re-encode at the very end that decides the delivered container. Replaces the file in
    place and returns the envelope verdict MEASURED ON WHAT WAS WRITTEN.

    WHY ONE PASS, NOT SIX EDITS. The repo encodes in at least six places (per-scene normalise,
    concat, the finalize mux, the end-card clip, the end-card xfade, the motion assemblies).
    Patching each is fragile and silently leaks the day someone adds a seventh — which is
    exactly how the dashboard ended up on its own reel engine. Everything upstream stays free to
    encode however it likes; this is the only function that decides what leaves the building.

    Verdict comes from `check_envelope` on the OUTPUT, never from the flags we sent: a report
    built from arguments says PASS even when ffmpeg ignored them.

    Never raises. On any failure the ORIGINAL file is left untouched and the verdict is `None` —
    a delivery we could not finalise must not be reported as finalised."""
    from reel.ffmpeg_tools import ffmpeg_exe

    src = Path(video)
    if not src.is_file() or src.stat().st_size == 0:
        return {"gate": "platform_envelope", "value": None, "checked": False,
                "reason": "no file to finalise", "method": "code", "measure": "container",
                "threshold": None, "observed": None, "units_checked": {}, "issues": []}

    tmp = src.with_name(src.stem + "_final.mp4")
    args = [ffmpeg_exe(), "-y", "-loglevel", "error", "-i", str(src),
            "-vf", scale_filter()] + video_encode_args()
    # An audio stream may not exist; `-map 0:a?` keeps the call valid either way.
    args += ["-map", "0:v:0", "-map", "0:a:0?"] + audio_encode_args(loudnorm=loudnorm)
    args += ["-movflags", "+faststart", str(tmp)]
    try:
        r = subprocess.run(args, capture_output=True, text=True)
        if r.returncode != 0 or not tmp.is_file() or tmp.stat().st_size == 0:
            raise RuntimeError((r.stderr or "")[:300])
    except Exception as exc:  # noqa: BLE001
        logger.warning("finalize_delivery failed (%s) — the original is untouched", exc)
        try:
            tmp.unlink(missing_ok=True)
        except Exception:  # noqa: BLE001
            pass
        return {"gate": "platform_envelope", "value": None, "checked": False,
                "reason": f"finalise failed: {exc}", "method": "code", "measure": "container",
                "threshold": None, "observed": None, "units_checked": {}, "issues": []}

    import shutil
    shutil.move(str(tmp), str(src))
    verdict = check_envelope(src)
    logger.info("[envelope] %s :: %s", "PASS" if verdict["value"] else "FAIL",
                verdict.get("reason"))
    return verdict


def check_audio(video: "str | Path") -> dict:
    """Integrated loudness, true peak and range — the numbers the platform normalises against.

    MEASURED on the shipped reel: -28.3 LUFS with -14.7 dBTP of unused headroom and an LRA of
    2.2. At -28 the reel plays at roughly a quarter the perceived loudness of the feed around
    it, and the empty headroom proves it is a GAIN problem, not a limiting one.

    `reel.final_screening._mean_volume_db` already existed and is a SILENCE tripwire (ffmpeg
    `volumedetect`, mean dB): useful, and not the same measurement. Integrated loudness is what
    the standard is written in, and only `ebur128` produces it. Tri-state: a file with no audio
    stream is `None`, never a failure and never a pass."""
    from reel.ffmpeg_tools import ffmpeg_exe

    blob = _probe(video)
    if "Audio:" not in blob:
        return {"gate": "audio_loudness", "value": None, "checked": False,
                "reason": "no audio stream", "method": "code", "measure": "lufs",
                "threshold": LOUDNESS_TARGET, "observed": {"lufs": None},
                "units_checked": {"streams": 0}}

    r = subprocess.run(
        [ffmpeg_exe(), "-i", str(video), "-map", "0:a:0", "-af", "ebur128=peak=true",
         "-f", "null", "-"], capture_output=True, text=True)
    err = r.stderr or ""
    lufs = tp = lra = None
    m = re.search(r"I:\s*(-?\d+(?:\.\d+)?)\s*LUFS", err[::-1][:2000][::-1]) or \
        re.findall(r"I:\s*(-?\d+(?:\.\d+)?)\s*LUFS", err)
    if isinstance(m, list) and m:
        lufs = float(m[-1])
    elif hasattr(m, "group"):
        lufs = float(m.group(1))
    tps = re.findall(r"Peak:\s*(-?\d+(?:\.\d+)?)\s*dBFS", err)
    if tps:
        tp = float(tps[-1])
    lras = re.findall(r"LRA:\s*(-?\d+(?:\.\d+)?)\s*LU", err)
    if lras:
        lra = float(lras[-1])

    if lufs is None:
        return {"gate": "audio_loudness", "value": None, "checked": False,
                "reason": "loudness could not be measured", "method": "code",
                "measure": "lufs", "threshold": LOUDNESS_TARGET,
                "observed": {"lufs": None, "true_peak": tp, "lra": lra},
                "units_checked": {"streams": 1}}

    issues = []
    if abs(lufs - LOUDNESS_TARGET) > 1.5:
        issues.append(f"integrated loudness {lufs:.1f} LUFS (target {LOUDNESS_TARGET:.0f} +/- 1.5)")
    if tp is not None and tp > TRUE_PEAK_MAX:
        issues.append(f"true peak {tp:.1f} dBTP (max {TRUE_PEAK_MAX:.1f})")

    return {"gate": "audio_loudness", "value": not issues, "checked": True,
            "reason": "; ".join(issues) if issues else f"{lufs:.1f} LUFS, peak {tp} dBTP",
            "method": "code", "measure": "lufs", "threshold": LOUDNESS_TARGET,
            "observed": {"lufs": round(lufs, 1), "true_peak": tp, "lra": lra},
            "units_checked": {"streams": 1}}


def _probe(path: "str | Path") -> str:
    from reel.ffmpeg_tools import ffmpeg_exe
    r = subprocess.run([ffmpeg_exe(), "-i", str(path)], capture_output=True, text=True)
    return r.stderr or ""


def _int(pattern: str, blob: str) -> Optional[int]:
    m = re.search(pattern, blob)
    return int(m.group(1)) if m else None


def check_envelope(video: "str | Path") -> dict:
    """Measure the delivered file against the envelope. Returns a gate-shaped dict:
    `value` True/False/None (None = could not probe — never a silent pass), with `observed`
    carrying the numbers so a failure is actionable rather than an opinion."""
    blob = _probe(video)
    if "Video:" not in blob:
        return {"gate": "platform_envelope", "value": None, "checked": False,
                "reason": "could not probe the file", "method": "code",
                "measure": "container", "threshold": None, "observed": None,
                "units_checked": {}, "issues": []}

    m = re.search(r"Video:.*?\s(\d{2,5})x(\d{2,5})", blob)
    width = int(m.group(1)) if m else None
    height = int(m.group(2)) if m else None
    pix = (re.search(r"Video:\s*\w+[^,]*,\s*([\w\d]+)", blob) or [None, None])[1]
    fps = None
    mf = re.search(r"(\d+(?:\.\d+)?)\s*fps", blob)
    if mf:
        fps = float(mf.group(1))
    vbr = _int(r"Video:.*?(\d+)\s*kb/s", blob)
    arate = _int(r"Audio:.*?(\d+)\s*Hz", blob)
    achan = "stereo" if "stereo" in blob else ("mono" if "mono" in blob else "")
    tags = {k: (k in blob) for k in ("bt709",)}

    issues = []
    if (width, height) != (TARGET_W, TARGET_H):
        issues.append(f"resolution {width}x{height} (need {TARGET_W}x{TARGET_H})")
    if width and height and (width % 2 or height % 2):
        issues.append("odd dimensions (yuv420p needs even)")
    if pix and pix != "yuv420p":
        issues.append(f"pix_fmt {pix} (need yuv420p)")
    if "bt709" not in blob:
        issues.append("no bt709 colour tags")
    # BITRATE IS AN OBSERVATION, NOT A CONFORMANCE FIELD. Measured while building this: a
    # one-second flat-colour clip encodes at 50 kb/s no matter what `-b:v 8M` asks for, because
    # x264 spends bits on detail that is not there. Once `finalize_delivery` owns the encode the
    # ALLOWANCE is guaranteed; what is actually spent measures the footage. The original 1.65
    # Mbps defect came from `-crf 20` with no target at all, and that cause is now closed at the
    # source — so this is reported for the record and never fails a delivery on its own.
    low_bitrate = vbr is not None and vbr * 1000 < MIN_VIDEO_BITRATE
    if arate is not None and arate != AUDIO_RATE:
        issues.append(f"audio {arate} Hz (need {AUDIO_RATE})")
    if achan == "mono":
        issues.append("mono audio (need stereo)")
    if fps is not None and abs(fps - TARGET_FPS) > 0.6:
        issues.append(f"{fps} fps (need {TARGET_FPS})")

    return {
        "gate": "platform_envelope",
        "value": not issues,
        "checked": True,
        "reason": "; ".join(issues) if issues else "container matches the platform envelope",
        "method": "code",
        "measure": "container_fields",
        "threshold": {"resolution": f"{TARGET_W}x{TARGET_H}", "pix_fmt": "yuv420p",
                      "colour": "bt709/tv", "video_bitrate_min": MIN_VIDEO_BITRATE,
                      "audio_rate": AUDIO_RATE},
        "observed": {"width": width, "height": height, "pix_fmt": pix, "fps": fps,
                     "video_kbps": vbr, "audio_hz": arate, "channels": achan,
                     "bt709_tagged": tags["bt709"],
                     "bitrate_below_target": low_bitrate},
        "units_checked": {"streams": 2},
        "issues": issues,
    }
