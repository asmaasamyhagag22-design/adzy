"""Composite a REAL product screen/photo onto a generated clip (overhaul Phase D).

Video models can't render legible product UI, so a software product's scene keeps its GENERATED
screen abstract and this pass rides the brand's REAL screenshot/photo on top — that is how the
product is actually SHOWN (the [PRODUCT ON SCREEN] contract's real_screen route). One ffmpeg overlay
pass, structurally the same as reel/text_overlay.overlay_timeline_on_video. Best-effort: on any
failure it returns the original clip, so it can never break a render. Audio is copied through.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from reel.ffmpeg_tools import run_ffmpeg

logger = logging.getLogger(__name__)

REEL_W, REEL_H = 1080, 1920


def _default_box(width: int, height: int) -> tuple[int, int, int, int]:
    """A generic, brand-agnostic inset in the lower-middle two-thirds (leaves headroom for the
    caption/hook overlay above). (x, y, w, h)."""
    bw = int(width * 0.62)
    bh = int(height * 0.34)
    bx = (width - bw) // 2
    by = int(height * 0.40)
    return bx, by, bw, bh


def composite_product_screen(clip_path: "str | Path", asset_path: "str | Path",
                             out_path: "str | Path", *, placement: str = "inset",
                             box: Optional[tuple[int, int, int, int]] = None,
                             fps: int = 30, width: int = REEL_W, height: int = REEL_H) -> Path:
    """Overlay the real product image onto the clip and return `out_path`; returns the ORIGINAL
    clip on any failure (missing asset, ffmpeg error) so a render never breaks. `box` = (x,y,w,h)
    overrides the default inset."""
    clip_path, asset_path, out_path = Path(clip_path), Path(asset_path), Path(out_path)
    if not clip_path.is_file() or not asset_path.is_file():
        return clip_path
    bx, by, bw, bh = box or _default_box(width, height)
    fc = (
        f"[1:v]scale={bw}:{bh}:force_original_aspect_ratio=decrease,format=rgba,"
        f"fade=t=in:st=0:d=0.4:alpha=1[ov];"
        f"[0:v]fps={fps},format=yuv420p[bg];"
        f"[bg][ov]overlay={bx}:{by}:format=auto,format=yuv420p[v]"
    )
    try:
        run_ffmpeg([
            "-i", str(clip_path),
            "-loop", "1", "-i", str(asset_path),
            "-filter_complex", fc, "-map", "[v]", "-map", "0:a?",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-profile:v", "high",
            "-preset", "medium", "-crf", "20", "-c:a", "copy",
            "-shortest", "-movflags", "+faststart", str(out_path),
        ])
    except Exception as exc:  # noqa: BLE001 — a compositing failure must never lose the clip
        logger.info("[product_overlay] composite failed (%s); keeping the plain clip",
                    type(exc).__name__)
        return clip_path
    return out_path if out_path.is_file() else clip_path
