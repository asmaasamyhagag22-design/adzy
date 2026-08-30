# -*- coding: utf-8 -*-
"""The FINAL SCREENING — somebody watches the assembled FILM before it ships.

Owner, after v2 (2026-07-26): «انت محتاج وقفة مع الريل... المفروض انك ديزاينر ومخرج».
Every part of v2 passed its own gate, and the whole still shipped broken: letterboxed clips,
no endcard, a flat voice. The parts were checked; the FILM was not. This module is the missing
viewer: cheap CODE checks on the assembled mp4 (no model, no spend) that catch exactly the
defect classes the owner saw on screen.

Checks (each a measured v2 defect):
  * letterboxing  — uniform dark bands top/bottom of sampled frames (the landscape-seed look).
  * silence       — mean loudness via ffmpeg volumedetect (v2's predecessor shipped audio=False).
  * endcard state — the sidecar stamp written by reel.__main__ ("failed" = ship stopped).

TRI-STATE (the absence-read-as-success law): an unreadable/unprobeable reel returns
checked=False and passed=None — a reel we could not screen is never reported as screened."""
from __future__ import annotations

import json
import logging
import re
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

logger = logging.getLogger(__name__)


@dataclass
class ScreenVerdict:
    passed: Optional[bool] = None      # True/False only when checked; None = could not verify
    checked: bool = False
    issues: List[str] = field(default_factory=list)


def _ffmpeg() -> str:
    from reel.ffmpeg_tools import ffmpeg_exe
    return ffmpeg_exe()


def _sample_frames(video: Path, n: int = 6) -> List[Path]:
    """n PNG frames spread across the reel (into a temp dir the caller need not clean —
    tempfile prunes with the OS)."""
    tmp = Path(tempfile.mkdtemp(prefix="screen_"))
    out = tmp / "f_%02d.png"
    r = subprocess.run(
        [_ffmpeg(), "-y", "-loglevel", "error", "-i", str(video),
         "-vf", f"select='not(mod(n\\,15))',scale=180:-2", "-vsync", "vfr",
         "-frames:v", str(n), str(out)],
        capture_output=True, text=True)
    if r.returncode != 0:
        return []
    return sorted(tmp.glob("f_*.png"))


def _letterboxed(frame: Path, band_frac: float = 0.12) -> bool:
    """True when the frame's top AND bottom bands are near-uniform dark rows AND the middle is
    markedly brighter — a letterbox is a DISCONTINUITY, not just darkness.

    MEASURED false positive (2026-07-26, caught before the v3 render): a genuinely dark night
    frame tripped the dark-bands-only test 5/5, which would have exited 3 on a good reel after
    the money was spent. The brightness-gap requirement is what separates 'bands around an
    image' from 'a dark scene'."""
    try:
        from PIL import Image, ImageStat

        with Image.open(frame) as im:
            g = im.convert("L")
            w, h = g.size
            band = max(2, int(h * band_frac))
            top = ImageStat.Stat(g.crop((0, 0, w, band)))
            bot = ImageStat.Stat(g.crop((0, h - band, w, h)))
            mid = ImageStat.Stat(g.crop((0, band, w, max(band + 1, h - band))))

            def _dark_flat(st) -> bool:
                return st.mean[0] < 46 and st.stddev[0] < 14
            if not (_dark_flat(top) and _dark_flat(bot)):
                return False
            band_mean = (top.mean[0] + bot.mean[0]) / 2.0
            return (mid.mean[0] - band_mean) >= 25.0
    except Exception:  # noqa: BLE001
        return False


def _mean_volume_db(video: Path) -> Optional[float]:
    """mean_volume from ffmpeg volumedetect; None when unprobeable (no audio stream, bad file)."""
    r = subprocess.run(
        [_ffmpeg(), "-i", str(video), "-map", "0:a:0?", "-af", "volumedetect",
         "-f", "null", "-"],
        capture_output=True, text=True)
    m = re.search(r"mean_volume:\s*(-?\d+(?:\.\d+)?)\s*dB", r.stderr or "")
    return float(m.group(1)) if m else None


def screen_reel(video: "str | Path", *, sidecar: Optional[Path] = None,
                log=logger.info) -> ScreenVerdict:
    """Watch the assembled reel. Code-only, free, seconds."""
    video = Path(video)
    v = ScreenVerdict()
    if not video.is_file() or video.stat().st_size == 0:
        v.issues.append("the reel file is missing or empty")
        return v                                   # checked=False — could not verify

    frames = _sample_frames(video)
    if not frames:
        v.issues.append("could not decode the reel for screening")
        return v                                   # checked=False — could not verify

    banded = sum(1 for f in frames if _letterboxed(f))
    if banded * 2 >= len(frames):
        v.issues.append(
            f"letterboxed footage: {banded}/{len(frames)} sampled frames sit between dark "
            "bands — a landscape clip inside the 9:16 canvas")

    vol = _mean_volume_db(video)
    if vol is None:
        v.issues.append("no audio stream — the reel is silent")
    elif vol < -50.0:
        v.issues.append(f"the reel is effectively silent (mean volume {vol:.1f} dB)")

    side = sidecar or video.with_suffix(".path.json")
    if side.is_file():
        try:
            state = (json.loads(side.read_text(encoding="utf-8")) or {}).get("endcard")
            if state == "failed":
                v.issues.append("the brand endcard failed to append")
        except Exception:  # noqa: BLE001
            pass

    v.checked = True
    v.passed = not v.issues
    for issue in v.issues:
        log(f"[screening] {issue}")
    return v
