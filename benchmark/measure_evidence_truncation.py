"""Pillar 2 / Step 0 — MEASURE evidence-pack truncation (READ-ONLY).

Establishes the baseline the RAG work must beat. Touches NOTHING in the live
pipeline: it replays the REAL extraction path on every saved manifest and
counts how many scraped text blocks the LLM extractor actually gets to read.

The real path (business_profile/build.py):
    manifest -> apply_rules(manifest)                      -> rules_profile
             -> build_evidence_pack(manifest, rules_profile)  (200-block cap)
             -> _format_pack_for_prompt(pack, max_chars=30_000)
                  == pack.as_llm_text(max_chars=30_000)       (30k-char cap)
    ... and that SAME capped+char-truncated pack feeds all 4 extraction groups
    (identity / offerings / positioning / trust).

So the blocks the LLM ever sees per scrape == the lines that fit in
`pack.as_llm_text(30_000)`. Everything past that is silently lost.

Four block populations per scrape:
    total     = blocks_total_in_manifest          (everything scraped)
    filtered  = blocks_after_filter               (real content; nav/boilerplate/dupes removed)
    pack200   = block_count                        (survives the 200-block + 60/page-type cap)
    fit30k    = lines in as_llm_text(30_000)        (survives the 30k-char cap -> the LLM sees these)

The meaningful loss is filtered -> fit30k: real content blocks that exist but
never reach the model. (total -> filtered is legitimate noise removal.)

Usage:
    python -m benchmark.measure_evidence_truncation
    python -m benchmark.measure_evidence_truncation --all   # every manifest, not just freshest-per-site
"""
from __future__ import annotations

import logging
import re
import sys
from pathlib import Path

from scraper.schemas import ScrapeManifest
from business_profile.rules.orchestrator import apply_rules
from business_profile.llm.evidence_pack import build_evidence_pack

logging.disable(logging.CRITICAL)  # silence pipeline logging noise

REPO = Path(__file__).resolve().parent.parent
SCRAPES = REPO / "scrapes"
CHAR_CAP = 30_000  # _format_pack_for_prompt default

_TS_RE = re.compile(r"_\d{8}_\d{6}$")  # trailing _YYYYMMDD_HHMMSS


def _site_slug(dirname: str) -> str:
    """Strip the trailing timestamp so re-scrapes of one site collapse."""
    return _TS_RE.sub("", dirname)


def coverage_pct(fit30k: int, filtered: int) -> float:
    """% of filtered blocks that fit the char budget. CS-67: an EMPTY scrape (filtered == 0) has NO
    content, so it must NOT score a perfect 100% ('everything fit' is vacuously true but reads as a
    fully-covered brand — the P-43 marasim empty scrape got a perfect grade). 0/0 -> 0.0."""
    return (fit30k / filtered * 100.0) if filtered else 0.0


def _fit_count(pack, max_chars: int) -> int:
    """Number of blocks that survive the char cap (== lines the LLM reads)."""
    txt = pack.as_llm_text(max_chars=max_chars)
    if not txt:
        return 0
    return len([ln for ln in txt.split("\n") if ln])


def measure_one(manifest_path: Path) -> dict | None:
    try:
        raw = manifest_path.read_text(encoding="utf-8")
        manifest = ScrapeManifest.model_validate_json(raw)
        rules_profile = apply_rules(manifest, manifest_path=str(manifest_path))
        pack = build_evidence_pack(manifest, rules_profile)
    except Exception as exc:  # noqa: BLE001 - a bad manifest must not kill the run
        return {"error": f"{type(exc).__name__}: {exc}"}

    total = pack.blocks_total_in_manifest
    filtered = pack.blocks_after_filter
    pack200 = pack.block_count
    fit30k = _fit_count(pack, CHAR_CAP)

    # which cap actually binds (drops the most relevant content)?
    drop_200 = filtered - pack200      # lost to the 200-block / 60-per-type cap
    drop_30k = pack200 - fit30k        # lost to the 30k-char cap
    if not filtered:
        bind = "empty"                 # CS-67: nothing captured — not "none" (which reads as clean)
    elif drop_200 <= 0 and drop_30k <= 0:
        bind = "none"
    elif drop_30k >= drop_200:
        bind = "30k-char"
    else:
        bind = "200-block"

    seen_pct = coverage_pct(fit30k, filtered)
    return {
        "total": total,
        "filtered": filtered,
        "pack200": pack200,
        "fit30k": fit30k,
        "lost_of_filtered": filtered - fit30k,
        "seen_pct": seen_pct,
        "lost_pct": 100.0 - seen_pct,
        "bind": bind,
        "empty": filtered == 0,
    }


def main(argv: list[str]) -> int:
    show_all = "--all" in argv
    manifests = sorted(SCRAPES.glob("*/manifest.json"))
    if not manifests:
        print(f"No manifests found under {SCRAPES}")
        return 1

    rows: list[tuple[str, str, dict]] = []  # (slug, dirname, metrics)
    for mp in manifests:
        dirname = mp.parent.name
        rows.append((_site_slug(dirname), dirname, measure_one(mp)))

    # freshest-per-site (dirnames sort lexicographically == chronologically here)
    if not show_all:
        freshest: dict[str, tuple[str, str, dict]] = {}
        for slug, dirname, m in rows:
            freshest[slug] = (slug, dirname, m)  # later wins (sorted asc)
        rows = list(freshest.values())

    ok = [(s, d, m) for s, d, m in rows if "error" not in m]
    errs = [(s, d, m) for s, d, m in rows if "error" in m]
    ok.sort(key=lambda r: r[2]["lost_of_filtered"], reverse=True)

    label = "ALL manifests" if show_all else "freshest manifest per site"
    print(f"\nEvidence-pack truncation — {label} ({len(ok)} measured, {len(errs)} errored)\n")
    hdr = f"{'site':<34}{'total':>7}{'filt':>7}{'pack':>6}{'LLM':>6}{'seen%':>7}{'lost':>6}  bind"
    print(hdr)
    print("-" * len(hdr))
    for slug, _dir, m in ok:
        print(f"{slug:<34}{m['total']:>7}{m['filtered']:>7}{m['pack200']:>6}"
              f"{m['fit30k']:>6}{m['seen_pct']:>6.0f}%{m['lost_of_filtered']:>6}  {m['bind']}")

    # corpus aggregates
    if ok:
        n = len(ok)
        tot_filtered = sum(m["filtered"] for _, _, m in ok)
        tot_seen = sum(m["fit30k"] for _, _, m in ok)
        seen_pcts = sorted(m["seen_pct"] for _, _, m in ok)
        median = seen_pcts[n // 2]
        lose_any = sum(1 for _, _, m in ok if m["lost_of_filtered"] > 0)
        lose_25 = sum(1 for _, _, m in ok if m["lost_pct"] >= 25)
        lose_50 = sum(1 for _, _, m in ok if m["lost_pct"] >= 50)
        binds: dict[str, int] = {}
        for _, _, m in ok:
            binds[m["bind"]] = binds.get(m["bind"], 0) + 1
        print("\n--- corpus ---")
        print(f"sites measured:               {n}")
        print(f"relevant blocks (post-filter):{tot_filtered:>8}")
        print(f"blocks the LLM actually sees: {tot_seen:>8}  "
              f"({tot_seen / tot_filtered * 100:.1f}% of relevant content)")
        print(f"median per-site coverage:     {median:.0f}%")
        print(f"sites losing >0 / >=25% / >=50% relevant: {lose_any} / {lose_25} / {lose_50}")
        print(f"binding cap when content is lost: "
              + ", ".join(f"{k}={v}" for k, v in sorted(binds.items())))
    if errs:
        print("\nerrored manifests:")
        for slug, _dir, m in errs:
            print(f"  {slug}: {m['error']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
