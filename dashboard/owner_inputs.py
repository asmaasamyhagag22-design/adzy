# -*- coding: utf-8 -*-
"""أرقام المالك (owner ask 2026-07-20 "حط مكان يحطها فيه"): the human numbers §7 keeps asking
for — real profit margin, real daily capacity, monthly budget — typed ONCE in the vault, saved
per brand, and every governing number recomputes from them. File: outputs/<slug>_owner_inputs.json.
Validation is strict-but-forgiving: out-of-range values are DROPPED (never clamp silently into a
different meaning); absent file -> {} (priors apply, labeled as priors)."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

_FIELDS = ("profit_margin_pct", "capacity_per_day", "monthly_budget")


def _san(data: dict) -> dict:
    out: dict = {}
    try:
        v = float(data.get("profit_margin_pct"))
        if 1 <= v <= 95:
            out["profit_margin_pct"] = round(v, 1)
    except (TypeError, ValueError):
        pass
    try:
        v = int(float(data.get("capacity_per_day")))
        if 1 <= v <= 100000:
            out["capacity_per_day"] = v
    except (TypeError, ValueError):
        pass
    try:
        v = float(data.get("monthly_budget"))
        if v > 0:
            out["monthly_budget"] = round(v, 2)
    except (TypeError, ValueError):
        pass
    return out


def load_owner_inputs(slug: str, out_dir: str = "outputs") -> dict:
    p = Path(out_dir) / f"{slug}_owner_inputs.json"
    if not p.is_file():
        return {}
    try:
        return _san(json.loads(p.read_text(encoding="utf-8")))
    except Exception:  # noqa: BLE001 — a corrupt file means priors, never a crash
        return {}


def save_owner_inputs(slug: str, data: dict, out_dir: str = "outputs") -> dict:
    """Merge-save (a field she doesn't type stays as-is). Returns the saved dict."""
    current = load_owner_inputs(slug, out_dir)
    incoming = _san(data or {})
    for k in _FIELDS:                       # explicit empty string clears the field
        if str((data or {}).get(k, None)) == "":
            current.pop(k, None)
    current.update(incoming)
    current["updated_at"] = datetime.now(timezone.utc).isoformat()
    p = Path(out_dir) / f"{slug}_owner_inputs.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(current, ensure_ascii=False, indent=2), encoding="utf-8")
    return current


def margin_fraction(inputs: dict) -> Optional[float]:
    v = inputs.get("profit_margin_pct")
    return (float(v) / 100.0) if isinstance(v, (int, float)) else None
