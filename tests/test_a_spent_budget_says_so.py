# -*- coding: utf-8 -*-
"""A spent budget must not look like an empty market.

MEASURED LIVE (2026-07-25, the owner's own server, abuauf.com):

    SerperProvider.search("مكسرات اون لاين مصر")  ->  0 hits
    last_error: "refused: monthly Meta call cap reached (500+1 > 500)"

The Serper monthly cap was fully consumed. Every web search therefore returned `[]`, and because
`SerpWebDiscoveryEngine` is the ONLY discovery lane for an ECOMMERCE brand, Abu Auf shipped a SWOT
reading "Standalone Strategic Analysis (No Competitors Found)". Trends went the same way — they
pay through the same chokepoint.

TWO SEPARATE FACTS, AND THE CLIENT WAS SHOWN THE WRONG ONE:

    "we searched the market and found no comparable business"     <- what the screen said
    "we stopped searching because this month's budget is spent"   <- what actually happened

The first is a finding about her market. The second is a fact about our meter. Presenting the
second as the first is the same failure class as every other defect found today — a true reason
exists, is computed, and never reaches the screen.

WHAT THIS FILE LOCKS:

  1. The refusal names the service that was actually capped. `check_calls` is shared by the Meta
     puller and by Serper, and its message hardcoded "Meta" — so a Serper exhaustion reported
     itself as a Meta problem, which is what made this take longer to find than it should have.
  2. `PeerMatchResult.notes` — where `route_discovery` already writes why a lane came back empty —
     reaches `swot.notes`, i.e. the box the client reads. The webapp path computed those notes and
     dropped them on the floor.

NOT IN SCOPE, DELIBERATELY: raising the cap. The cap is the owner's spend guard; only she moves it.
What is broken here is the silence, not the limit.

Hermetic: a stubbed discovery result, no network, no key.
"""
from __future__ import annotations

from ads_intel.pull_ledger import check_calls


# --- 1) the refusal names the right meter ---------------------------------------------------

def test_a_serper_refusal_does_not_blame_meta(tmp_path):
    led = tmp_path / "serper.jsonl"
    led.write_text("", encoding="utf-8")
    ok, msg = check_calls(1, ledger=str(led), monthly_cap=0, service="Serper")
    assert ok is False
    assert "Serper" in msg, msg
    assert "Meta" not in msg, f"a Serper exhaustion reported itself as a Meta problem: {msg}"


def test_the_meta_caller_still_reads_as_meta(tmp_path):
    led = tmp_path / "meta.jsonl"
    led.write_text("", encoding="utf-8")
    _ok, msg = check_calls(1, ledger=str(led), monthly_cap=0)
    assert "Meta" in msg, msg


def test_the_message_still_carries_the_numbers():
    """"Cap reached" with no numbers cannot be acted on. The owner needs to see how far over."""
    _ok, msg = check_calls(1, ledger="nonexistent.jsonl", monthly_cap=0, service="Serper")
    assert "0" in msg and ">" in msg, msg


# --- 2) the reason reaches the client -------------------------------------------------------

def test_discovery_notes_reach_the_swot(monkeypatch):
    """`route_discovery` already writes WHY a lane returned nothing. The webapp threw those notes
    away, so the one sentence that explains the empty analysis never left the process."""
    import api.webapp_analysis as wa
    import competitor
    from competitor.models import PeerMatchResult

    note = ("ECOMMERCE -> web engine 'serp-web': no peers (search refused: "
            "monthly Serper call cap reached (500+1 > 500)); standalone analysis.")
    # PATCH THE PACKAGE, NOT THE MODULE. `_compute_swot` does `from competitor import
    # route_discovery` INSIDE the function, so a name set on `api.webapp_analysis` is never read —
    # a trap this suite already fell into once with `web_engine`.
    monkeypatch.setattr(competitor, "route_discovery",
                        lambda *a, **k: PeerMatchResult([], True, 0, 0, [note], []))
    swot, _peers = wa._compute_swot({"name": "X", "category": "ecommerce",
                                     "source_url": "https://x.test/"})
    joined = " ".join(swot.notes or [])
    assert "serp-web" in joined or "Serper" in joined, (
        f"the reason the market looked empty never reached the client: {swot.notes}")


def test_an_exhausted_budget_is_not_reported_as_an_empty_market(monkeypatch):
    import api.webapp_analysis as wa
    import competitor
    from competitor.models import PeerMatchResult

    note = "no peers (search refused: monthly Serper call cap reached (500+1 > 500))"
    monkeypatch.setattr(competitor, "route_discovery",
                        lambda *a, **k: PeerMatchResult([], True, 0, 0, [note], []))
    swot, _peers = wa._compute_swot({"name": "X", "category": "ecommerce",
                                     "source_url": "https://x.test/"})
    joined = " ".join(swot.notes or [])
    assert "refused" in joined or "cap" in joined.lower(), joined


def test_a_healthy_run_gains_no_scary_note(monkeypatch):
    """The disclosure must fire on a real refusal, not on every run."""
    import api.webapp_analysis as wa
    import competitor
    from competitor.models import PeerMatchResult
    monkeypatch.setattr(competitor, "route_discovery",
                        lambda *a, **k: PeerMatchResult([], True, 0, 0,
                                                        ["business_type=ecommerce :: fine"], []))
    swot, _peers = wa._compute_swot({"name": "X", "category": "ecommerce",
                                     "source_url": "https://x.test/"})
    assert "refused" not in " ".join(swot.notes or [])
