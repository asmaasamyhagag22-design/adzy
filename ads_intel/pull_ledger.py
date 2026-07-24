# -*- coding: utf-8 -*-
"""ش1 — the Meta pulls ledger (the spend_guard shape for a FREE but rate-limited API): check the
monthly CALL cap BEFORE every fetch, record AFTER. FAIL-CLOSED on any corrupt ledger line — a
ledger we can't read is a cap we can't enforce, so the pull is refused."""
from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Tuple

DEFAULT_LEDGER = "runs/meta_pulls.jsonl"


def meta_monthly_call_cap() -> int:
    return int(os.getenv("META_MONTHLY_CALL_CAP", "500"))


def month_key(dt: Optional[datetime] = None) -> str:
    dt = dt or datetime.now(timezone.utc)
    return f"{dt.year:04d}-{dt.month:02d}"


def _month_calls(ledger: str, month: str) -> Tuple[int, int]:
    """(calls_this_month, corrupt_lines). Missing ledger = (0, 0)."""
    p = Path(ledger)
    if not p.is_file():
        return 0, 0
    calls = corrupt = 0
    for line in p.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
            if str(row.get("month")) == month:
                calls += 1
        except Exception:  # noqa: BLE001
            corrupt += 1
    return calls, corrupt


def check_calls(planned: int, *, ledger: str = DEFAULT_LEDGER,
                monthly_cap: Optional[int] = None, month: Optional[str] = None) -> Tuple[bool, str]:
    cap = monthly_cap if monthly_cap is not None else meta_monthly_call_cap()
    mk = month or month_key()
    calls, corrupt = _month_calls(ledger, mk)
    if corrupt:
        return False, f"ledger has {corrupt} corrupt line(s) — refusing (fail-closed)"
    if calls + planned > cap:
        return False, f"monthly Meta call cap reached ({calls}+{planned} > {cap})"
    return True, ""


def record_call(url: str, n_results: int, *, ledger: str = DEFAULT_LEDGER) -> dict:
    row = {"ts": time.time(), "month": month_key(), "n_results": int(n_results),
           "url": str(url).split("access_token=")[0]}      # never persist the token
    p = Path(ledger)
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")
    return row
