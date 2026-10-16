"""Reel Studio.

The BusinessProfile pipeline documented below is the explicitly isolated LEGACY renderer.
Canonical StrategyVersion authority is defined in :mod:`reel.canonical_contract` and never calls
this renderer during Gate 2D.1.

The natural extension of the Poster Studio: where the poster uses Imagen for a
still + an HTML/CSS overlay, the reel uses **Veo** for the moving visual + an
**ffmpeg/libass** overlay. Same two hard rules carry over:

  * ZERO HALLUCINATION — every overlaid word (logo / headline / offering / CTA /
    contact) is VERBATIM scraped evidence selected from the profile, or omitted.
    The reel reuses the poster's verbatim selectors as the single source of truth.
  * TEXT-FREE VISUAL + VERBATIM OVERLAY — Veo generates the scene with NO text
    (it garbles Arabic, exactly like Imagen — poster decisions §5); all text is
    overlaid afterward by libass, which shapes Arabic RTL correctly.

Pipeline:  profile -> build_reel_brief -> build_storyboard -> VideoProvider
           (Veo | stub) per scene -> ffmpeg compositor (crossfade + RTL subtitle
           overlay + logo + music) -> .mp4

Verifiable offline via StubVideoProvider (brand-palette animated gradients), so
the whole pipeline runs here without Google credentials — live Veo runs in GCP.
"""
from __future__ import annotations

from .from_profile import build_reel_brief, is_rtl
from .storyboard import build_storyboard
from .schemas import ReelScene, Storyboard, ReelRenderResult, REEL_W, REEL_H
from .canonical_contract import (
    ClaimIntent,
    ClaimSurface,
    ReelCreativeContract,
    ReelCreativeContractCompiler,
    authorize_claim,
)
from .canonical_creative import (
    CanonicalReelExecutionPlan,
    ConceptTournament,
    CreativeConcept,
    Platform,
    PlatformProfile,
    ReelBlueprint,
    ShotContract,
    compile_canonical_creative_plan,
)
from .canonical_production import (
    CompiledAudioRequest,
    CompiledSeedRequest,
    CompiledVideoRequest,
    ShotExecutionManifest,
    dry_run_execution_plan,
)
from .canonical_media_preconditions import (
    CanonicalMediaCostPlanner,
    MediaPricingRegistry,
    ProviderSurfaceIdentity,
    SafeZoneAssetRegistry,
)

__all__ = [
    "build_reel_brief",
    "is_rtl",
    "build_storyboard",
    "ReelScene",
    "Storyboard",
    "ReelRenderResult",
    "REEL_W",
    "REEL_H",
    "ClaimIntent",
    "ClaimSurface",
    "ReelCreativeContract",
    "ReelCreativeContractCompiler",
    "authorize_claim",
    "CanonicalReelExecutionPlan",
    "ConceptTournament",
    "CreativeConcept",
    "Platform",
    "PlatformProfile",
    "ReelBlueprint",
    "ShotContract",
    "compile_canonical_creative_plan",
    "CompiledAudioRequest",
    "CompiledSeedRequest",
    "CompiledVideoRequest",
    "ShotExecutionManifest",
    "dry_run_execution_plan",
    "CanonicalMediaCostPlanner",
    "MediaPricingRegistry",
    "ProviderSurfaceIdentity",
    "SafeZoneAssetRegistry",
]
