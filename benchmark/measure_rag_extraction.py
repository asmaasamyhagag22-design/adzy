"""Pillar 2 / Step 4 — LIVE before/after of RAG extraction on one manifest.

Runs the REAL pipeline twice on the SAME manifest with the SAME Gemini caller —
once with today's 200-cap pack (use_rag=False) and once with semantic retrieval
over the full pack (use_rag=True) — and prints what the LLM now sees that the
count cap used to drop (offerings / value-props / insights / trust).

COSTS MONEY (Gemini extraction x2 + embeddings once, on GCP credits) — out of CI.
HONEST: one stochastic pass each — directional, not a stable rate.

Usage:
    python -m benchmark.measure_rag_extraction [path/to/manifest.json]
    (default: the freshest te.eg manifest — the spec's named case)
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

# Windows console is cp1252; the profile carries Arabic. Force UTF-8 so prints
# (and the saved sidecar) don't crash on non-Latin output. (Same fix as poster/__main__.)
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:
        pass

from dotenv import load_dotenv

# Entry-point loads .env (the embeddings/caller helpers stay side-effect free).
load_dotenv()

from business_profile.build import build_profile  # noqa: E402
from business_profile.llm.caller import default_caller, format_cost  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
_DEFAULT = REPO / "scrapes" / "te_eg_20260628_085013" / "manifest.json"


def _vals(items) -> list[str]:
    """Display strings for a list of EvidencedField[str]."""
    out = []
    for it in items:
        v = getattr(it, "value", it)
        if v:
            out.append(str(v))
    return out


def _summary(res) -> dict:
    p = res.profile
    return {
        "pack_blocks": res.pack.block_count,
        "rejections": res.payload.diagnostics.total_rejections if res.payload else None,
        "cost": res.llm_result.total_cost_usd if res.llm_result else 0.0,
        "offerings": [o.name for o in p.offerings],
        "value_props": _vals(p.value_propositions),
        "insights": _vals(p.other_unique_insights),
        "trust": _vals(p.trust_signals),
        "audience": _vals(p.audience_signals),
    }


def _print_block(label: str, s: dict) -> None:
    print(f"\n===== {label} =====")
    print(f"pack blocks seen: {s['pack_blocks']}   validation rejections: {s['rejections']}"
          f"   extraction cost: {format_cost(s['cost'])}")
    for key in ("offerings", "value_props", "insights", "trust", "audience"):
        items = s[key]
        print(f"\n  {key} ({len(items)}):")
        for it in items:
            print(f"    - {it}")


def _diff(name: str, before: list[str], after: list[str]) -> None:
    b = {x.strip().lower() for x in before}
    gained = [x for x in after if x.strip().lower() not in b]
    a = {x.strip().lower() for x in after}
    lost = [x for x in before if x.strip().lower() not in a]
    print(f"\n  {name}: {len(before)} -> {len(after)}"
          f"   (+{len(gained)} gained, -{len(lost)} lost)")
    for g in gained:
        print(f"      + {g}")
    for l in lost:
        print(f"      - {l}")


def main(argv: list[str]) -> int:
    manifest = Path(argv[0]) if argv else _DEFAULT
    if not manifest.exists():
        print(f"Manifest not found: {manifest}")
        return 1
    caller = default_caller(strong=False)  # Gemini Flash
    if caller is None:
        print("No Gemini caller available (need google-genai + GOOGLE_CLOUD_PROJECT/ADC "
              "or a Gemini API key). Cannot run the live measurement.")
        return 1

    print(f"Manifest: {manifest}")
    print(f"Caller model: {getattr(caller, 'model', '?')}")

    print("\n[1/2] Extracting WITHOUT RAG (today's 200-cap pack) ...")
    base = build_profile(manifest, caller, use_rag=False, return_details=True)
    s_base = _summary(base)

    print("[2/2] Extracting WITH RAG (semantic retrieval over the full pack) ...")
    rag = build_profile(manifest, caller, use_rag=True, return_details=True)
    s_rag = _summary(rag)

    # Save a sidecar first so the data survives even if console printing chokes.
    out = REPO / "benchmark" / "rag_extraction_result.json"
    out.write_text(json.dumps({"manifest": str(manifest), "before": s_base,
                               "after": s_rag}, ensure_ascii=False, indent=2),
                   encoding="utf-8")
    print(f"\nSaved: {out}")

    _print_block("BEFORE — no RAG", s_base)
    _print_block("AFTER — RAG", s_rag)

    print("\n\n##### DIFF (RAG vs baseline) #####")
    _diff("offerings", s_base["offerings"], s_rag["offerings"])
    _diff("value_props", s_base["value_props"], s_rag["value_props"])
    _diff("insights", s_base["insights"], s_rag["insights"])
    _diff("trust", s_base["trust"], s_rag["trust"])
    _diff("audience", s_base["audience"], s_rag["audience"])
    print(f"\npack: {s_base['pack_blocks']} (capped) -> {s_rag['pack_blocks']} (full)")
    costs = (s_base["cost"], s_rag["cost"])
    total = sum(costs) if None not in costs else None
    print(f"total extraction cost: {format_cost(total)} (2 runs)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
