"""CSV import for real client performance exports (v2.2 §6-W6). Same `PerformanceSnapshot` schema as
the simulator, so imported data is judged by identical code. Stdlib csv only; no network."""
from __future__ import annotations

import csv
import io
from datetime import date
from pathlib import Path
from typing import List

from decision_engine.schemas import PerformanceSnapshot

# The documented column order a pilot client fills (also the export order).
FIELDS = ["campaign_ref", "ad_ref", "date", "spend", "impressions", "clicks",
          "ctr", "frequency", "conversions", "cpl"]


def _row_to_snapshot(row: dict) -> PerformanceSnapshot:
    def num(k, cast):
        v = (row.get(k) or "").strip()
        return cast(v) if v != "" else None
    return PerformanceSnapshot(
        campaign_ref=(row.get("campaign_ref") or "").strip(),
        ad_ref=(row.get("ad_ref") or "").strip(),
        date=date.fromisoformat((row.get("date") or "").strip()),
        spend=float(row.get("spend") or 0), impressions=int(row.get("impressions") or 0),
        clicks=int(row.get("clicks") or 0), ctr=float(row.get("ctr") or 0),
        frequency=float(row.get("frequency") or 0), conversions=int(row.get("conversions") or 0),
        cpl=num("cpl", float))


def load_snapshots_csv(text_or_path: "str | Path") -> List[PerformanceSnapshot]:
    """Parse CSV text (or a file path) into snapshots. Blank/whitespace rows are skipped."""
    raw = (Path(text_or_path).read_text(encoding="utf-8")
           if isinstance(text_or_path, Path) or (isinstance(text_or_path, str) and "\n" not in text_or_path
                                                  and Path(text_or_path).is_file())
           else str(text_or_path))
    out: List[PerformanceSnapshot] = []
    for row in csv.DictReader(io.StringIO(raw)):
        if not (row.get("date") or "").strip():
            continue
        out.append(_row_to_snapshot(row))
    return out


def dump_snapshots_csv(snapshots: List[PerformanceSnapshot]) -> str:
    """Serialize snapshots back to CSV text (the template + round-trip source)."""
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=FIELDS)
    w.writeheader()
    for s in snapshots:
        row = s.model_dump()
        row["date"] = s.date.isoformat()
        if row.get("cpl") is None:
            row["cpl"] = ""
        w.writerow(row)
    return buf.getvalue()
