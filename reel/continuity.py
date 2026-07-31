# -*- coding: utf-8 -*-
"""Continuity between scenes — by pixel, not by adjective.

OWNER'S QUESTION (2026-07-28): «هو بما إنه علشان يكون سيناريو مترابط، الأحسن i2v ولا t2v؟»

i2v, and not as a preference — every coherence control this pipeline owns lives on the STILL:
`brand.look.look_lock` fixes one camera and grade for the brand, `identity_ref` attaches the same
chosen face to every seed, `place_ref_url` holds the real location, the product is composited and
the frame passes QA before anything moves. Text-to-video throws all of that away and asks the
model to imagine it from prose — and "prose loses to pixels" is the lesson this repo has paid for
four separate times.

BUT THE QUESTION POINTS AT A REAL GAP, which is not the one it names. Coherence WITHIN a clip is
not continuity BETWEEN clips: scene 1 and scene 3 are separately generated stills, so the room,
the light and the wardrobe can all move between them. MEASURED: `grep -rn last_frame reel/`
returned zero — nothing in this pipeline had ever carried one frame into the next.

So the last frame of scene N can seed scene N+1, and the two clips become the same continuous
moment instead of two takes that resemble each other.

TWO THINGS THIS MODULE REFUSES TO DO, both because they trade a real guarantee for a cosmetic one:

  * IT NEVER OVERRIDES A REAL PHOTOGRAPH. The reel's grounding promise is that each scene is
    seeded from the brand's own scraped photo of the actual place. A previous clip's frame is a
    generated image; letting it displace a real one buys smoothness with the thing that makes the
    reel true.
  * IT NEVER CHAINS WITHOUT A BOUND. Each chained scene is one more generation away from any
    grounding, so drift COMPOUNDS — by the fifth link the reel is four generations from the real
    place and nothing has said so. `MAX_CHAIN` forces a re-ground, which is also how continuity is
    shot in practice: you hold a run, then you cut back to the establishing frame.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

# How many consecutive scenes may inherit the previous frame before one must re-ground on its own
# seed. Two holds a beat together; beyond that the compounding drift outruns the continuity.
MAX_CHAIN = 2

# A scene of these kinds is a bookend, not a continuation — chaining one produces an intro that
# opens mid-gesture on the end of a shot nobody has seen yet.
_BOOKENDS = ("intro", "outro")


class ContinuityDecision:
    """Why this scene got the seed it got — so a chained reel is never a silent one."""

    __slots__ = ("seed", "chained", "reason")

    def __init__(self, seed: Optional[str], chained: bool, reason: str):
        self.seed, self.chained, self.reason = seed, chained, reason

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Continuity chained={self.chained} {self.reason}>"


def _attr(scene: Any, name: str, default=None):
    return getattr(scene, name, default)


def _is_real_photo(url: Optional[str]) -> bool:
    """A scraped photograph, as opposed to a still this run generated.

    Generated seeds are written under the run's own output tree; a real one is a URL or a path
    from the scrape. Getting this wrong in the permissive direction would let a chain displace a
    real photograph, so anything unrecognised counts as REAL and is protected."""
    u = str(url or "")
    if not u:
        return False
    low = u.replace("\\", "/").lower()
    generated_markers = ("/outputs/", "/_seeds/", "seed_", "/reel_", "/tmp/")
    return not any(m in low for m in generated_markers)


def decide_seed(scene: Any, previous_tail: Optional[str], *, own_seed: Optional[str],
                chain_len: int, previous_scene: Any = None) -> ContinuityDecision:
    """The seed frame for one scene, and the reason for it.

    Precedence, strongest grounding first:
      1. the scene's own REAL photograph        — never displaced
      2. the previous scene's last frame        — when this scene continues that moment
      3. the scene's own generated still        — its art direction, independently grounded
      4. the previous scene's last frame        — when the scene has nothing at all
    """
    kind = str(_attr(scene, "kind", "") or "")

    if own_seed and _is_real_photo(own_seed):
        return ContinuityDecision(own_seed, False, "the scene's own real photograph")

    if kind in _BOOKENDS:
        return ContinuityDecision(own_seed, False, f"{kind} is a bookend, never a continuation")

    # A product beat is a deliberate cut: the frame changes to the thing being sold, and
    # continuing a person's gesture into a packshot reads as an error, not as flow.
    if _attr(scene, "product_asset_url") or _attr(scene, "hero_product"):
        return ContinuityDecision(own_seed, False, "a product beat is a cut, not a continuation")

    if previous_tail and chain_len < MAX_CHAIN and _same_moment(scene, previous_scene):
        return ContinuityDecision(previous_tail, True,
                                  f"continues the previous shot (link {chain_len + 1}"
                                  f"/{MAX_CHAIN})")

    if not own_seed and previous_tail:
        # Without this the scene has no seed at all, and Veo — image-to-video only since
        # 2026-07-28 — refuses, dropping a scene that could have continued to Ken Burns.
        return ContinuityDecision(previous_tail, True, "no seed of its own; inherits the frame")

    if previous_tail and chain_len >= MAX_CHAIN:
        return ContinuityDecision(own_seed, False,
                                  f"chain bound reached ({MAX_CHAIN}) — re-grounding on its "
                                  "own seed")

    return ContinuityDecision(own_seed, False, "its own seed")


def _same_moment(scene: Any, previous: Any) -> bool:
    """Do these two scenes depict the same continuous moment?

    DERIVED, NOT DECLARED. The obvious design is a `continues_previous` field for the creative
    director to set — and this repo has already written down what that costs: "a cure that depends
    on a model remembering to opt in is not a cure" (`test_the_reel_product_is_pasted_not_drawn`,
    after a director forgot `product_overlay` and a real packshot went through the video model).
    Two scenes are the same moment when they were grounded on the same real place and neither is
    a bookend or a product beat — all of which are already on the scene."""
    if previous is None:
        return False
    a = str(_attr(scene, "place_ref_url", "") or "")
    b = str(_attr(previous, "place_ref_url", "") or "")
    if not a or not b:
        return False
    return a == b


def tail_frame(clip_path: "str | Path", out_path: "str | Path") -> Optional[str]:
    """The clip's LAST frame as a PNG, or None. Never raises — a missing tail costs continuity,
    and continuity must never cost a render."""
    from .ffmpeg_tools import ffmpeg_exe, run_ffmpeg
    src, dest = Path(str(clip_path)), Path(str(out_path))
    if not src.is_file():
        return None
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        # `-sseof -0.2` seeks from the END, so this does not depend on knowing the duration and
        # cannot land past it. `-update 1` keeps overwriting one file, leaving the final frame.
        res = run_ffmpeg([
            ffmpeg_exe(), "-y", "-sseof", "-0.2", "-i", str(src),
            "-vsync", "0", "-update", "1", "-frames:v", "1", str(dest),
        ])
        if getattr(res, "returncode", 1) != 0 or not dest.is_file() or dest.stat().st_size < 64:
            return None
        return str(dest)
    except Exception:  # noqa: BLE001
        return None
