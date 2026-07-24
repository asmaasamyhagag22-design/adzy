# -*- coding: utf-8 -*-
"""A timed-out Apify call must ABORT the run, not abandon it.

MEASURED (2026-07-21): a diagnostic call to `run-sync-get-dataset-items` hit the client read
timeout. The HTTP connection closed — and the actor kept running on Apify's side. An hour later
the account was at its full 16GB memory ceiling and every new run failed:

    HTTP 402 actor-memory-limit-exceeded — currently used: 16384MB

A client-side timeout is not a cancellation. Abandoning a run leaves it billing and holding the
account's quota, so the next real analysis fails for a reason that has nothing to do with it.

Also pinned: an explicit memory request. Without one the actor takes the platform default, which
on this account is large enough that a single run consumes the whole ceiling. Hermetic."""
from __future__ import annotations

from scraper.apify_fallback import _MAX_MEMORY_MB, build_run_input


def test_the_run_declares_a_bounded_memory():
    body = build_run_input("https://x.example/", max_pages=10)
    assert body["memory"] <= _MAX_MEMORY_MB
    assert _MAX_MEMORY_MB <= 4096, "one fallback must not be able to take the whole account"


def test_the_page_cap_reaches_the_actor():
    assert build_run_input("https://x.example/", max_pages=7)["maxCrawlPages"] == 7


def test_a_server_side_timeout_is_requested():
    body = build_run_input("https://x.example/", max_pages=10)
    assert body.get("timeout", 0) > 0, "Apify must stop the run itself if we go away"


def test_the_start_url_is_carried():
    body = build_run_input("https://pepsi.com/", max_pages=3)
    assert body["startUrls"][0]["url"] == "https://pepsi.com/"


def test_a_timed_out_call_aborts_the_run(monkeypatch):
    """The regression this file exists for: when the fetch raises, the run id we started must be
    told to stop."""
    from scraper import apify_fallback as af
    aborted: list = []

    def fake_start(url, *, max_pages, token):
        return "run_123"

    def fake_wait(run_id, *, token):
        raise TimeoutError("read timed out")

    monkeypatch.setattr(af, "_start_run", fake_start)
    monkeypatch.setattr(af, "_wait_for_items", fake_wait)
    monkeypatch.setattr(af, "_abort_run", lambda rid, *, token: aborted.append(rid))
    assert af.apify_scrape("https://x.example/", token="t") is None
    assert aborted == ["run_123"], "a run we walked away from keeps billing"


def test_a_successful_call_does_not_abort(monkeypatch):
    from scraper import apify_fallback as af
    aborted: list = []
    monkeypatch.setattr(af, "_start_run", lambda url, *, max_pages, token: "run_9")
    monkeypatch.setattr(af, "_wait_for_items",
                        lambda rid, *, token: [{"url": "u", "text": "body"}])
    monkeypatch.setattr(af, "_abort_run", lambda rid, *, token: aborted.append(rid))
    res = af.apify_scrape("https://x.example/", token="t")
    assert res is not None and aborted == []
