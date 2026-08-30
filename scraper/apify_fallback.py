# -*- coding: utf-8 -*-
"""Apify fallback for sites our own crawler cannot read.

MEASURED (pepsi.com, 2026-07-21): the project's crawler drives a real headless Chromium and still
receives HTTP 403 on the homepage, on `sitemap.xml`, and on `robots.txt` itself — the refusal
happens at the CDN edge, before any content negotiation, so no browser setting gets past it. Owner's
decision: route those sites through Apify, which performs the fetch on its own infrastructure.

Deliberate properties, following the discipline already used for the Instagram provider
(`instagram_intelligence/providers/apify_provider.py`):

  * SECOND, never first. It runs only after our own crawl returned zero pages, so a site that works
    is never billed twice.
  * HARD PAGE CAP. Apify bills per result; `_MAX_PAGES` bounds any single run whatever the caller
    asks for.
  * DISCLOSED. The result is stamped `source="apify"` so nothing downstream can present
    "a paid service fetched this for us" as "we crawled their site".
  * QUIET DEGRADE. No token, a failure, or an empty run returns None and the caller keeps the
    honest "this site blocks crawlers" message. Never raises, never a silent empty profile.

NOTE for the owner, stated once and not repeated: a site that returns 403 even for `robots.txt` is
refusing to publish a crawling policy at all. Using a third-party fetcher there is a decision about
that site's terms, and it is yours to make per brand — the code does not make it silently, which is
why every fallback run is stamped and logged.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

# Apify bills per result. No single fallback run may exceed this, whatever the caller passes.
_MAX_PAGES = 50
_DEFAULT_PAGES = 12

# The generic content crawler — a maintained, general-purpose actor rather than a brand-specific one.
_ACTOR = os.getenv("APIFY_CRAWLER_ACTOR", "apify~website-content-crawler")
_API = "https://api.apify.com/v2"


@dataclass
class ApifyResult:
    """Pages fetched by Apify. `source` travels with them so provenance is never lost."""
    pages: list = field(default_factory=list)
    source: str = "apify"
    actor: str = _ACTOR


# MEASURED (2026-07-21): a client read-timeout on `run-sync-get-dataset-items` closed OUR socket
# and left the actor RUNNING. An hour later the account sat at its full ceiling and every new run
# failed with `402 actor-memory-limit-exceeded — currently used: 16384MB`. A client timeout is not
# a cancellation. So: start the run, poll it, and ABORT it on our way out.
_MAX_MEMORY_MB = 2048          # one fallback must never be able to consume the whole account
_RUN_TIMEOUT_S = 300           # Apify stops the run itself even if this process disappears


def build_run_input(url: str, *, max_pages: int) -> dict:
    """The actor input — bounded in pages, memory AND server-side time."""
    return {
        "startUrls": [{"url": url}],
        "maxCrawlPages": max_pages,
        "crawlerType": "playwright:adaptive",
        "saveHtml": True,
        "memory": _MAX_MEMORY_MB,
        "timeout": _RUN_TIMEOUT_S,
    }


def _post(path: str, token: str, body: Optional[dict] = None, timeout: int = 60):
    import json
    import urllib.parse
    import urllib.request
    url = f"{_API}/{path}{'&' if '?' in path else '?'}token={urllib.parse.quote(token)}"
    data = json.dumps(body).encode("utf-8") if body is not None else b""
    req = urllib.request.Request(url, data=data,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read().decode("utf-8")
    return json.loads(raw) if raw.strip() else {}


def _start_run(url: str, *, max_pages: int, token: str) -> Optional[str]:
    data = _post(f"acts/{_ACTOR}/runs?memory={_MAX_MEMORY_MB}&timeout={_RUN_TIMEOUT_S}",
                 token, build_run_input(url, max_pages=max_pages))
    return ((data or {}).get("data") or {}).get("id")


def _abort_run(run_id: str, *, token: str) -> None:
    try:
        _post(f"actor-runs/{run_id}/abort", token, timeout=30)
    except Exception:  # noqa: BLE001 — best effort; the server-side timeout is the backstop
        pass


def _wait_for_items(run_id: str, *, token: str) -> list:
    """Poll until the run finishes, then read its dataset. Raises TimeoutError past the budget."""
    import json
    import time
    import urllib.parse
    import urllib.request

    deadline = time.monotonic() + _RUN_TIMEOUT_S
    dataset_id = None
    while time.monotonic() < deadline:
        url = (f"{_API}/actor-runs/{run_id}"
               f"?token={urllib.parse.quote(token)}")
        with urllib.request.urlopen(url, timeout=30) as r:
            info = json.loads(r.read().decode("utf-8"))
        d = (info or {}).get("data") or {}
        status = str(d.get("status") or "")
        dataset_id = d.get("defaultDatasetId") or dataset_id
        if status in ("SUCCEEDED", "FAILED", "ABORTED", "TIMED-OUT"):
            break
        time.sleep(5)
    else:
        raise TimeoutError("apify run exceeded the local budget")
    if not dataset_id:
        return []
    url = (f"{_API}/datasets/{dataset_id}/items"
           f"?token={urllib.parse.quote(token)}&clean=true")
    with urllib.request.urlopen(url, timeout=60) as r:
        return json.loads(r.read().decode("utf-8"))


def apify_scrape(url: str, *, token: Optional[str] = None, max_pages: int = _DEFAULT_PAGES,
                 runner: Optional[Callable[..., Any]] = None,
                 record: Optional[Callable[[int, str], None]] = None) -> Optional[ApifyResult]:
    """Fetch `url` through Apify. Returns None on anything short of real content."""
    token = token if token is not None else (os.environ.get("APIFY_TOKEN")
                                             or os.environ.get("APIFY_API_TOKEN") or "")
    if not str(token).strip():
        return None
    cap = max(1, min(int(max_pages or _DEFAULT_PAGES), _MAX_PAGES))
    if runner is not None:                 # injected in tests
        try:
            items = runner(url, max_pages=cap, token=token) or []
        except Exception:  # noqa: BLE001
            return None
    else:
        run_id = None
        try:
            run_id = _start_run(url, max_pages=cap, token=token)
            if not run_id:
                return None
            items = _wait_for_items(run_id, token=token) or []
        except Exception:  # noqa: BLE001 — a fallback must never break the caller's error path
            if run_id:
                _abort_run(run_id, token=token)   # walking away is not cancelling
            return None

    pages = []
    for it in items:
        if not isinstance(it, dict):
            continue
        text = str(it.get("text") or it.get("markdown") or "").strip()
        if not text:
            continue                       # a fetched-but-empty page is not evidence
        pages.append(it)
        if len(pages) >= cap:
            break
    if not pages:
        return None
    if record is not None:
        try:
            record(len(pages), url)
        except Exception:  # noqa: BLE001
            pass
    return ApifyResult(pages=pages)
