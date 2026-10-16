"""Network-impossible CLI for inspecting canonical production requests."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Optional, Sequence, TextIO

from pydantic import ValidationError

from .canonical_creative import CanonicalReelExecutionPlan
from .canonical_execution import VERTEX_VEO_31, VERTEX_VEO_31_FAST
from .canonical_production import dry_run_execution_plan


_VIDEO_PROFILES = {
    VERTEX_VEO_31.model_id: VERTEX_VEO_31,
    VERTEX_VEO_31_FAST.model_id: VERTEX_VEO_31_FAST,
}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m reel execution-dry-run",
        description=(
            "Compile inspectable seed/video/audio/QA request metadata from an OFFLINE "
            "CanonicalReelExecutionPlan fixture. Never executes providers or DB reads."))
    parser.add_argument("plan", help="provider-free CanonicalReelExecutionPlan JSON fixture")
    parser.add_argument("--video-model", choices=tuple(_VIDEO_PROFILES),
                        default=VERTEX_VEO_31.model_id)
    parser.add_argument("--pretty", action="store_true")
    return parser


def main(argv: Optional[Sequence[str]] = None, *, stdout: Optional[TextIO] = None,
         stderr: Optional[TextIO] = None) -> int:
    out = stdout or sys.stdout
    err = stderr or sys.stderr
    args = _parser().parse_args(argv)
    path = Path(args.plan)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        plan = CanonicalReelExecutionPlan.model_validate(payload)
    except (OSError, json.JSONDecodeError, ValidationError) as error:
        print(json.dumps({
            "code": "OFFLINE_EXECUTION_PLAN_INVALID",
            "message": str(error),
            "provider_calls": 0,
            "network_calls": 0,
            "spend_usd": "0",
        }, ensure_ascii=False, sort_keys=True), file=err)
        return 3
    report = dry_run_execution_plan(
        plan, provider_capabilities=_VIDEO_PROFILES[args.video_model])
    print(json.dumps(report.model_dump(mode="json"), ensure_ascii=False,
                     sort_keys=True, indent=2 if args.pretty else None), file=out)
    return 0


__all__ = ["main"]
