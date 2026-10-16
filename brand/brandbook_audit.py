"""Per-brand identity audit: what every stored brand resolves to, and whether it is safe.

`python -m brand.brandbook_audit` answers, for each stored brand, the only questions that
matter after the 2026-09-01 identity repair:

  * which brandbook does this brand resolve to NOW?
  * does that artifact's own embedded identity agree?
  * which legacy files still exist that this brand's display name used to address?
  * could any other stored brand reach the same file?

Read-only. It never builds, migrates or deletes anything.
"""
from __future__ import annotations

import json
from pathlib import Path

from brand.brandbook_identity import (DEFAULT_CACHE_DIR, canonical_path, legacy_candidate_paths,
                                      resolve_brandbook)
from brand.brandbook_migration import load_stored_brands


def audit_brands(cache_dir: str | Path = DEFAULT_CACHE_DIR,
                 brands_dir: str | Path = "outputs/webapp/brands") -> list[dict]:
    brands = load_stored_brands(brands_dir)
    rows: list[dict] = []
    # Which canonical path does each brand claim? A repeat here would be a live collision.
    claims: dict[str, list[str]] = {}
    for b in brands:
        p = str(canonical_path(b.domain_key, cache_dir=cache_dir))
        claims.setdefault(p, []).append(b.domain_key)

    for b in brands:
        lookup = resolve_brandbook(b.profile, b.domain_key, cache_dir=cache_dir)
        path = canonical_path(b.domain_key, cache_dir=cache_dir)
        legacy = [p.name for p in legacy_candidate_paths(b.profile, cache_dir=cache_dir)
                  if p.exists()]
        sharers = [k for k in claims[str(path)] if k != b.domain_key]
        if lookup.exists:
            status = "RESOLVED"
        elif lookup.code:
            status = lookup.code
        else:
            status = "NO_BRANDBOOK_YET"
        rows.append({
            "domain_key": b.domain_key,
            "display_name": b.display_name,
            "canonical_path": path.name,
            "legacy_files": legacy,
            "identity_status": status,
            "migration_action": ("none" if lookup.exists else
                                 ("migrate/rebuild deliberately" if legacy else
                                  "build on first render")),
            "cross_brand_risk": ("HIGH — shares a path with " + ", ".join(sharers)) if sharers
                                else "none",
            "reason": lookup.reason,
        })
    return rows


def main() -> int:
    import argparse
    import sys
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")   # type: ignore[attr-defined]
        except Exception:
            pass
    ap = argparse.ArgumentParser(prog="brand.brandbook_audit")
    ap.add_argument("--cache-dir", default=DEFAULT_CACHE_DIR)
    ap.add_argument("--brands-dir", default="outputs/webapp/brands")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    rows = audit_brands(args.cache_dir, args.brands_dir)
    if args.json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
        return 0

    hdr = (f"{'domain_key':24} {'display_name':34} {'canonical brandbook':32} "
           f"{'status':28} risk")
    print(hdr)
    print("-" * len(hdr))
    for r in rows:
        name = r["display_name"]
        name = (name[:31] + "...") if len(name) > 34 else name
        print(f"{r['domain_key']:24} {name:34} {r['canonical_path']:32} "
              f"{r['identity_status']:28} {r['cross_brand_risk']}")
    print()
    for r in rows:
        if r["legacy_files"]:
            print(f"  {r['domain_key']}: legacy files still on disk -> {', '.join(r['legacy_files'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
