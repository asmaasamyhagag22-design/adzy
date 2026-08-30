# -*- coding: utf-8 -*-
"""OWNER RULING (2026-07-25): «شيل اي حدود ع اي ميزانيه» — remove every budget cap.

WHAT PROMPTED IT. The Serper monthly cap (500) was fully consumed, and because the refusal was
silent every downstream feature looked broken instead of paused: abuauf.com came back with zero
competitors and no trends, and the dashboard reported "No Competitors Found" — a statement about
her market — when the truth was a statement about our meter.

WHAT THE RULING DOES AND DOES NOT CHANGE:

    caps      -> lifted on all four meters (Serper, Meta, Apify, Vertex/Veo)
    metering  -> UNCHANGED. Every call and every render is still recorded, still costed, still
                 reportable. A cap and a meter are different things and only the cap was removed.

I raised the one asymmetric risk before doing it — Veo bills $0.40/second and an uncapped renderer
is what produced the $228-in-four-days incident this project already lived through — and the owner
chose to lift that one too. Recorded here so the decision is in the code and not only in a chat.

DEFAULT = UNLIMITED, and the env var still works. Anyone who wants a ceiling back sets
`VERTEX_MONTHLY_CAP_USD` (or the matching var) to a number; absent means no limit, which is now the
deliberate default rather than an oversight.
"""
from __future__ import annotations

import os

import pytest


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for k in ("META_MONTHLY_CALL_CAP", "SERPER_MONTHLY_CALL_CAP",
              "APIFY_MONTHLY_CAP_USD", "VERTEX_MONTHLY_CAP_USD"):
        monkeypatch.delenv(k, raising=False)


# --- the four meters run uncapped by default ------------------------------------------------

def test_the_meta_pull_cap_is_lifted():
    from ads_intel.pull_ledger import meta_monthly_call_cap
    assert meta_monthly_call_cap() == float("inf")


def test_the_apify_cap_is_lifted():
    from social_intel.spend_guard import monthly_cap_usd
    assert monthly_cap_usd() == float("inf")


def test_the_vertex_cap_is_lifted():
    """Including Veo. The owner was shown the $0.40/second exposure and chose this."""
    import vertex_spend
    assert vertex_spend.monthly_cap_usd() == float("inf")


def test_a_search_is_no_longer_refused_for_budget(tmp_path, monkeypatch):
    """The live failure that started this: 500 recorded calls refusing the 501st."""
    from ads_intel.pull_ledger import check_calls, record_call
    led = str(tmp_path / "serper.jsonl")
    for i in range(600):
        record_call(f"serper:q{i}", 1, ledger=led)
    ok, msg = check_calls(1, ledger=led, monthly_cap=None, service="Serper")
    assert ok is True, msg


# --- an explicit ceiling still binds ----------------------------------------------------------

def test_an_explicit_env_cap_is_still_honoured(monkeypatch):
    """NO `importlib.reload` HERE, and that matters. The first version of this test reloaded
    `vertex_spend`, which swaps the module OBJECT — so `tests/test_vertex_spend.py`, which holds a
    module-level reference to it, then monkeypatched a stale object while the code under test used
    the fresh one. It passed alone and failed in the full suite: test pollution I introduced.

    The reload was never needed: `monthly_cap_usd` reads the environment at CALL time, which is
    also what makes the env var usable without a restart."""
    import vertex_spend
    monkeypatch.setenv("VERTEX_MONTHLY_CAP_USD", "75")
    assert vertex_spend.monthly_cap_usd() == 75.0


def test_an_explicit_call_cap_still_refuses(tmp_path):
    from ads_intel.pull_ledger import check_calls, record_call
    led = str(tmp_path / "m.jsonl")
    for i in range(5):
        record_call(f"x{i}", 1, ledger=led)
    ok, _msg = check_calls(1, ledger=led, monthly_cap=5)
    assert ok is False


# --- METERING SURVIVES. Lifting the cap must not stop the counting. -----------------------------

def test_spend_is_still_recorded_with_no_cap(tmp_path, monkeypatch):
    from ads_intel.pull_ledger import _month_calls, month_key, record_call
    led = str(tmp_path / "m.jsonl")
    for i in range(7):
        record_call(f"serper:q{i}", 1, ledger=led)
    calls, corrupt = _month_calls(led, month_key())
    assert calls == 7 and not corrupt, "the meter stopped counting when the cap was removed"


def test_a_corrupt_ledger_still_fails_closed(tmp_path):
    """Uncapped is not unguarded. An unreadable ledger means spend cannot be accounted for at all,
    and that still stops the run — the one refusal that is about integrity, not about budget."""
    from ads_intel.pull_ledger import check_calls
    led = tmp_path / "bad.jsonl"
    led.write_text("{not json\n", encoding="utf-8")
    ok, msg = check_calls(1, ledger=str(led), monthly_cap=None)
    assert ok is False and "corrupt" in msg
