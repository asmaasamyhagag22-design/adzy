"""Benchmark aggregation + report writers.

`aggregate` rolls per-URL grade sets up into overall / per-vertical / per-tier
numbers and runs the pass/fail threshold check. `write_results_json` and
`write_report_md` persist a run for later inspection (used by benchmark.runner).

A URL is "ready" when its SWOT-critical average is >= READY_SWOT_THRESHOLD. The
threshold check fails if any of the configured floors is breached:
  - min_ready_fraction               (overall fraction of ready URLs)
  - min_avg_swot_critical_score      (overall mean SWOT-critical score)
  - per_vertical_min_swot_critical_score  (each vertical's mean must clear this)
"""
from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import List, Optional

from .graders import UrlGradeSet


# A URL counts as "ready for strategy" when its SWOT-critical fields average at
# least this. Separate from the configurable pass/fail thresholds below.
READY_SWOT_THRESHOLD = 0.7


def _mean(values: List[Optional[float]]) -> Optional[float]:
    vals = [v for v in values if v is not None]
    if not vals:
        return None
    return sum(vals) / len(vals)


def _group_stats(per_url: List[dict], key: str) -> dict:
    """Group per-URL rows by `key` (vertical or tier) -> summary stats."""
    groups: dict[str, list] = {}
    for row in per_url:
        groups.setdefault(row[key], []).append(row)
    out = {}
    for name, rows in groups.items():
        total = len(rows)
        ready = sum(1 for r in rows if r["ready"])
        out[name] = {
            "total": total,
            "ready_count": ready,
            "ready_fraction": ready / total if total else 0.0,
            "avg_swot_critical": _mean([r["avg_swot"] for r in rows]),
            "avg_all": _mean([r["avg_all"] for r in rows]),
        }
    return out


def aggregate(grade_sets: List[UrlGradeSet], metadata: dict, thresholds: dict) -> dict:
    """Roll per-URL grades into overall + per-vertical + per-tier numbers and run
    the threshold check. `metadata` is keyed by url_id (each value carries at
    least 'vertical' and 'tier')."""
    per_url = []
    for gs in grade_sets:
        meta = metadata.get(gs.url_id, {}) or {}
        avg_swot = gs.avg_score(only_swot_critical=True)
        per_url.append({
            "url_id": gs.url_id,
            "vertical": meta.get("vertical", "unknown"),
            "tier": meta.get("tier", "unknown"),
            "avg_swot": avg_swot,
            "avg_all": gs.avg_score(),
            "ready": avg_swot is not None and avg_swot >= READY_SWOT_THRESHOLD,
        })

    total = len(per_url)
    ready_count = sum(1 for r in per_url if r["ready"])
    ready_fraction = ready_count / total if total else 0.0
    overall_avg_swot = _mean([r["avg_swot"] for r in per_url]) or 0.0

    # P-32: the average silently ignores SWOT-critical fields that had no ground-truth label
    # (score=None) — so "0.88" can be a mean over 4 of 7 fields. Carry the coverage so the report
    # can state the real denominator instead of implying all SWOT-critical fields were scored.
    swot_scored = sum(1 for gs in grade_sets for g in gs.grades
                      if g.swot_critical and g.score is not None)
    swot_total = sum(1 for gs in grade_sets for g in gs.grades if g.swot_critical)

    overall = {
        "total": total,
        "ready_count": ready_count,
        "ready_fraction": ready_fraction,
        "avg_swot_critical": overall_avg_swot,
        "avg_all": _mean([r["avg_all"] for r in per_url]),
        "swot_critical_scored": swot_scored,
        "swot_critical_total": swot_total,
    }
    by_vertical = _group_stats(per_url, "vertical")
    by_tier = _group_stats(per_url, "tier")

    # --- threshold check ---
    failures: List[str] = []
    min_ready = thresholds.get("min_ready_fraction", 0.0)
    if ready_fraction < min_ready:
        failures.append(f"overall ready fraction {ready_fraction:.2f} < {min_ready}")

    min_swot = thresholds.get("min_avg_swot_critical_score", 0.0)
    if overall_avg_swot < min_swot:
        failures.append(f"overall swot-critical avg {overall_avg_swot:.2f} < {min_swot}")

    floor = thresholds.get("per_vertical_min_swot_critical_score", 0.0)
    for vertical, stats in sorted(by_vertical.items()):
        avg = stats["avg_swot_critical"]
        if avg is not None and avg < floor:
            failures.append(
                f"vertical {vertical!r} swot-critical avg {avg:.2f} < floor {floor}"
            )

    return {
        "overall": overall,
        "by_vertical": by_vertical,
        "by_tier": by_tier,
        "per_url": per_url,
        "threshold_check": {"passed": not failures, "failures": failures},
    }


# ---------------------------------------------------------------------
# Persistence (used by benchmark.runner; not exercised by the unit tests)
# ---------------------------------------------------------------------

def write_results_json(grade_sets, aggregations, run_meta, path) -> None:
    """Write the full machine-readable run result to `path` (JSON)."""
    data = {
        "run": run_meta,
        "aggregations": aggregations,
        "urls": [
            {
                "url_id": gs.url_id,
                "avg_swot_critical": gs.avg_score(only_swot_critical=True),
                "avg_all": gs.avg_score(),
                "grades": [asdict(g) for g in gs.grades],
            }
            for gs in grade_sets
        ],
    }
    Path(path).write_text(
        json.dumps(data, indent=2, ensure_ascii=False, default=str), encoding="utf-8"
    )


def write_report_md(grade_sets, aggregations, run_meta, path) -> None:
    """Write a human-readable Markdown summary to `path`."""
    o = aggregations["overall"]
    tc = aggregations["threshold_check"]
    lines = [
        f"# Benchmark report — {run_meta.get('timestamp', '')}",
        "",
        f"- URLs graded: **{o['total']}**",
        f"- Ready: **{o['ready_count']}/{o['total']}** "
        f"({o['ready_fraction']:.0%})",
        f"- Avg SWOT-critical score: **{o['avg_swot_critical']:.2f}**"
        + (f" — averaged over **{o['swot_critical_scored']}/{o['swot_critical_total']}** scored "
           f"SWOT-critical fields (interpretive fields without ground-truth labels are excluded)"
           if o.get("swot_critical_total") else ""),
        f"- Threshold check: **{'PASS' if tc['passed'] else 'FAIL'}**",
        "",
    ]
    if tc["failures"]:
        lines.append("## Threshold failures")
        lines += [f"- {f}" for f in tc["failures"]]
        lines.append("")

    lines.append("## Per-vertical")
    lines.append("")
    lines.append("| vertical | ready | avg SWOT-critical |")
    lines.append("|---|---|---|")
    for vertical, s in sorted(aggregations["by_vertical"].items()):
        avg = s["avg_swot_critical"]
        avg_str = f"{avg:.2f}" if avg is not None else "—"
        lines.append(f"| {vertical} | {s['ready_count']}/{s['total']} | {avg_str} |")
    lines.append("")

    lines.append("## Per-URL")
    lines.append("")
    lines.append("| url | vertical | tier | avg SWOT-critical | avg all | ready |")
    lines.append("|---|---|---|---|---|---|")
    for r in aggregations["per_url"]:
        swot = f"{r['avg_swot']:.2f}" if r["avg_swot"] is not None else "—"
        allv = f"{r['avg_all']:.2f}" if r["avg_all"] is not None else "—"
        lines.append(
            f"| {r['url_id']} | {r['vertical']} | {r['tier']} | {swot} | {allv} | "
            f"{'yes' if r['ready'] else 'no'} |"
        )
    lines.append("")

    if run_meta.get("errors"):
        lines.append("## Errors")
        lines += [f"- **{uid}**: {msg}" for uid, msg in run_meta["errors"].items()]
        lines.append("")

    Path(path).write_text("\n".join(lines), encoding="utf-8")
