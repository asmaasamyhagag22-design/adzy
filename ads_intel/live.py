# -*- coding: utf-8 -*-
"""ش1 — the Meta Ad Library LIVE seam: ONE transport Protocol (FakeTransport in tests,
UrllibTransport the only real-network class — PD-4), cursor pagination, and the facebook-URL
page-handle hint. Fail-closed: any HTTP/parse error stops the iteration WITH what was already
collected — never a retry storm."""
from __future__ import annotations

import json
import re
import urllib.parse
import urllib.request
from typing import Iterator, Optional, Protocol

_GRAPH = "https://graph.facebook.com/v21.0/ads_archive"


class MetaAdsTransport(Protocol):
    def fetch(self, url: str) -> dict: ...


class UrllibTransport:
    """The ONLY class that touches the network (timeout-bounded)."""

    def fetch(self, url: str) -> dict:
        with urllib.request.urlopen(url, timeout=60) as r:
            return json.loads(r.read().decode("utf-8"))


def build_query_url(*, search_terms: str, country: str, fields: str, limit: int,
                    token: str, after: Optional[str] = None,
                    search_type: str = "KEYWORD_EXACT_PHRASE") -> str:
    # 1.1: EXACT_PHRASE — a multi-word competitor name matches as a phrase, not scattered
    # keywords across every advertiser (the default KEYWORD_UNORDERED pulled strangers).
    params = {"search_terms": search_terms, "ad_reached_countries": f'["{country}"]',
              "ad_active_status": "ACTIVE", "search_type": search_type, "fields": fields,
              "limit": str(max(1, min(limit, 100))), "access_token": token}
    if after:
        params["after"] = after
    return _GRAPH + "?" + urllib.parse.urlencode(params)


def iter_ad_pages(search_terms: str, *, country: str, token: str, fields: str,
                  transport: MetaAdsTransport, batch_size: int = 20,
                  hard_cap: int = 200) -> Iterator[list]:
    """Yield pages of raw ads_archive rows (≤batch_size each) following the after-cursor, stopping
    at hard_cap total rows, the end of results, or the FIRST error (fail-closed, partial kept)."""
    pulled = 0
    after: Optional[str] = None
    search_type = "KEYWORD_EXACT_PHRASE"
    # MEASURED (live, 2026-07-20, EG/ACTIVE): "Vodafone Egypt" returns 0 as an exact phrase and 1
    # unordered — a multi-word competitor name can vanish entirely. Precision is still tried first;
    # only a ZERO first page widens the search once, and the relevance gate downstream
    # (ads_intel/relevance, applied at the ads_from_snapshot chokepoint) drops the strangers that
    # widening lets in. A one-word query is skipped: both search types mean the same thing there.
    widened = " " not in (search_terms or "").strip()
    while pulled < hard_cap:
        url = build_query_url(search_terms=search_terms, country=country, fields=fields,
                              limit=min(batch_size, hard_cap - pulled), token=token, after=after,
                              search_type=search_type)
        data = transport.fetch(url)          # an exception here propagates ONCE — caller stops
        rows = list(data.get("data") or [])
        if not rows:
            if pulled == 0 and not widened:
                widened = True
                search_type = "KEYWORD_UNORDERED"
                continue
            return
        pulled += len(rows)
        yield rows
        after = ((data.get("paging") or {}).get("cursors") or {}).get("after")
        if not after:
            return


_UNRESOLVED = object()      # "resolve it yourself", distinct from "resolved to nothing" (None)


def iter_competitor_ads(name: str, *, country: str, token: str, fields: str,
                        transport: MetaAdsTransport, batch_size: int = 20,
                        hard_cap: int = 200, page_id=_UNRESOLVED) -> Iterator[tuple[list, dict]]:
    """Yield (page_of_ads, basis) for ONE competitor, strongest evidence first.

    1. Resolve the advertiser's page by NAME and pull that page's ads — every row is provably
       theirs (verified live: a search_page_ids query returns one page_id only).
    2. Only if no page can be identified, fall back to the keyword search, whose rows are ad-copy
       matches and still need the relevance gate.

    `basis` travels WITH the rows so the difference between "their ads" and "ads mentioning them"
    never gets lost downstream. An identified advertiser running NOTHING yields no rows and does
    NOT fall back: "they aren't advertising" is a real finding, and replacing it with strangers'
    ads would turn a true answer into a false one.
    """
    # `page_id` may be supplied by a caller that already resolved it — the orchestrator does,
    # because identifying the advertiser is a real API request and has to be checked and recorded
    # against the pull budget on its own rather than riding along inside the first page's budget unit.
    if page_id is _UNRESOLVED:
        page_id = resolve_page_id(name, country=country, token=token, transport=transport)
    if page_id:
        basis = {"basis": "page_id", "page_id": page_id}
        pulled, after = 0, None
        while pulled < hard_cap:
            url = build_page_query_url(page_id=page_id, country=country, fields=fields,
                                       limit=min(batch_size, hard_cap - pulled), token=token,
                                       after=after)
            try:
                data = transport.fetch(url)
            except Exception:  # noqa: BLE001
                return
            rows = list((data or {}).get("data") or [])
            if not rows:
                if pulled == 0:
                    # The advertiser WAS identified and is running nothing. Yield the empty page
                    # so the caller learns that — otherwise "identified, not advertising" is
                    # indistinguishable from "we never found their page", and only one of those
                    # is a fact about the competitor.
                    yield [], basis
                return
            pulled += len(rows)
            yield rows, basis
            after = ((data.get("paging") or {}).get("cursors") or {}).get("after")
            if not after:
                return
        return
    basis = {"basis": "keyword", "page_id": ""}
    yielded = False
    for page in iter_ad_pages(name, country=country, token=token, fields=fields,
                              transport=transport, batch_size=batch_size, hard_cap=hard_cap):
        yielded = True
        yield page, basis
    if not yielded:
        yield [], basis          # same reason: the basis must reach the caller even when empty


def _norm_page_name(s: str) -> str:
    return " ".join(str(s or "").lower().split())


def resolve_page_id(name: str, *, country: str, token: str, transport: MetaAdsTransport,
                    limit: int = 25) -> Optional[str]:
    """The advertiser's page id, or None when we cannot identify it honestly.

    MEASURED (live, 2026-07-20): `search_terms` matches AD COPY, not the advertiser — searching
    "NTI" in EG returned ads by AMOpportunities, Alibaba.com and Codigo Divino. Attributing those
    to a competitor would put a stranger's budget, angles and offers into her intel. So the match
    is made on `page_name` — the advertiser's OWN name — and anything short of a real name match
    returns None. A missing competitor is a gap she can see; a wrong one is a false fact.
    """
    q = _norm_page_name(name)
    if not q:
        return None
    url = build_query_url(search_terms=name, country=country,
                          fields="id,page_id,page_name", limit=limit, token=token,
                          search_type="KEYWORD_UNORDERED")
    try:
        rows = list((transport.fetch(url) or {}).get("data") or [])
    except Exception:  # noqa: BLE001 — an unreachable API is "unknown", never a guess
        return None
    exact: Optional[str] = None
    partial: Optional[str] = None
    for r in rows:
        pid, pname = r.get("page_id"), _norm_page_name(r.get("page_name"))
        if not pid or not pname:
            continue
        if pname == q:
            return str(pid)                       # the advertiser IS this competitor
        if exact is None and (pname.startswith(q + " ") or pname.endswith(" " + q)
                              or f" {q} " in f" {pname} "):
            partial = partial or str(pid)         # the name appears as whole words in the page name
    return exact or partial


def build_page_query_url(*, page_id: str, country: str, fields: str, limit: int, token: str,
                         after: Optional[str] = None) -> str:
    """Ads BY one advertiser. Verified live: every row comes back with that same page_id."""
    params = {"search_page_ids": f'["{page_id}"]', "ad_reached_countries": f'["{country}"]',
              "ad_active_status": "ACTIVE", "fields": fields,
              "limit": str(max(1, min(limit, 100))), "access_token": token}
    if after:
        params["after"] = after
    return _GRAPH + "?" + urllib.parse.urlencode(params)


def page_hint_from_website(url: Optional[str]) -> str:
    """'https://facebook.com/<handle>' -> 'handle' (the only place discovery ever captures a
    competitor's social page). '' for a normal website — never a guess."""
    m = re.match(r"https?://(?:www\.)?facebook\.com/([^/?#]+)", str(url or ""), re.I)
    return m.group(1) if m else ""
