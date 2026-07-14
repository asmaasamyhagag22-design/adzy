"""Vertex AI spend guard — meters + caps the EXPENSIVE Google-model operations (Veo video and
Nano-Banana / gemini image generation), the ones that actually burn the credit.

Why this exists (2026-07-14): $228 of a $300 Vertex credit vanished in ~4 days with NO local
tracking — it was Veo reel renders (render3 alone rendered 3x) + image gen, all invisible. Text
calls are cents and are not metered here; Veo is dollars-per-render and is the real cost.

Model, mirroring social_intel/spend_guard.py: we can't see dollars at call time, so we budget a
conservative ESTIMATE (video seconds x rate, images x per-model rate) against a local JSONL ledger,
enforce a monthly cap, and log every render. The AUTHORITATIVE number is always Cloud Billing; these
estimates only power the guard + a running mirror so nothing burns invisibly again.

Rates + cap are env-overridable:
  VERTEX_MONTHLY_CAP_USD (default 50)   — hard monthly ceiling on metered renders
  VEO_USD_PER_SEC        (default 0.40) — Veo video, per generated second
  PRO_IMAGE_USD          (default 0.12) — gemini-3-pro-image (legible-text lane)
  NANO_IMAGE_USD         (default 0.04) — gemini-*-flash-image (Nano Banana, fast lane)
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

DEFAULT_LEDGER = Path("runs") / "vertex_spend.jsonl"   # runs/ is gitignored


class SpendRefused(RuntimeError):
    """Raised when a render would push metered monthly spend over the cap."""


def monthly_cap_usd() -> float:
    return float(os.getenv("VERTEX_MONTHLY_CAP_USD", "50"))


def _veo_usd_per_sec() -> float:
    return float(os.getenv("VEO_USD_PER_SEC", "0.40"))


def _image_usd(model: str) -> float:
    m = (model or "").lower()
    if "pro-image" in m:
        return float(os.getenv("PRO_IMAGE_USD", "0.12"))
    return float(os.getenv("NANO_IMAGE_USD", "0.04"))     # flash-image / Nano Banana default


def est_video_cost_usd(seconds: float) -> float:
    return round(max(0.0, float(seconds)) * _veo_usd_per_sec(), 4)


def est_image_cost_usd(n: int, model: str = "") -> float:
    return round(max(0, int(n)) * _image_usd(model), 4)


def month_key(dt: Optional[datetime] = None) -> str:
    d = dt or datetime.now(timezone.utc)
    return f"{d.year:04d}-{d.month:02d}"


def month_spend_usd(ledger: Optional[Path] = None, *, month: Optional[str] = None) -> float:
    """Sum of estimated spend recorded for the month. A corrupt line counts as 0, but never opens
    budget — callers add their estimate before comparing to the cap."""
    ledger = ledger or DEFAULT_LEDGER
    mk = month or month_key()
    total = 0.0
    if not ledger.is_file():
        return 0.0
    for line in ledger.read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(line)
            if row.get("month") == mk:
                total += float(row.get("est_cost_usd") or 0.0)
        except Exception:  # noqa: BLE001
            continue
    return round(total, 4)


def check_budget(est_cost_usd: float, *, ledger: Optional[Path] = None,
                 cap: Optional[float] = None, month: Optional[str] = None) -> tuple[bool, str]:
    """(ok, message). ok=False means the render must NOT run."""
    cap_usd = cap if cap is not None else monthly_cap_usd()
    est = round(max(0.0, float(est_cost_usd)), 4)
    spent = month_spend_usd(ledger, month=month)
    if spent + est > cap_usd:
        return False, (f"REFUSED: est ${est:.2f} + ${spent:.2f} already metered this month exceeds "
                       f"the ${cap_usd:.2f}/month Vertex cap (raise VERTEX_MONTHLY_CAP_USD to proceed)")
    return True, f"ok: est ${est:.2f} + ${spent:.2f} metered <= ${cap_usd:.2f}/month cap"


def record(kind: str, units: float, est_cost_usd: float, *, model: str = "", detail: str = "",
           ledger: Optional[Path] = None, when: Optional[datetime] = None) -> dict:
    """Append a render event to the ledger (kind = 'veo' | 'image')."""
    ledger = ledger or DEFAULT_LEDGER
    d = when or datetime.now(timezone.utc)
    row = {"ts": d.isoformat(), "month": month_key(d), "kind": kind, "units": units,
           "model": model, "detail": detail, "est_cost_usd": round(float(est_cost_usd), 4)}
    ledger.parent.mkdir(parents=True, exist_ok=True)
    with ledger.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")
    return row


def guard_video(seconds: float, *, detail: str = "", model: str = "",
                ledger: Optional[Path] = None) -> float:
    """Pre-flight a Veo render: raise SpendRefused if it would breach the cap, else return the
    estimate (the caller records() it after a successful render)."""
    est = est_video_cost_usd(seconds)
    ok, msg = check_budget(est, ledger=ledger)
    if not ok:
        raise SpendRefused(msg)
    return est


def guard_image(n: int, model: str = "", *, detail: str = "",
                ledger: Optional[Path] = None) -> float:
    est = est_image_cost_usd(n, model)
    ok, msg = check_budget(est, ledger=ledger)
    if not ok:
        raise SpendRefused(msg)
    return est


def spend_summary(ledger: Optional[Path] = None, *, month: Optional[str] = None) -> dict:
    """This month's metered spend + a per-kind breakdown, for a human check."""
    ledger = ledger or DEFAULT_LEDGER
    mk = month or month_key()
    by_kind: dict[str, float] = {}
    n = 0
    if ledger.is_file():
        for line in ledger.read_text(encoding="utf-8").splitlines():
            try:
                row = json.loads(line)
            except Exception:  # noqa: BLE001
                continue
            if row.get("month") != mk:
                continue
            n += 1
            by_kind[row.get("kind", "?")] = round(
                by_kind.get(row.get("kind", "?"), 0.0) + float(row.get("est_cost_usd") or 0.0), 4)
    return {"month": mk, "events": n, "by_kind": by_kind,
            "total_usd": round(sum(by_kind.values()), 4), "cap_usd": monthly_cap_usd()}


if __name__ == "__main__":   # `python -m vertex_spend` — a quick human check of the running total
    import sys
    s = spend_summary()
    sys.stdout.write(
        f"Vertex metered spend {s['month']}: ${s['total_usd']:.2f} / ${s['cap_usd']:.2f} cap "
        f"({s['events']} renders) — {s['by_kind']}\n")
