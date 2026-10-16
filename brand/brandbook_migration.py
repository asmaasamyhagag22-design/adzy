"""Audit and migrate legacy brandbooks onto `domain_key` identity.

READ-ONLY BY DEFAULT. `python -m brand.brandbook_migration` inventories every artifact under
`outputs/brandbooks/` and prints what it WOULD do; `--apply` performs only the migrations whose
ownership is proven from the artifact's own content plus the stored brand catalog.

Why an artifact needs proving at all: the legacy filename was a slug of the DISPLAY NAME, so it
is not evidence of ownership — it is the thing that broke. `brand_dna.json` in particular was
the shared bucket for every Arabic-named brand, and is treated as contaminated regardless of
what it happens to contain (see CROSS_BRAND_CONTAMINATED below).

CLASSIFICATIONS
  ALREADY_CANONICAL         filename is <domain_key>_dna.json AND the embedded key agrees.
  SAFE_EXACT_MIGRATION      exactly one stored brand claims it, and the artifact's own
                            references_seen corroborate that brand's registrable domain.
  AMBIGUOUS_LEGACY_ARTIFACT a stored brand matches by NAME only — no reference corroborates it,
                            so ownership is asserted rather than proven. Never auto-migrated.
  ORPHAN_ARTIFACT           no stored brand claims it. Harmless, but nothing may load it.
  CROSS_BRAND_CONTAMINATED  it occupies a slot that more than one brand addressed (today: the
                            literal `brand_dna.json`). Never migrated, never deleted here.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlparse

from brand.brandbook_identity import (DEFAULT_CACHE_DIR, canonical_path, legacy_name_slug,
                                      normalize_domain_key, read_brandbook)

ALREADY_CANONICAL = "ALREADY_CANONICAL"
SAFE_EXACT_MIGRATION = "SAFE_EXACT_MIGRATION"
AMBIGUOUS_LEGACY_ARTIFACT = "AMBIGUOUS_LEGACY_ARTIFACT"
ORPHAN_ARTIFACT = "ORPHAN_ARTIFACT"
CROSS_BRAND_CONTAMINATED = "CROSS_BRAND_CONTAMINATED"

# The slot the broken slug produced. Any brand whose display name had no ASCII alphanumerics
# landed here, so the file cannot be attributed by its NAME under any circumstances.
SHARED_LEGACY_SLOT = "brand_dna.json"


def _sha256(path: Path) -> str:
    import hashlib
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _host(url: str) -> str:
    try:
        return (urlparse(url if "://" in url else "https://" + url).hostname or "").lower()
    except Exception:
        return ""


def _registrable(host: str) -> str:
    try:
        from scraper.url_utils import _registrable_domain
        return (_registrable_domain("https://" + host) or "").lower() if host else ""
    except Exception:
        parts = [p for p in host.split(".") if p]
        return ".".join(parts[-2:]) if len(parts) >= 2 else host


@dataclass
class StoredBrand:
    domain_key: str
    display_name: str = ""
    source_url: str = ""
    profile: dict = field(default_factory=dict)

    @property
    def registrable(self) -> str:
        return _registrable(_host(self.source_url))


def load_stored_brands(brands_dir: str | Path = "outputs/webapp/brands") -> list[StoredBrand]:
    """The brand catalog — the authority on which domain_keys exist and what they are called."""
    out: list[StoredBrand] = []
    d = Path(brands_dir)
    if not d.is_dir():
        return out
    for p in sorted(d.glob("*.json")):
        if ".bak." in p.name or p.name.endswith(".lock"):
            continue
        try:
            o = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        key = normalize_domain_key(str(o.get("domain_key") or ""))
        if not key:
            continue
        prof = o.get("_profile") or {}
        def _fv(d: dict, k: str) -> str:
            v = (d or {}).get(k)
            if isinstance(v, dict):
                v = v.get("value")
            return "" if v is None else str(v)
        out.append(StoredBrand(
            domain_key=key,
            display_name=(_fv(prof, "name") or str(o.get("organization_name") or "")),
            source_url=(_fv(prof, "final_url") or _fv(prof, "source_url")
                        or str(o.get("source_url") or "")),
            profile=prof,
        ))
    return out


@dataclass
class ArtifactRecord:
    """One legacy/canonical file, and everything known about who it belongs to."""
    filename: str
    path: str
    content_hash: str
    business_name: str = ""
    embedded_domain_key: str = ""
    embedded_source_url: str = ""
    reference_hosts: list[str] = field(default_factory=list)
    candidate_domain_key: str = ""
    classification: str = ""
    confidence: str = ""
    evidence: list[str] = field(default_factory=list)
    migration_action: str = ""
    cross_brand_risk: str = ""
    is_creative_dna: bool = True

    def as_row(self) -> dict:
        return {
            "filename": self.filename, "business_name": self.business_name,
            "embedded_domain_key": self.embedded_domain_key,
            "candidate_domain_key": self.candidate_domain_key,
            "classification": self.classification, "confidence": self.confidence,
            "migration_action": self.migration_action,
            "cross_brand_risk": self.cross_brand_risk,
            "content_hash": self.content_hash[:16],
        }


_DNA_MARKERS = ("layout_philosophy", "composition_patterns", "signature_moves")


def inspect_artifact(path: Path, brands: list[StoredBrand]) -> ArtifactRecord:
    """Classify ONE file from its own content plus the brand catalog. Never writes."""
    rec = ArtifactRecord(filename=path.name, path=str(path), content_hash=_sha256(path))
    try:
        o = read_brandbook(path)
    except (OSError, ValueError) as exc:
        rec.classification = ORPHAN_ARTIFACT
        rec.confidence = "none"
        rec.evidence.append(f"unreadable: {exc}")
        rec.migration_action = "none (unreadable)"
        rec.is_creative_dna = False
        return rec

    rec.business_name = str(o.get("business_name") or "")
    rec.embedded_domain_key = normalize_domain_key(str(o.get("domain_key") or ""))
    rec.embedded_source_url = str(o.get("source_url") or "")
    rec.is_creative_dna = any(m in o for m in _DNA_MARKERS)
    refs = [r for r in (o.get("references_seen") or []) if isinstance(r, str)]
    hosts = [h for h in (_host(r) for r in refs) if h]
    rec.reference_hosts = sorted(set(hosts))

    if not rec.is_creative_dna:
        rec.classification = ORPHAN_ARTIFACT
        rec.confidence = "n/a"
        rec.evidence.append("not a BrandCreativeDNA artifact (different schema)")
        rec.migration_action = "leave in place"
        return rec

    # --- the shared bucket: unattributable by construction -------------------------------
    if path.name == SHARED_LEGACY_SLOT:
        claimants = [b for b in brands if legacy_name_slug(b.display_name) == "brand"]
        rec.cross_brand_risk = (
            f"HIGH — {len(claimants)} stored brands address this same file: "
            + ", ".join(b.domain_key for b in claimants))
        rec.classification = CROSS_BRAND_CONTAMINATED
        rec.confidence = "refused"
        rec.evidence.append(
            "the display-name slug for every non-Latin brand name collapses to 'brand', so this "
            "filename is shared and proves nothing about ownership")
        # Content MAY still point at one owner; record it as information, never as permission.
        by_ref = _owner_by_reference(rec, brands)
        if by_ref:
            rec.evidence.append(
                f"content references resolve to {by_ref.domain_key} "
                f"({by_ref.registrable}) — informational only; NOT a migration warrant")
        rec.migration_action = "quarantine: never satisfies a canonical lookup; retain for audit"
        return rec

    # --- already canonical ---------------------------------------------------------------
    stem_key = normalize_domain_key(path.name[:-len("_dna.json")]) if path.name.endswith("_dna.json") else ""
    if stem_key and rec.embedded_domain_key == stem_key:
        rec.candidate_domain_key = stem_key
        rec.classification = ALREADY_CANONICAL
        rec.confidence = "proven"
        rec.evidence.append("filename and embedded domain_key agree")
        rec.migration_action = "none"
        return rec

    # --- attribute by content, then corroborate against the catalog ----------------------
    owner_ref = _owner_by_reference(rec, brands)
    owner_name = _owner_by_name(rec, brands)

    if owner_ref is not None and (owner_name is None or owner_name.domain_key == owner_ref.domain_key):
        rec.candidate_domain_key = owner_ref.domain_key
        rec.classification = SAFE_EXACT_MIGRATION
        rec.confidence = "proven"
        rec.evidence.append(
            f"references_seen resolve to {owner_ref.registrable}, the registrable domain of "
            f"stored brand {owner_ref.domain_key}")
        if owner_name is not None:
            rec.evidence.append(f"business_name also matches {owner_name.domain_key}")
        rec.migration_action = f"copy -> {canonical_path(owner_ref.domain_key).name}"
        rec.cross_brand_risk = "none"
        return rec

    if owner_ref is not None and owner_name is not None:
        rec.classification = AMBIGUOUS_LEGACY_ARTIFACT
        rec.confidence = "conflicting"
        rec.evidence.append(
            f"references point at {owner_ref.domain_key} but the name matches "
            f"{owner_name.domain_key}")
        rec.cross_brand_risk = "HIGH — two brands have a claim"
        rec.migration_action = "none (needs a human)"
        return rec

    if owner_name is not None:
        rec.candidate_domain_key = owner_name.domain_key
        rec.classification = AMBIGUOUS_LEGACY_ARTIFACT
        rec.confidence = "name-only"
        rec.evidence.append(
            f"business_name matches stored brand {owner_name.domain_key}, but no reference "
            f"corroborates it — the name alone is what this repair distrusts")
        rec.cross_brand_risk = "MEDIUM — asserted, not proven"
        rec.migration_action = "none (re-derive or migrate deliberately)"
        return rec

    rec.classification = ORPHAN_ARTIFACT
    rec.confidence = "none"
    rec.evidence.append("no stored brand claims this artifact")
    rec.cross_brand_risk = "none (unreachable by any canonical lookup)"
    rec.migration_action = "leave in place"
    return rec


def _owner_by_reference(rec: ArtifactRecord, brands: list[StoredBrand]) -> Optional[StoredBrand]:
    """The one stored brand whose registrable domain the artifact's OWN references point at.

    Requires a strict majority of resolvable reference hosts, so a couple of Behance/portfolio
    hits cannot outvote the brand's own CDN, and two brands can never both qualify.
    """
    if not rec.reference_hosts:
        return None
    regs = [_registrable(h) for h in rec.reference_hosts]
    regs = [r for r in regs if r]
    if not regs:
        return None
    hits: dict[str, StoredBrand] = {}
    counts: dict[str, int] = {}
    for b in brands:
        if not b.registrable:
            continue
        n = sum(1 for r in regs if r == b.registrable)
        if n:
            hits[b.domain_key] = b
            counts[b.domain_key] = n
    if len(hits) != 1:
        return None
    key = next(iter(hits))
    return hits[key] if counts[key] * 2 > len(regs) else None


def _owner_by_name(rec: ArtifactRecord, brands: list[StoredBrand]) -> Optional[StoredBrand]:
    """The one stored brand whose legacy name-slug would have produced this filename."""
    if not rec.filename.endswith("_dna.json"):
        return None
    stem = rec.filename[:-len("_dna.json")]
    matches = [b for b in brands
               if legacy_name_slug(b.display_name) == stem and stem != "brand"]
    if len(matches) == 1:
        return matches[0]
    # Fall back to the artifact's own recorded business_name.
    if rec.business_name and legacy_name_slug(rec.business_name) == stem and stem != "brand":
        named = [b for b in brands
                 if b.display_name.strip().lower() == rec.business_name.strip().lower()]
        if len(named) == 1:
            return named[0]
    return None


def audit(cache_dir: str | Path = DEFAULT_CACHE_DIR,
          brands_dir: str | Path = "outputs/webapp/brands") -> list[ArtifactRecord]:
    brands = load_stored_brands(brands_dir)
    d = Path(cache_dir)
    if not d.is_dir():
        return []
    return [inspect_artifact(p, brands) for p in sorted(d.glob("*.json"))]


def apply_migrations(records: list[ArtifactRecord], *,
                     cache_dir: str | Path = DEFAULT_CACHE_DIR,
                     brands_dir: str | Path = "outputs/webapp/brands",
                     dry_run: bool = True,
                     accept_name_only: bool = False) -> list[dict]:
    """COPY (never move) each proven artifact to its canonical path, stamping identity in.

    The legacy file is left untouched: the migration must be re-runnable and reversible, and a
    deletion here would destroy the only provenance for an artifact whose classification a human
    may later dispute.
    """
    from brand.brandbook_identity import stamp_identity
    from brand.creative_dna import BrandCreativeDNA
    brands = {b.domain_key: b for b in load_stored_brands(brands_dir)}
    done: list[dict] = []
    # §15: a name-only artifact may cross over, but only on an explicit human decision, and it
    # is recorded as LEGACY_VERIFIED_MIGRATION in the file rather than passing as canonical.
    eligible = {SAFE_EXACT_MIGRATION}
    if accept_name_only:
        eligible.add(AMBIGUOUS_LEGACY_ARTIFACT)
    for rec in records:
        if rec.classification not in eligible or not rec.candidate_domain_key:
            continue
        if rec.classification == AMBIGUOUS_LEGACY_ARTIFACT and rec.confidence != "name-only":
            continue                       # conflicting claims are never operator-resolvable here
        dest = canonical_path(rec.candidate_domain_key, cache_dir=cache_dir)
        if dest.exists():
            done.append({"file": rec.filename, "dest": dest.name, "status": "skipped",
                         "reason": "canonical artifact already exists (never regenerate over one)"})
            continue
        entry = {"file": rec.filename, "dest": dest.name,
                 "domain_key": rec.candidate_domain_key,
                 "source_hash": rec.content_hash[:16]}
        if dry_run:
            entry["status"] = "would-migrate"
            done.append(entry)
            continue
        payload = read_brandbook(Path(rec.path))
        b = brands.get(rec.candidate_domain_key)
        dna = BrandCreativeDNA.model_validate(payload)
        dna = stamp_identity(dna, domain_key=rec.candidate_domain_key,
                             profile=(b.profile if b else {}))
        # Preserve the original artifact's provenance alongside the new identity.
        try:
            state = ("LEGACY_VERIFIED_MIGRATION" if rec.classification == SAFE_EXACT_MIGRATION
                     else "LEGACY_NAME_ONLY_ACCEPTED_BY_OPERATOR")
            dna.note = ((dna.note or "") +
                        f" [{state}: migrated from {rec.filename} "
                        f"sha256:{rec.content_hash[:16]}]").strip()
        except Exception:
            pass
        from brand.brandbook_identity import save_brandbook
        written = save_brandbook(dna, domain_key=rec.candidate_domain_key,
                                 profile=(b.profile if b else {}), cache_dir=cache_dir)
        entry["status"] = "migrated"
        entry["written"] = str(written)
        entry["dna_hash"] = dna.dna_hash
        done.append(entry)
    return done


def _print_table(records: list[ArtifactRecord]) -> None:
    hdr = f"{'file':44} {'classification':26} {'candidate domain_key':24} {'conf':12} risk"
    print(hdr)
    print("-" * len(hdr))
    for r in records:
        print(f"{r.filename:44} {r.classification:26} {r.candidate_domain_key or '-':24} "
              f"{r.confidence:12} {r.cross_brand_risk or '-'}")


def main() -> int:
    import argparse
    import sys
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")   # type: ignore[attr-defined]
        except Exception:
            pass
    ap = argparse.ArgumentParser(prog="brand.brandbook_migration",
                                 description="Audit/migrate brandbooks onto domain_key identity.")
    ap.add_argument("--cache-dir", default=DEFAULT_CACHE_DIR)
    ap.add_argument("--brands-dir", default="outputs/webapp/brands")
    ap.add_argument("--apply", action="store_true", help="perform the proven migrations")
    ap.add_argument("--accept-name-only", action="store_true",
                    help="ALSO migrate AMBIGUOUS name-only artifacts — an explicit operator "
                         "decision, recorded in each file's note")
    ap.add_argument("--json", action="store_true", help="emit the inventory as JSON")
    args = ap.parse_args()

    records = audit(args.cache_dir, args.brands_dir)
    if args.json:
        print(json.dumps([r.as_row() | {"evidence": r.evidence} for r in records],
                         ensure_ascii=False, indent=2))
    else:
        _print_table(records)
        print()
        for r in records:
            if r.classification in (SAFE_EXACT_MIGRATION, CROSS_BRAND_CONTAMINATED,
                                    AMBIGUOUS_LEGACY_ARTIFACT):
                print(f"* {r.filename}  [{r.classification}]")
                for e in r.evidence:
                    print(f"    - {e}")
                print(f"    action: {r.migration_action}")

    results = apply_migrations(records, cache_dir=args.cache_dir, brands_dir=args.brands_dir,
                               dry_run=not args.apply,
                               accept_name_only=args.accept_name_only)
    print("\n=== migrations " + ("APPLIED" if args.apply else "(dry run)") + " ===")
    for e in results:
        print("  " + json.dumps(e, ensure_ascii=False))
    if not results:
        print("  (none)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
