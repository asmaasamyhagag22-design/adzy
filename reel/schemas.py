"""Reel Studio schemas — the storyboard + render contracts.

Content (headline / offerings / CTA / contact / palette / logo) is carried by the
poster's `PosterBrief` (reused verbatim — see reel/from_profile.py). These models
add only the genuinely reel-specific structure: timed scenes and render results.
"""
from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

# Vertical short-video canvas (Instagram Reels / TikTok / YouTube Shorts).
REEL_W, REEL_H = 1080, 1920

SceneKind = Literal["intro", "offering", "value_prop", "contact", "outro", "gallery"]


class ReelScene(BaseModel):
    """One timed visual scene.

    The VideoProvider generates a TEXT-FREE clip from `visual_prompt`; the
    compositor overlays the verbatim text fields below (any may be empty -> not
    shown). `source_field` records which profile field the text came from, so a
    scene is always traceable to real evidence (zero-hallucination provenance).
    """
    kind: SceneKind
    duration_s: float = Field(ge=1.0, le=10.0)
    visual_prompt: str                       # text-free scene description for Veo / stub
    headline: Optional[str] = None           # verbatim, prominent line
    sublines: list[str] = Field(default_factory=list)   # verbatim secondary lines
    cta_text: Optional[str] = None           # verbatim CTA label
    source_field: str = "unknown"            # provenance (e.g. "tagline", "offerings")
    # The REAL scraped photo this scene is built from. KenBurns animates it; Veo
    # 3.1 image-to-video uses it as the first frame and brings it to life. So the
    # generated motion is LEARNED FROM the real place, not invented.
    seed_image_url: Optional[str] = None
    # owner ruling 2026-07-12: seeds are GENERATED; the real photo moves here as the
    # judging reference (motion-QA place fidelity) — visible never, load-bearing always.
    place_ref_url: Optional[str] = None
    # overhaul Phase D: a REAL product screen/photo composited onto this scene's generated clip
    # (so a software product is actually shown). None -> no composite.
    product_asset_url: Optional[str] = None
    product_placement: str = ""              # "" | "inset" (reserved for future placements)


class Storyboard(BaseModel):
    """An ordered, length-capped set of scenes plus the brand styling the
    compositor needs. `primary_dir` is detected from the dominant text so libass
    aligns the whole reel correctly (RTL for Arabic)."""
    business_name: str
    primary_dir: Literal["rtl", "ltr"] = "ltr"
    total_duration_s: float = 0.0
    scenes: list[ReelScene] = Field(default_factory=list)

    palette_hex: list[str] = Field(default_factory=list, max_length=6)
    primary_color: Optional[str] = None
    tone: Optional[str] = None
    # The reel's marketing archetype (poster parity) — steers the generated SCENE composition
    # (text-to-video prompts). None/Optional keeps old storyboards valid (backward-compatible).
    marketing_archetype: Optional[str] = None

    logo_url: Optional[str] = Field(default=None, max_length=1000)
    logo_text: Optional[str] = None
    heading_font: Optional[str] = Field(default=None, max_length=80)
    body_font: Optional[str] = Field(default=None, max_length=80)

    # Scraped PHOTOGRAPHIC brand reference (profile.visual.hero_image_url). When
    # set, Veo seeds the brand-bookend scenes (intro/outro) via image-to-video so
    # the reel resembles the brand's REAL imagery (#4). None -> pure text-to-video.
    reference_image_url: Optional[str] = Field(default=None, max_length=1000)

    # The brand's REAL on-page photos (profile.visual.content_images), logos
    # excluded. The FAITHFUL reel (Ken Burns) animates THESE so it comes from the
    # actual place. Empty when the scrape found only a logo.
    content_images: list[str] = Field(default_factory=list, max_length=12)

    music_path: Optional[str] = None
    warnings: list[str] = Field(default_factory=list)


class VideoClipResult(BaseModel):
    """One generated scene clip (before overlay/compositing)."""
    scene_index: int
    clip_path: str
    duration_s: float
    provider: str
    fallback_used: bool = False
    fallback_reason: Optional[str] = None


class ReelRenderResult(BaseModel):
    """The final muxed reel."""
    reel_path: str
    filename: str
    width: int
    height: int
    duration_s: float
    fps: int = 30
    mime_type: Literal["video/mp4"] = "video/mp4"
    provider: str
    fallback_used: bool = False
    has_audio: bool = False
    scene_qa: list = []              # per-scene motion-QA verdict dicts (additive, 2026-07-12)


# ---------------------------------------------------------------------
# Web API contract — POST /api/reel/from-profile
# ---------------------------------------------------------------------

class ReelFromProfileRequest(BaseModel):
    profile: dict[str, Any]
    # 5 calmer ~4s shots => ~17s reel (was 3 short shots => ~4.5s frantic reel).
    n_scenes: int = Field(default=5, ge=2, le=6)
    # Output language for captions + spoken voiceover: "ar"/"en" force it (owner's
    # choice); "auto" infers from the brand's audience (the old behavior).
    language: Literal["auto", "ar", "en"] = "auto"


class ReelFromProfileResponse(BaseModel):
    video_base64: str                       # the rendered mp4, base64 (data-URI in a <video>)
    filename: str
    mime_type: Literal["video/mp4"] = "video/mp4"
    width: int = REEL_W
    height: int = REEL_H
    duration_s: float = 0.0
    scene_count: int = 0
    mode: str = ""                          # "generated" (from brand ads) | "motion" (real photos)
    business_name: str = ""
    warnings: list[str] = Field(default_factory=list)
