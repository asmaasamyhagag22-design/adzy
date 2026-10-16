# -*- coding: utf-8 -*-
"""Measure what the shared claim EXTRACTOR catches — the gate's intake, not its verdict.

`EvidenceLedger.resolve_claim` can only judge a claim `extract_claims` produced. A hard claim the
extractor never yields is not "unsourced"; it is invisible. It ships having been checked by
nothing. So the number that matters here is EXTRACTION RECALL, measured separately on:

  DIRECT   — the proposition is carried by a word already in the closed lexicon, or by a digit.
  HELD-OUT — the SAME proposition, worded without any lexicon member or digit.

The held-out split is the whole point. Swapping one listed keyword for another listed keyword
measures the list against itself. These are ordinary paraphrases, and the gap between the two
splits is the size of the semantic blind spot.

Run:  python -m grounding.measure_claim_recall
      python -m grounding.measure_claim_recall --false-negatives

Pure local computation. No model, no network, no spend.
"""
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path
from typing import Any

from grounding.ledger import extract_claims

CORPUS = Path(__file__).with_name("claim_recall_corpus.json")


def load_cases(path: Path = CORPUS) -> list[dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return list(data.get("cases") or [])


def evaluate(cases: list[dict[str, Any]]) -> dict[str, Any]:
    """Run the extractor over every case. A case is DETECTED when >=1 claim comes back."""
    rows = []
    for case in cases:
        claims = extract_claims(case["text"])
        rows.append({
            **case,
            "detected": bool(claims),
            "kinds": sorted({c.kind for c in claims}),
        })
    return {"rows": rows}


def _rate(hit: int, total: int) -> float:
    return (hit / total * 100.0) if total else 0.0


def summarise(rows: list[dict[str, Any]]) -> dict[str, Any]:
    hard = [r for r in rows if r["hard_claim"]]
    soft = [r for r in rows if not r["hard_claim"]]

    def recall(subset):
        return _rate(sum(1 for r in subset if r["detected"]), len(subset))

    direct = [r for r in hard if r["split"] == "direct"]
    held = [r for r in hard if r["split"] == "held_out"]

    by_category: dict[str, dict] = defaultdict(lambda: {"direct": [], "held_out": []})
    for r in hard:
        by_category[r["category"]][r["split"]].append(r)

    return {
        "n_cases": len(rows),
        "n_hard": len(hard),
        "n_soft": len(soft),
        "overall_recall": recall(hard),
        "direct_recall": recall(direct),
        "held_out_recall": recall(held),
        "held_out_escape": 100.0 - recall(held),
        "overall_escape": 100.0 - recall(hard),
        # A non-claim that yields a claim is a FALSE POSITIVE: the gate would demand evidence for
        # a sentence that asserts nothing, and honest copy would be blanked.
        "false_positive_rate": _rate(sum(1 for r in soft if r["detected"]), len(soft)),
        "n_direct": len(direct),
        "n_held_out": len(held),
        "by_category": {
            cat: {
                "direct": f"{recall(v['direct']):.1f}% ({len(v['direct'])})" if v["direct"] else "-",
                "held_out": f"{recall(v['held_out']):.1f}% ({len(v['held_out'])})" if v["held_out"] else "-",
            }
            for cat, v in sorted(by_category.items())
        },
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--corpus", default=str(CORPUS))
    ap.add_argument("--false-negatives", action="store_true",
                    help="list every hard claim the extractor missed")
    ap.add_argument("--json", action="store_true", help="machine-readable summary")
    args = ap.parse_args()

    rows = evaluate(load_cases(Path(args.corpus)))["rows"]
    s = summarise(rows)

    if args.json:
        print(json.dumps(s, ensure_ascii=False, indent=2))
        return 0

    print("=" * 72)
    print("CLAIM EXTRACTION RECALL — grounding.ledger.extract_claims")
    print("=" * 72)
    print(f"cases: {s['n_cases']}   hard claims: {s['n_hard']}   non-claims: {s['n_soft']}")
    print()
    print(f"  overall recall      {s['overall_recall']:6.1f} %   (escape {s['overall_escape']:.1f} %)")
    print(f"  DIRECT  recall      {s['direct_recall']:6.1f} %   (n={s['n_direct']})")
    print(f"  HELD-OUT recall     {s['held_out_recall']:6.1f} %   (n={s['n_held_out']})")
    print(f"  HELD-OUT escape     {s['held_out_escape']:6.1f} %   <-- the semantic blind spot")
    print(f"  false positives     {s['false_positive_rate']:6.1f} %   (non-claims flagged as claims)")
    print()
    print(f"{'category':<26} {'direct':>16} {'held-out':>16}")
    print("-" * 60)
    for cat, v in s["by_category"].items():
        print(f"{cat:<26} {v['direct']:>16} {v['held_out']:>16}")

    if args.false_negatives:
        print()
        print("MISSED HARD CLAIMS (extractor returned nothing to audit)")
        print("-" * 72)
        for r in rows:
            if r["hard_claim"] and not r["detected"]:
                print(f"  [{r['split']:<8}] {r['category']:<22} {r['text']}")
                print(f"             proposition: {r['proposition']}")
        print()
        print("FALSE POSITIVES (non-claims the extractor flagged)")
        print("-" * 72)
        fp = [r for r in rows if not r["hard_claim"] and r["detected"]]
        for r in fp:
            print(f"  {r['text']}   -> kinds={r['kinds']}")
        if not fp:
            print("  none")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
