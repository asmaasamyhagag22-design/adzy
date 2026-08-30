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
    # THE PRODUCT INSERT (2026-08-01). Non-empty -> this scene is not generated at all: the
    # compositor renders it from the brand's REAL packshot (see `reel/insert_shot.py`), so the
    # label is the photograph itself instead of something a video model repaints every frame.
    # Candidates, best-first — the compositor keeps the first that cuts out cleanly.
    insert_product_urls: list[str] = Field(default_factory=list, max_length=6)
    # Cheat-sheet: per-word CSS delays for the hero lockup, measured off the narration's
    # ElevenLabs word timestamps. None -> the designed 0.07s stagger (the schedule is a
    # bonus, never a dependency).
    caption_word_delays: Optional[list[float]] = None


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
    # C1 (2026-07-15): the run hit VEO_MAX_USD_PER_RUN and the remaining scenes fell back. This is
    # a BUDGET DECISION, not a provider failure — HITL must be able to offer "approve an override
    # or shorten the reel" instead of showing "the video model broke".
    budget_stopped: bool = False
    budget_note: str = ""


# ---------------------------------------------------------------------
# Web API contract — POST /api/reel/from-profile
# ---------------------------------------------------------------------

class ReelFromProfileRequest(BaseModel):
    profile: dict[str, Any]
    # A CEILING, NOT A COUNT (2026-08-01). The route now derives the real count with the same
    # `_scenes_for` the CLI uses — from `max_seconds` and whether the last beat is a rendered
    # product insert — and takes this only when it is SMALLER. Before that the browser's hardcoded
    # 5 was used verbatim, so the dashboard and the CLI shipped different films.
    n_scenes: int = Field(default=5, ge=2, le=6)
    # The length the film is CUT to. 15s is the format the owner named; the scene count follows
    # from it rather than the other way round.
    max_seconds: float = Field(default=15.0, ge=6.0, le=60.0)
    # Output language for captions + spoken voiceover: "ar"/"en" force it (owner's
    # choice); "auto" infers from the brand's audience (the old behavior).
    language: Literal["auto", "ar", "en"] = "auto"
    # THE PRODUCT THE OWNER NAMED. A pasted product-page link makes the film about THAT item: the
    # insert cuts to its real packshot from the catalogue instead of whichever entry ranked first.
    # Empty is the old behaviour exactly.
    hero_product_url: str = ""


class ReelFromProfileResponse(BaseModel):
    video_base64: str                       # the rendered mp4, base64 (data-URI in a <video>)
    filename: str
    mime_type: Literal["video/mp4"] = "video/mp4"
    width: int = REEL_W
    height: int = REEL_H
    duration_s: float = 0.0
    scene_count: int = 0
    mode: str = ""                          # "creative" (director) | "motion" (real photos)
    business_name: str = ""
    warnings: list[str] = Field(default_factory=list)
    # The post-ready caption that rides WITH the reel (owner: «فوق الريل يبقى مكتوب كلام زي
    # السوشيال»). The poster path returns one already; without it here the owner has to leave
    # the studio to write the words for the video she just made.
    social_caption: str = ""
    hashtags: list[str] = Field(default_factory=list)
    # What the final screening saw: "clean" | "issues: ..." | "not screened". Never absent —
    # a reel nobody could screen must not read as a screened one.
    screening: str = ""
    # The delivery container, measured on the file that shipped: "ok" | "issues: ..." |
    # "not finalised". Never absent — a container nobody could check must not read as checked.
    envelope: str = ""
    # The ONE blocking decision (directive 6.4): "ok" | "blocked: ..." | "unverified".
    artifact_verdict: str = ""
