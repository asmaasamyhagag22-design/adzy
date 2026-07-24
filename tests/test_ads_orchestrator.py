# -*- coding: utf-8 -*-
"""ش1 — السحب الذكي التكيفي (2026-07-19): batch sampling over the Meta Ad Library with a
confidence early-stop and hard caps. ALL hermetic: FakeTransport (network seam), MockCaller,
tmp ledgers. autopull is DISABLED under BASEERA_NO_SPEND (conftest) so tests can never pull."""
from __future__ import annotations

import json
from datetime import date

from business_profile.llm.caller import MockCaller

from ads_intel.consumer import ref_slug
from ads_intel.orchestrator import (PullPolicy, adaptive_pull_competitor, autopull_enabled,
                                    enough_evidence)
from marketing_intel.extractor import _RawItem, _RawResponse
from marketing_intel.schemas import MarketingIntelligence


def _row(i, text="إعلان تجريبي بنص كافي للاقتباس", start="2026-06-01"):
    return {"id": str(i), "page_name": "Rival A", "publisher_platforms": ["facebook"],
            "ad_delivery_start_time": start, "media_type": "image",
            "ad_creative_bodies": [f"{text} #{i}"]}


class FakeTransport:
    """Pages of 20 rows each, follows the after-cursor. Counts fetches; can be armed to raise."""

    def __init__(self, pages, *, boom_on: int = -1):
        self.pages = pages
        self.fetches = 0
        self.boom_on = boom_on

    def fetch(self, url: str) -> dict:
        self.fetches += 1
        if self.fetches == self.boom_on:
            raise RuntimeError("HTTP 500")
        idx = 0
        if "after=" in url:
            idx = int(url.split("after=cursor")[1].split("&")[0])
        data = {"data": self.pages[idx]} if idx < len(self.pages) else {"data": []}
        if idx + 1 < len(self.pages):
            data["paging"] = {"cursors": {"after": f"cursor{idx + 1}"}}
        return data


def _mi(offers=0, pains=0):
    mk = lambda k, n: [{"value": f"{k}{i}", "evidence": [], "confidence": "high",
                        "source_type": "extracted"} for i in range(n)]
    return MarketingIntelligence(brand_ref="r", offers=mk("o", offers), usps=[],
                                 pain_points=mk("p", pains), message_angles=[], rejected=0)


def test_confidence_predicate_table():
    pol = PullPolicy()
    pres_no = type("P", (), {"winners_30d": 0})()
    pres_win = type("P", (), {"winners_30d": 1})()
    assert enough_evidence(_mi(offers=3), pres_no, pol) is True       # 3 offers
    assert enough_evidence(_mi(pains=5), pres_no, pol) is True        # 5 pains
    assert enough_evidence(_mi(), pres_win, pol) is True              # 1 winner ad
    assert enough_evidence(_mi(offers=2, pains=4), pres_no, pol) is False


def test_confidence_stops_after_first_batch(tmp_path):
    # 5 pages available; the extractor returns 3 verbatim offers on batch 1 -> EXACTLY 1 fetch.
    pages = [[_row(i + b * 20) for i in range(20)] for b in range(5)]
    tr = FakeTransport(pages)
    items = [_RawItem(kind="offer", text=f"offer {i}",
                      quote=f"إعلان تجريبي بنص كافي للاقتباس #{i}", permalink="") for i in range(3)]
    caller = MockCaller({"ads_competitor_intel": _RawResponse(items=items)})
    b = adaptive_pull_competitor("Rival A", country="EG", token="tkn", caller=caller,
                                 transport=tr, snapshots_dir=str(tmp_path),
                                 ledger=str(tmp_path / "ledger.jsonl"), today=date(2026, 7, 19))
    assert tr.fetches == 1 and b.stopped == "confidence" and b.ads_pulled == 20
    assert b.ref == ref_slug("Rival A")
    snap = json.loads((tmp_path / f"{b.ref}.json").read_text(encoding="utf-8"))
    assert len(snap["ads"]) == 20                       # snapshot in the pull-script shape
    # the provider round-trips the orchestrator's snapshot
    from ads_intel.providers.meta_ad_library import MetaAdLibraryProvider
    assert len(MetaAdLibraryProvider(snapshots_dir=str(tmp_path)).fetch(b.ref)) == 20


def test_hard_cap_never_exceeded(tmp_path):
    pages = [[_row(i + b * 20, start="2026-07-18") for i in range(20)]
             for b in range(30)]                              # 600 available, none a winner
    tr = FakeTransport(pages)
    caller = MockCaller({"ads_competitor_intel": _RawResponse(items=[])})  # never confident
    b = adaptive_pull_competitor("Rival A", country="EG", token="tkn", caller=caller,
                                 transport=tr, snapshots_dir=str(tmp_path),
                                 ledger=str(tmp_path / "l.jsonl"),
                                 policy=PullPolicy(hard_cap=60), today=date(2026, 7, 19))
    assert b.ads_pulled <= 60 and b.stopped == "hard_cap" and tr.fetches == 3


def test_transport_error_stops_fail_closed_with_partial(tmp_path):
    pages = [[_row(i + b * 20, start="2026-07-18") for i in range(20)] for b in range(5)]
    tr = FakeTransport(pages, boom_on=2)                 # second fetch explodes
    caller = MockCaller({"ads_competitor_intel": _RawResponse(items=[])})
    b = adaptive_pull_competitor("Rival A", country="EG", token="tkn", caller=caller,
                                 transport=tr, snapshots_dir=str(tmp_path),
                                 ledger=str(tmp_path / "l.jsonl"), today=date(2026, 7, 19))
    assert b.ads_pulled == 20 and b.stopped.startswith("error:")   # partial kept, no retry storm


def test_ledger_fail_closed_and_monthly_cap(tmp_path):
    from ads_intel.pull_ledger import check_calls, record_call
    led = tmp_path / "meta_pulls.jsonl"
    ok, _ = check_calls(1, ledger=str(led))
    assert ok
    record_call("https://graph/x", 20, ledger=str(led))
    led.write_text(led.read_text(encoding="utf-8") + "{corrupt\n", encoding="utf-8")
    ok2, msg = check_calls(1, ledger=str(led))
    assert not ok2 and "corrupt" in msg.lower()          # fail-closed on a corrupt line
    led2 = tmp_path / "l2.jsonl"
    for _ in range(3):
        record_call("https://graph/x", 20, ledger=str(led2))
    ok3, msg3 = check_calls(1, ledger=str(led2), monthly_cap=3)
    assert not ok3                                        # the monthly call cap blocks BEFORE fetch


def test_autopull_disabled_without_token_and_under_no_spend(monkeypatch):
    monkeypatch.delenv("META_ADS_TOKEN", raising=False)
    monkeypatch.delenv("BASEERA_NO_SPEND", raising=False)
    assert autopull_enabled() is False                    # no token
    monkeypatch.setenv("META_ADS_TOKEN", "tkn")
    monkeypatch.setenv("BASEERA_NO_SPEND", "1")
    assert autopull_enabled() is False                    # pytest/no-spend context
    monkeypatch.delenv("BASEERA_NO_SPEND", raising=False)
    monkeypatch.setenv("ADS_AUTOPULL_DISABLED", "1")
    assert autopull_enabled() is False                    # the escape hatch
    monkeypatch.delenv("ADS_AUTOPULL_DISABLED", raising=False)
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)   # the guard also blocks pytest itself
    assert autopull_enabled() is True


def test_page_hint_facebook_handle():
    from ads_intel.live import page_hint_from_website
    assert page_hint_from_website("https://www.facebook.com/meatmoot.egypt") == "meatmoot.egypt"
    assert page_hint_from_website("https://rakict.com/") == ""
    assert page_hint_from_website(None) == ""


def test_apify_token_unified_prefers_APIFY_TOKEN(monkeypatch):
    from social_intel.tokens import apify_token
    monkeypatch.delenv("APIFY_TOKEN", raising=False)
    monkeypatch.delenv("APIFY_API_TOKEN", raising=False)
    assert apify_token() is None
    monkeypatch.setenv("APIFY_API_TOKEN", "b")
    assert apify_token() == "b"
    monkeypatch.setenv("APIFY_TOKEN", "a")
    assert apify_token() == "a"                           # the canonical name wins
