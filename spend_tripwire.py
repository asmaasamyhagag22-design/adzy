"""Spend tripwire — a RULE, not a warning.

Some entrypoints in this repo cost REAL MONEY on every run:

  * `python -m benchmark.grade_u1` — one LIVE Gemini call per labelled URL
    (`benchmark/grade_u1.py` → `media_plan/builder.py:build_campaign_objective`). Its own
    `_load_env()` loads the repo-root `.env`, so it has credentials even when the caller's shell
    does not.
  * `python -m trends` — LIVE **paid** Serper SERP calls (`trends/sources.py:SerperTrendSource`,
    first in `default_trend_sources`). `trends/sources.py`'s "FREE, keyless" docstring predates
    Serper and is wrong.

WHY THIS MODULE EXISTS (2026-07-15): an agent ran `grade_u1` believing it was offline — the
docstring says otherwise on line 9 — and then propagated that false premise into a fan-out brief,
so ~5 subagents re-ran it: ~72 billed calls, ~$0.59 (`logs/spend.md`). Nothing in the code
objected. The lesson is that a comment is not a control: the refusal has to be mechanical.

`refuse_if_no_spend()` is called by the paid `__main__` entrypoints ONLY. Library imports never
touch it, so injecting a fake caller (the correct way to test these paths) is unaffected —
see `tests/test_trends.py`, which drives the trend engine with fake sources and no network.
"""
from __future__ import annotations

import os


def spend_is_forbidden() -> tuple[bool, str]:
    """(forbidden, why). Test context is detected automatically — pytest sets PYTEST_CURRENT_TEST
    for every test, so a paid entrypoint can never be invoked from the suite by accident."""
    if os.environ.get("BASEERA_NO_SPEND") == "1":
        return True, "BASEERA_NO_SPEND=1 is set"
    if os.environ.get("PYTEST_CURRENT_TEST"):
        return True, "it is running inside pytest (PYTEST_CURRENT_TEST is set)"
    return False, ""


def refuse_if_no_spend(what: str, *, alternative: str = "") -> None:
    """Raise SystemExit when real spend is forbidden. `what` names the cost concretely, e.g.
    "one live Gemini call per labelled URL" — the operator must see WHAT they are buying."""
    forbidden, why = spend_is_forbidden()
    if not forbidden:
        return
    msg = (f"REFUSED — this entrypoint SPENDS REAL MONEY ({what}).\n"
           f"Blocked because {why}.")
    if alternative:
        msg += f"\n{alternative}"
    raise SystemExit(msg)
