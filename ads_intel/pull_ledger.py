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


_UNLIMITED = float("inf")


def _cap_from_env(var: str) -> float:
    """A cap from the environment, or UNLIMITED when unset.

    OWNER RULING (2026-07-25): «شيل اي حدود ع اي ميزانيه». What prompted it was this very cap
    silently refusing the 501st Serper call of the month, which made every web-discovery feature
    look broken instead of paused — abuauf.com shipped "No Competitors Found", a claim about her
    market, when the truth was a fact about our meter.

    LIFTED IS NOT UNMETERED. `record_call` still writes every call and `_month_calls` still counts
    them; only the refusal is gone. Setting the env var to a number brings the ceiling back."""
    raw = str(os.getenv(var) or "").strip()
    if not raw:
        return _UNLIMITED
    try:
        return float(raw)
    except ValueError:
        return _UNLIMITED


def meta_monthly_call_cap() -> float:
    return _cap_from_env("META_MONTHLY_CALL_CAP")


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
                monthly_cap: Optional[float] = None, month: Optional[str] = None,
                service: str = "Meta") -> Tuple[bool, str]:
    """`service` NAMES THE METER THAT REFUSED. This function is shared by the Meta ad puller and by
    Serper, and the message hardcoded "Meta" — so an exhausted Serper budget reported itself as
    "monthly Meta call cap reached", which is what made the live abuauf.com failure take far longer
    to identify than it should have. A guard that misnames itself is worse than a silent one."""
    cap = monthly_cap if monthly_cap is not None else meta_monthly_call_cap()
    mk = month or month_key()
    calls, corrupt = _month_calls(ledger, mk)
    # UNCAPPED IS NOT UNGUARDED. A ledger we cannot read means spend cannot be accounted for at
    # all, and that still stops the run — this refusal is about integrity, not about budget, so
    # lifting the caps must not lift it.
    if corrupt:
        return False, f"ledger has {corrupt} corrupt line(s) — refusing (fail-closed)"
    if calls + planned > cap:
        return False, f"monthly {service} call cap reached ({calls}+{planned} > {cap})"
    return True, ""


def record_call(url: str, n_results: int, *, ledger: str = DEFAULT_LEDGER) -> dict:
    row = {"ts": time.time(), "month": month_key(), "n_results": int(n_results),
           "url": str(url).split("access_token=")[0]}      # never persist the token
    p = Path(ledger)
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")
    return row
