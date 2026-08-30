# -*- coding: utf-8 -*-
"""D-6 CLOSED (owner ruling 2026-07-19): NOTHING launches or ships to Meta without an explicit
HUMAN sign-off — legal + financial protection (PD-7). This is that gate:

    python -m launch_bundle.approve outputs/launch_bundle/<slug> --by "اسم الموافِق"

The sign-off is CONTENT-BOUND: the stored approval_token is the sha256 of the exact campaign
plan, so approving plan A can never carry over to a silently-mutated plan B — a tampered
campaign_plan.json makes approval REFUSE. Refusal is the default; approval is explicit."""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path


def approve_bundle(bundle_dir: "str | Path", *, approved_by: str) -> dict:
    """Flip DRAFT -> APPROVED after verifying the content-bound token. Returns the approval dict.
    Raises SystemExit with a clear reason on ANY mismatch (fail-closed — never half-approves)."""
    d = Path(bundle_dir)
    who = str(approved_by or "").strip()
    if not who:
        raise SystemExit("[x] --by <name> is required — an anonymous approval is not a sign-off")
    bpath, apath, cpath = d / "launch_bundle.json", d / "approval.json", d / "campaign_plan.json"
    for p in (bpath, apath, cpath):
        if not p.is_file():
            raise SystemExit(f"[x] not a bundle dir (missing {p.name}): {d}")
    bundle = json.loads(bpath.read_text(encoding="utf-8"))
    approval = json.loads(apath.read_text(encoding="utf-8"))

    # Content-bound integrity: the plan on disk must hash to the token minted at assembly.
    from launch_bundle.schemas import CampaignPlanJSON

    from .bundle import approval_token
    plan = CampaignPlanJSON.model_validate(json.loads(cpath.read_text(encoding="utf-8")))
    expect = approval.get("approval_token") or ""
    actual = approval_token(plan)
    if actual != expect:
        raise SystemExit("[x] REFUSED: campaign_plan.json does not match the approval token — "
                         "the plan changed after assembly. Re-run the bundle, review, then approve.")

    now = datetime.now(timezone.utc).isoformat()
    approval.update({"status": "APPROVED", "approved_by": who, "approved_at": now})
    bundle["status"] = "APPROVED"
    bundle["approval"] = {**(bundle.get("approval") or {}), **approval}
    apath.write_text(json.dumps(approval, ensure_ascii=False, indent=2), encoding="utf-8")
    bpath.write_text(json.dumps(bundle, ensure_ascii=False, indent=2), encoding="utf-8")
    return approval


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m launch_bundle.approve", description=__doc__)
    ap.add_argument("bundle_dir", help="outputs/launch_bundle/<slug>")
    ap.add_argument("--by", required=True, help="اسم الموافِق — recorded in the sign-off")
    args = ap.parse_args(argv)
    a = approve_bundle(args.bundle_dir, approved_by=args.by)
    print(f"[ok] APPROVED by {a['approved_by']} at {a['approved_at']} "
          f"(token {str(a.get('approval_token'))[:12]}…)")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
