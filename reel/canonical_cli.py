"""Provider-free CLI for canonical Reel contract preparation.

Usage is intentionally separate from the legacy renderer:

    python -m reel canonical --domain-key ... --strategy-version-id ... --user-id ... \
        --provider google --model veo-3.1-generate-001 --surface VERTEX_AI

Gate 2D.1 stops at CONTRACT_READY and therefore cannot spend or return a Reel path.
"""
from __future__ import annotations

import argparse
import json
import sys
from typing import Optional, Sequence, TextIO

from api.persistence import CapabilityNotWired, get_persistence
from reel.canonical_contract import ReelContractError
from reel.canonical_execution import (
    CanonicalReelExecutionError,
    CanonicalReelExecutionPreferences,
    ProviderSelection,
    ProviderSurface,
    RequiredVideoCapabilities,
    prepare_canonical_reel,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m reel canonical",
        description="Compile canonical Reel authority only; performs zero provider calls.")
    parser.add_argument("--domain-key", required=True)
    parser.add_argument("--strategy-version-id", required=True)
    parser.add_argument("--user-id", required=True,
                        help="owner whose immutable StrategyVersion may be resolved")
    parser.add_argument("--provider", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--surface", required=True,
                        choices=[v.value for v in ProviderSurface])
    parser.add_argument("--platform", default="instagram_reels")
    parser.add_argument("--duration-min", type=int, default=15)
    parser.add_argument("--duration-max", type=int, default=15)
    parser.add_argument("--creative-style", default="")
    parser.add_argument("--actor-preference", default="")
    parser.add_argument("--music-preference", default="")
    parser.add_argument("--quality-cost-mode", default="standard")
    parser.add_argument("--clip-duration", type=int, default=4)
    parser.add_argument("--resolution", default="1080p")
    parser.add_argument("--require-first-last-frame", action="store_true")
    parser.add_argument("--require-reference-images", action="store_true")
    parser.add_argument("--require-native-audio", action="store_true")
    return parser


def main(argv: Optional[Sequence[str]] = None, *, persistence=None,
         stdout: Optional[TextIO] = None, stderr: Optional[TextIO] = None) -> int:
    out = stdout or sys.stdout
    err = stderr or sys.stderr
    args = _parser().parse_args(argv)
    store = persistence or get_persistence()
    try:
        context = store.canonical_strategy_context(
            domain_key=args.domain_key, user_id=args.user_id,
            run_id=args.strategy_version_id)
    except CapabilityNotWired:
        print(json.dumps({
            "code": "CANONICAL_REEL_REQUIRES_STRATEGY_AUTHORITY",
            "message": "Canonical Reel requires immutable StrategyVersion storage.",
        }), file=err)
        return 3
    if context is None:
        print(json.dumps({
            "code": "STRATEGY_VERSION_NOT_FOUND",
            "message": "The exact StrategyVersion is missing or not visible.",
        }), file=err)
        return 4
    if str(context.get("strategy_version_id") or "") != args.strategy_version_id:
        print(json.dumps({
            "code": "STRATEGY_VERSION_AUTHORITY_MISMATCH",
            "message": "Storage returned a different StrategyVersion.",
        }), file=err)
        return 5
    preferences = CanonicalReelExecutionPreferences(
        platform=args.platform,
        desired_duration_min_s=args.duration_min,
        desired_duration_max_s=args.duration_max,
        creative_style_preference=args.creative_style,
        actor_preference=args.actor_preference,
        music_preference=args.music_preference,
        quality_cost_mode=args.quality_cost_mode,
        provider=ProviderSelection(
            provider_name=args.provider, model_id=args.model,
            surface=ProviderSurface(args.surface)),
        required_capabilities=RequiredVideoCapabilities(
            first_last_frame=args.require_first_last_frame,
            reference_images=args.require_reference_images,
            native_audio=args.require_native_audio,
            clip_duration_s=args.clip_duration,
            resolution=args.resolution),
    )
    try:
        prepared = prepare_canonical_reel(context, preferences)
    except (ReelContractError, CanonicalReelExecutionError) as exc:
        print(json.dumps({"code": exc.code, "message": str(exc)}), file=err)
        return 6
    print(json.dumps(prepared.model_dump(mode="json"), ensure_ascii=False,
                     sort_keys=True), file=out)
    return 0


__all__ = ["main"]
