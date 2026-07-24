"""Pluggable web-search providers for competitor discovery.

A SearchProvider turns a query string into organic web results (SearchHit). The
competitor web-discovery engine (web_discovery.py) consumes these to find market
peers for ECOMMERCE / online businesses the Places (LOCAL) path can't reach.

MODULAR BY DESIGN — start on whatever the budget allows. `get_default_search_provider`
picks the first provider whose key is configured, in this order:
    Serper -> SearchApi -> Google CSE
Returns None when none is configured; the router then degrades to a grounded
standalone SWOT (peers are NEVER fabricated).

Keys (set ONE in .env at repo root):
    SERPER_API_KEY                        serper.dev          (paid credits, cheap)
    SEARCHAPI_API_KEY                     searchapi.io        (100 free searches)
    GOOGLE_CSE_API_KEY + GOOGLE_CSE_CX    Google Custom       (100 free/day)
                                          Search JSON API

Only the standard library is used (urllib) — no new dependency. Network failures
are swallowed and surfaced as an empty result; a provider never raises to its
caller (the router treats an empty peer set as an honest standalone case).
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import List, Optional, Protocol, runtime_checkable
from urllib.parse import urlencode

from scraper.net import get_json as _net_get_json, post_json as _net_post_json


@dataclass
class SearchHit:
    title: str
    link: str
    snippet: str = ""
    rank: int = 0                       # 1-based organic position (kept for provenance)


@runtime_checkable
class SearchProvider(Protocol):
    name: str

    def search(
        self, query: str, *, num: int = 10,
        gl: Optional[str] = None, hl: Optional[str] = None,
    ) -> List[SearchHit]: ...


# Both delegate to the shared resilient fetcher (bounded retry on a transient blip +
# per-host circuit breaker), so a single dropped request no longer silently collapses
# competitor discovery to a standalone SWOT. They return {} (never None / raise) so the
# providers' result parsing stays simple.
def _post_json(url: str, body: dict, headers: dict, timeout: int = 12) -> dict:
    return _net_post_json(url, body, headers=headers, timeout=timeout) or {}


def _get_json(url: str, timeout: int = 12) -> dict:
    return _net_get_json(
        url, timeout=timeout, headers={"User-Agent": "scraper_v01-competitor/1.0"}
    ) or {}


class SerperProvider:
    """serper.dev /search. Reads SERPER_API_KEY."""
    name = "serper"
    _ENDPOINT = "https://google.serper.dev/search"

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or os.getenv("SERPER_API_KEY")
        self.last_error: Optional[str] = None

    def search(self, query, *, num=10, gl=None, hl=None) -> List[SearchHit]:
        if not self.api_key or not query.strip():
            return []
        # SERPER-UNMETERED (C3 gap closed): the ONLY Serper chokepoint — every caller
        # (discovery, trends) pays through here. Monthly call cap + jsonl ledger, fail-closed
        # on a corrupt ledger (an unreadable ledger is an unenforceable cap).
        from ads_intel.pull_ledger import check_calls, record_call
        _ledger = os.getenv("SERPER_LEDGER", "runs/serper_calls.jsonl")
        _cap = int(os.getenv("SERPER_MONTHLY_CALL_CAP", "500"))
        ok, msg = check_calls(1, ledger=_ledger, monthly_cap=_cap)
        if not ok:
            self.last_error = f"refused: {msg}"
            return []
        payload = {"q": query, "num": max(1, min(num, 20))}
        if gl:
            payload["gl"] = gl
        if hl:
            payload["hl"] = hl
        try:
            data = _post_json(
                self._ENDPOINT, payload,
                {"X-API-KEY": self.api_key, "Content-Type": "application/json"},
            )
        except Exception as exc:                       # noqa: BLE001 - never raise to caller
            self.last_error = repr(exc)
            return []
        out: List[SearchHit] = []
        for i, item in enumerate(data.get("organic", []) or [], start=1):
            link = item.get("link")
            if link:
                out.append(SearchHit(item.get("title", "") or "", link,
                                     item.get("snippet", "") or "", i))
        record_call(f"serper:{query[:60]}", len(out), ledger=_ledger)
        return out


class SearchApiProvider:
    """searchapi.io (engine=google). Reads SEARCHAPI_API_KEY. 100 free searches."""
    name = "searchapi"
    _ENDPOINT = "https://www.searchapi.io/api/v1/search"

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or os.getenv("SEARCHAPI_API_KEY")
        self.last_error: Optional[str] = None

    def search(self, query, *, num=10, gl=None, hl=None) -> List[SearchHit]:
        if not self.api_key or not query.strip():
            return []
        params = {"engine": "google", "q": query, "api_key": self.api_key, "num": num}
        if gl:
            params["gl"] = gl
        if hl:
            params["hl"] = hl
        try:
            data = _get_json(f"{self._ENDPOINT}?{urlencode(params)}")
        except Exception as exc:                       # noqa: BLE001
            self.last_error = repr(exc)
            return []
        out: List[SearchHit] = []
        for item in data.get("organic_results", []) or []:
            link = item.get("link")
            if link:
                out.append(SearchHit(item.get("title", "") or "", link,
                                     item.get("snippet", "") or "",
                                     int(item.get("position", 0) or 0)))
        return out


class GoogleCSEProvider:
    """Official Google Custom Search JSON API. 100 free searches/day.

    Reads GOOGLE_CSE_API_KEY (the API key) + GOOGLE_CSE_CX (the Search Engine ID).
    Setup: console.cloud.google.com -> enable "Custom Search API" -> create an API
    key; programmablesearchengine.google.com -> create an engine set to "Search the
    entire web" -> copy its Search engine ID (cx).
    """
    name = "google_cse"
    _ENDPOINT = "https://www.googleapis.com/customsearch/v1"

    def __init__(self, api_key: Optional[str] = None, cx: Optional[str] = None):
        self.api_key = api_key or os.getenv("GOOGLE_CSE_API_KEY")
        self.cx = cx or os.getenv("GOOGLE_CSE_CX")
        self.last_error: Optional[str] = None

    def search(self, query, *, num=10, gl=None, hl=None) -> List[SearchHit]:
        if not self.api_key or not self.cx or not query.strip():
            return []
        params = {"key": self.api_key, "cx": self.cx, "q": query,
                  "num": max(1, min(num, 10))}             # CSE caps num at 10
        if gl:
            params["gl"] = gl
        if hl:
            params["hl"] = hl
        try:
            data = _get_json(f"{self._ENDPOINT}?{urlencode(params)}")
        except Exception as exc:                       # noqa: BLE001
            self.last_error = repr(exc)
            return []
        out: List[SearchHit] = []
        for i, item in enumerate(data.get("items", []) or [], start=1):
            link = item.get("link")
            if link:
                out.append(SearchHit(item.get("title", "") or "", link,
                                     item.get("snippet", "") or "", i))
        return out


def get_default_search_provider() -> Optional[SearchProvider]:
    """First configured provider, cheapest-key-first. None if nothing is set."""
    if os.getenv("SERPER_API_KEY"):
        return SerperProvider()
    if os.getenv("SEARCHAPI_API_KEY"):
        return SearchApiProvider()
    if os.getenv("GOOGLE_CSE_API_KEY") and os.getenv("GOOGLE_CSE_CX"):
        return GoogleCSEProvider()
    return None
