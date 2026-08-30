"""Apify spend guard — enforces the owner's monthly cap ($20/month, on record 2026-07-12).

Apify bills per Actor result; we cannot see dollars at call time, so the guard budgets an
ESTIMATE (results x APIFY_COST_PER_1K_RESULTS, conservative default $5/1k) against a local
JSONL ledger. Pure logic — the only network path stays scripts/pull_facebook.py (PD-4)."""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

DEFAULT_LEDGER = Path("runs") / "apify_spend.jsonl"   # runs/ is gitignored


def monthly_cap_usd() -> float:
    """UNLIMITED unless `APIFY_MONTHLY_CAP_USD` says otherwise — owner ruling 2026-07-25,
    «شيل اي حدود ع اي ميزانيه». Spend is still recorded and still costed; only the refusal is gone."""
    raw = str(os.getenv("APIFY_MONTHLY_CAP_USD") or "").strip()
    if not raw:
        return float("inf")
    try:
        return float(raw)
    except ValueError:
        return float("inf")


def cost_per_1k_usd() -> float:
    return float(os.getenv("APIFY_COST_PER_1K_RESULTS", "5"))


def est_cost_usd(n_results: int, *, cost_per_1k: Optional[float] = None) -> float:
    rate = cost_per_1k if cost_per_1k is not None else cost_per_1k_usd()
    return round(max(0, int(n_results)) * rate / 1000.0, 4)


def month_key(dt: Optional[datetime] = None) -> str:
    d = dt or datetime.now(timezone.utc)
    return f"{d.year:04d}-{d.month:02d}"


def _scan(ledger: Path, mk: str) -> tuple[float, int]:
    """(total_for_month, corrupt_lines). An unreadable line is COUNTED, never silently dropped —
    it may be a truncated pull record whose cost would vanish with it."""
    total = 0.0
    corrupt = 0
    if not ledger.is_file():
        return 0.0, 0
    for line in ledger.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if not s:
            continue
        try:
            row = json.loads(s)
        except Exception:  # noqa: BLE001
            corrupt += 1
            continue
        if not isinstance(row, dict):
            corrupt += 1
            continue
        if row.get("month") == mk:
            total += float(row.get("est_cost_usd") or 0.0)
    return round(total, 4), corrupt


def month_spend_usd(ledger: Path = DEFAULT_LEDGER, *, month: Optional[str] = None) -> float:
    """Sum of the READABLE spend rows for the month. Unreadable lines are excluded here and
    refused outright by `check_budget` (see `corrupt_line_count`) — the old docstring claimed a
    corrupt ledger "never silently OPENS budget because callers add before comparing", which is
    false: the caller only adds ITS OWN estimate and never recovers the dropped one."""
    return _scan(ledger, month or month_key())[0]


def corrupt_line_count(ledger: Path = DEFAULT_LEDGER, *, month: Optional[str] = None) -> int:
    """Unreadable lines. >0 means the month total is UNKNOWABLE, not merely lower."""
    return _scan(ledger, month or month_key())[1]


def check_budget(planned_results: int, *, ledger: Path = DEFAULT_LEDGER,
                 cap: Optional[float] = None, cost_per_1k: Optional[float] = None,
                 month: Optional[str] = None) -> tuple[bool, str]:
    """(ok, message). ok=False means the pull must NOT run."""
    cap_usd = cap if cap is not None else monthly_cap_usd()
    est = est_cost_usd(planned_results, cost_per_1k=cost_per_1k)
    spent, corrupt = _scan(ledger, month or month_key())
    # FAIL-CLOSED (P6 #6): an unreadable line makes the month total unknowable, not just smaller.
    # A killed pull appends a truncated record; skipping it erases its cost and re-opens the cap.
    if corrupt:
        return False, (f"REFUSED: {corrupt} unreadable line(s) in {ledger} — the month total is "
                       f"unknowable (a truncated pull record would silently re-open the cap). "
                       f"Fix or remove the bad line(s), then re-run.")
    if spent + est > cap_usd:
        return False, (f"REFUSED: est ${est:.2f} for {planned_results} results + "
                       f"${spent:.2f} already spent this month exceeds the owner's "
                       f"${cap_usd:.2f}/month cap")
    return True, (f"ok: est ${est:.2f} + ${spent:.2f} spent <= ${cap_usd:.2f}/month cap")


def record_pull(actual_results: int, *, url: str, ledger: Path = DEFAULT_LEDGER,
                cost_per_1k: Optional[float] = None, when: Optional[datetime] = None) -> dict:
    d = when or datetime.now(timezone.utc)
    row = {"ts": d.isoformat(), "month": month_key(d), "url": url,
           "results": int(actual_results),
           "est_cost_usd": est_cost_usd(actual_results, cost_per_1k=cost_per_1k)}
    ledger.parent.mkdir(parents=True, exist_ok=True)
    with ledger.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")
    return row
