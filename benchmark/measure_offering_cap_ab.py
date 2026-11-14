"""LIVE A/B: does raising the offerings cap surface MORE REAL offerings, or noise?

For each giant (>3x12 offering-page blocks) across different verticals, run the
RAG offerings group at cap=12 vs cap=30 (same retrieved pack, same Gemini caller)
and print the offerings at each cap so the extra ones can be judged real-vs-padding.

COSTS MONEY (Gemini Flash; ~1 embed batch + 2 extraction calls per site) — out of CI.
Usage: python -m benchmark.measure_offering_cap_ab [slug ...]
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:
        pass

from dotenv import load_dotenv
load_dotenv()

from scraper.schemas import ScrapeManifest  # noqa: E402
from business_profile.rules.orchestrator import apply_rules  # noqa: E402
from business_profile.llm.caller import default_caller, format_cost  # noqa: E402
from business_profile.llm.embeddings import embed_texts  # noqa: E402
from business_profile.llm.evidence_pack import build_evidence_pack  # noqa: E402
from business_profile.llm.prompts import SYSTEM_PROMPT, build_offerings_prompt  # noqa: E402
from business_profile.llm.rag import (  # noqa: E402
    DEFAULT_K, RagRetriever, build_full_evidence_pack, default_query_for,
)
from business_profile.llm.responses import OfferingsResponse  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
SCRAPES = REPO / "scrapes"
_TS_RE = re.compile(r"_\d{8}_\d{6}$")
CAPS = [12, 30]
DEFAULT_SLUGS = ["te_eg", "alfouadpharmacies_com", "almentor_net"]


def _freshest(slug: str):
    cands = sorted(p for p in SCRAPES.glob(f"{slug}_*/manifest.json"))
    return cands[-1] if cands else None


def _category(prof):
    cv = prof.category.value
    return cv.value if cv is not None and hasattr(cv, "value") else None


def main(argv) -> int:
    slugs = argv or DEFAULT_SLUGS
    caller = default_caller(strong=False)
    if caller is None:
        print("No Gemini caller (need google-genai + GOOGLE_CLOUD_PROJECT/ADC).")
        return 1
    print(f"caller: {getattr(caller, 'model', '?')}  caps: {CAPS}\n")

    report = {}
    for slug in slugs:
        mp = _freshest(slug)
        if mp is None:
            print(f"[{slug}] no manifest — skipped")
            continue
        m = ScrapeManifest.model_validate_json(mp.read_text(encoding="utf-8"))
        prof = apply_rules(m, manifest_path=str(mp))
        cat = _category(prof)
        full = build_full_evidence_pack(m, prof)
        retr = RagRetriever(full, build_evidence_pack(m, prof), embed_fn=embed_texts)
        off_pack = retr.retrieve(default_query_for("offerings"), DEFAULT_K)

        print(f"\n################ {slug}  (category={cat}, full={full.block_count} blocks) ################")
        report[slug] = {"category": cat}
        for cap in CAPS:
            prompt = build_offerings_prompt(off_pack, cat, max_offerings=cap)
            resp, usage = caller(SYSTEM_PROMPT, prompt, OfferingsResponse,
                                 group_name="offerings")
            names = [o.name for o in resp.offerings]
            report[slug][f"cap_{cap}"] = names
            print(f"\n  --- cap={cap}: {len(names)} offerings ({format_cost(usage.cost_usd)}) ---")
            for n in names:
                print(f"     • {n}")

    out = REPO / "benchmark" / "offering_cap_ab_result.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nSaved: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
