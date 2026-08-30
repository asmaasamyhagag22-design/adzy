"""Evidence Ledger — Step 1 measurement (READ-ONLY, no blocking).

Sizes the problem the Ledger exists to solve: across the real saved-profile corpus, how
often does each copy path emit a customer-facing line that carries a HARD CLAIM with NO
source in the brand's evidence? Nothing is blocked or changed in the live pipeline — we
call the existing copy builders and AUDIT their output.

Two measured paths per brand:
  A. Verbatim SELECTION path (poster/from_profile.build_poster_brief) — no LLM. Expected
     ~0 unsourced: this is the sanity floor proving the Ledger doesn't false-positive on
     genuinely grounded copy.
  B. LLM GENERATION path (poster/concept.build_creative_concept) — the real risk surface.
     Needs a live caller (Gemini/Vertex). The headline number comes from here.

Run:  python -m grounding.measure_step1            (both paths; B needs a caller)
      python -m grounding.measure_step1 --no-llm   (path A only, fully offline)
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Optional

# Windows cp1252 console crashes when printing Arabic — force UTF-8 (same as poster/__main__).
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:
        pass

from grounding.ledger import AuditReport, EvidenceLedger

# Corpus chosen by the mapping agent: 6 real profiles, 4 verticals, AR + EN.
CORPUS = [
    "te_eg_gemini.json",                 # telecom, AR, 12 offerings
    "iti_home_v2.json",                  # education/gov, EN, 8 trust signals
    "digilians_profile_real.json",       # education/gov youth, AR
    "elkbabgi_profile_real.json",        # restaurant, EN
    "nti_profile_fresh.json",            # training, EN
    "api_full_result_after_hotfix.json", # healthcare clinics, AR (wrapped)
]

CHANNELS = "(poster concept + verbatim brief)"


def _load(path: str) -> Optional[dict]:
    p = Path(path)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        print(f"  ! could not read {path}: {exc}")
        return None


def _unwrap(d: dict) -> dict:
    if isinstance(d, dict) and "value_propositions" not in d and isinstance(d.get("profile"), dict):
        return d["profile"]
    return d


def _brief_fields(profile: dict) -> dict[str, str]:
    """Path A — verbatim selection copy (no LLM)."""
    from poster.from_profile import build_poster_brief
    brief = build_poster_brief(profile)
    fields = {
        "headline": brief.headline or "",
        "subheadline": getattr(brief, "subheadline", "") or "",
        "cta": getattr(brief, "cta_text", "") or "",
    }
    for i, off in enumerate(getattr(brief, "offerings", []) or []):
        fields[f"offering_{i}"] = str(off)
    return fields


def _concept_fields(profile: dict, caller: Any) -> dict[str, str]:
    """Path B — LLM-generated concept copy (the risk surface)."""
    from poster.concept import build_creative_concept
    c = build_creative_concept(profile, caller=caller)
    fields = {
        "headline": c.headline or "",
        "subheadline": c.subheadline or "",
        "cta": c.cta or "",
    }
    for i, chip in enumerate(c.proof_points or []):
        fields[f"chip_{i}"] = str(chip)
    return fields


def _print_report(tag: str, name: str, rep: AuditReport) -> None:
    flag = "OK " if rep.passes else "!! "
    print(f"  {flag}[{tag}] fields={len(rep.field_audits)} "
          f"with_claims={rep.fields_with_claims} unsourced={rep.total_unsourced}/{rep.total_claims}")
    for fa in rep.unsourced_fields:
        toks = ", ".join(f"{v.claim.kind}:{v.claim.token}" for v in fa.unsourced)
        print(f"       UNSOURCED {fa.field!r}: {fa.text!r}  ->  [{toks}]")


def main(argv: list[str]) -> int:
    no_llm = "--no-llm" in argv
    caller = None
    if not no_llm:
        # Load .env so GOOGLE_CLOUD_PROJECT / ADC are visible (entry-point responsibility;
        # the library helpers stay side-effect free).
        try:
            from dotenv import load_dotenv
            load_dotenv()
        except Exception:
            pass
        try:
            from business_profile.llm import default_caller
            caller = default_caller(strong=True)
        except Exception as exc:  # noqa: BLE001
            print(f"[caller] could not build a caller: {exc}")
        if caller is None:
            print("[caller] no LLM caller available -> running path A (verbatim) only.\n"
                  "         Set up Gemini/Vertex (GOOGLE_CLOUD_PROJECT + ADC) to measure path B.")

    agg = {
        "A": {"fields": 0, "with_claims": 0, "claims": 0, "unsourced": 0, "bad_fields": 0, "n": 0},
        "B": {"fields": 0, "with_claims": 0, "claims": 0, "unsourced": 0, "bad_fields": 0, "n": 0},
    }
    sample_export = None

    def _accumulate(bucket: dict, rep: AuditReport) -> None:
        bucket["n"] += 1
        bucket["fields"] += len(rep.field_audits)
        bucket["with_claims"] += rep.fields_with_claims
        bucket["claims"] += rep.total_claims
        bucket["unsourced"] += rep.total_unsourced
        bucket["bad_fields"] += len(rep.unsourced_fields)

    n_loaded = 0
    for path in CORPUS:
        raw = _load(path)
        if raw is None:
            print(f"\n=== {path}: MISSING ===")
            continue
        n_loaded += 1
        profile = _unwrap(raw)
        name = ""
        nm = profile.get("name")
        name = nm.get("value") if isinstance(nm, dict) else (nm or path)
        ledger = EvidenceLedger.from_profile(profile)
        print(f"\n=== {name}  ({path})  evidence_entries={len(ledger.entries)} ===")

        # Path A — verbatim selection.
        try:
            repA = ledger.audit_fields(_brief_fields(profile))
            _print_report("A verbatim", name, repA)
            _accumulate(agg["A"], repA)
            if sample_export is None:
                sample_export = (name, repA.export())
        except Exception as exc:  # noqa: BLE001
            print(f"  ! path A failed: {type(exc).__name__}: {exc}")

        # Path B — LLM generation.
        if caller is not None:
            try:
                repB = ledger.audit_fields(_concept_fields(profile, caller))
                _print_report("B  concept", name, repB)
                _accumulate(agg["B"], repB)
                # Prefer an LLM export as the showcase trail (closer to production).
                sample_export = (name, repB.export())
            except Exception as exc:  # noqa: BLE001
                print(f"  ! path B (LLM) failed: {type(exc).__name__}: {exc}")

    # ---- THE NUMBER ----
    print("\n" + "=" * 64)
    print("STEP 1 RESULT — unsourced hard-claim rate (read-only, nothing blocked)")
    print("=" * 64)
    for tag, label in (("A", "Verbatim SELECTION path (sanity floor)"),
                       ("B", "LLM GENERATION path (the risk surface)")):
        a = agg[tag]
        if a["n"] == 0:
            print(f"{label}: not measured")
            continue
        pct = (100.0 * a["bad_fields"] / a["fields"]) if a["fields"] else 0.0
        print(f"{label}:")
        print(f"   brands={a['n']}  copy_fields={a['fields']}  fields_with_a_hard_claim={a['with_claims']}")
        print(f"   hard_claims={a['claims']}  UNSOURCED_claims={a['unsourced']}  "
              f"fields_that_would_be_REJECTED={a['bad_fields']} ({pct:.0f}% of copy fields)")

    if sample_export is not None:
        name, exp = sample_export
        out = Path("outputs") / "ledger_audit_sample.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps({"brand": name, "audit": exp}, ensure_ascii=False, indent=2),
                       encoding="utf-8")
        print(f"\nExportable audit-trail sample (the agency-facing artifact) -> {out}")

    # CS-53: an INCOMPLETE run (some corpus profiles missing on disk) must NOT exit 0 "success" —
    # a rate computed over 3 of 6 brands is not the corpus result. Report it and fail the run.
    missing = len(CORPUS) - n_loaded
    if missing:
        print(f"\n[INCOMPLETE] measured {n_loaded}/{len(CORPUS)} corpus profiles — {missing} "
              f"missing on disk. The rate above is NOT over the full corpus; re-run with all files.")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
