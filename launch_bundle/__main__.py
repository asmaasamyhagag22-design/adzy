"""CLI: assemble a human-gated, Ads-Manager-ready Launch Bundle (U6) from a completed run.

Completes the media-buying chain the pipeline now produces end to end:

    URL -> scrape -> profile -> SWOT/TOWS -> media_plan.json  ->  python -m launch_bundle <path>

Reads a grounded MediaPlan (the `media_plan.json` competitor/full_run writes, or the `media_plan`
field of a full result JSON) and emits a DRAFT bundle: the Meta campaign plan, a compliance sheet,
the U4 policy lint, the U5 missing-info report, and a content-bound approval token. NOTHING
publishes and no budget moves (PD-7) — the owner reviews the DRAFT and approves in Ads Manager by
hand. Purely a reader/assembler over already-produced artifacts; no network, no spend.

Usage:
    python -m launch_bundle path/to/media_plan.json
    python -m launch_bundle path/to/run_dir            # finds media_plan.json inside
    python -m launch_bundle path/to/result.json        # uses its .media_plan + .profile
    python -m launch_bundle <path> --out outputs/launch_bundle/acme
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Optional, Tuple

from launch_bundle import assemble_launch_bundle
from media_plan.schemas import MediaPlan


def _slugify(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (name or "bundle").strip().lower()).strip("-")
    return s or "bundle"


def _profile_of(result: Optional[dict]) -> dict:
    return result["profile"] if isinstance(result, dict) and isinstance(result.get("profile"), dict) else {}


def _competitor_count(result: Optional[dict]) -> int:
    comps = (result or {}).get("competitors") if isinstance(result, dict) else None
    return len(comps) if isinstance(comps, list) else 0


def _business_name(profile: dict, plan: MediaPlan) -> str:
    n = profile.get("name") if isinstance(profile, dict) else None
    if isinstance(n, dict):
        n = n.get("value")
    return str(n).strip() if n else (getattr(plan, "run_id", "") or "bundle")


def _load(path: Path) -> Tuple[MediaPlan, dict, Optional[dict]]:
    """(media_plan, profile_dict, result_doc) from a media_plan.json, a run dir, or a result JSON.
    profile/result are best-effort — used only to enrich the U5 advisor, never to fabricate."""
    if path.is_dir():
        mp = path / "media_plan.json"
        if not mp.exists():
            raise SystemExit(f"no media_plan.json in {path}")
        result = None
        for cand in sorted(path.glob("*.json")):
            if cand.name == "media_plan.json":
                continue
            try:
                doc = json.loads(cand.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if isinstance(doc, dict) and ("profile" in doc or "media_plan" in doc):
                result = doc
                break
        plan = MediaPlan.model_validate_json(mp.read_text(encoding="utf-8"))
        return plan, _profile_of(result), result

    if not path.exists():
        raise SystemExit(f"path not found: {path}")
    doc = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(doc, dict) and doc.get("media_plan"):          # a full result JSON
        return MediaPlan.model_validate(doc["media_plan"]), _profile_of(doc), doc
    return MediaPlan.model_validate(doc), {}, None               # a bare media_plan.json


def _summary(bundle, out: Path) -> str:
    cp = bundle.campaign_plan
    n_adsets = sum(len(getattr(c, "ad_sets", []) or []) for c in cp.campaigns)
    approval = getattr(bundle, "approval", None)
    token = (getattr(approval, "approval_token", "") or "")[:12]
    verdict = getattr(bundle.policy_lint, "verdict", "?")
    gaps = len(getattr(bundle.advisor, "gaps", []) or [])
    lines = [
        f"Launch bundle DRAFT — run {bundle.run_id}   status={bundle.status}",
        f"  campaigns : {len(cp.campaigns)}  ({n_adsets} ad set(s))",
        f"  policy    : {verdict}",
        f"  advisor   : {gaps} missing-info gap(s)",
        f"  approval  : {getattr(approval, 'status', 'DRAFT')} token={token}…  (human sign-off, PD-7)",
        f"  written   : {out}",
    ]
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m launch_bundle", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("path", help="media_plan.json, a run directory, or a full result JSON")
    ap.add_argument("--out", default=None,
                    help="output dir (default: outputs/launch_bundle/<business-slug>)")
    args = ap.parse_args(argv)

    plan, profile, result = _load(Path(args.path))
    name = _business_name(profile, plan)
    out = Path(args.out) if args.out else Path("outputs/launch_bundle") / _slugify(name)

    bundle = assemble_launch_bundle(
        profile=profile, media_plan=plan,
        competitor_count=_competitor_count(result),
        out_dir=out)

    print(_summary(bundle, out))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
