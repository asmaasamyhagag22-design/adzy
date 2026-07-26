# -*- coding: utf-8 -*-
"""ONE place that decides how this project launches Chromium.

WHY A MODULE FOR TWO LINES. `--disable-http2` was added for a single measured site
(lcwaikiki.eg resets HTTP/2 from headless Chromium) and then applied globally. Forcing HTTP/1.1
changes our TLS/connection fingerprint, and MEASURED twice on the owner's own sites, same URL
seconds apart, the flag the only difference:

    alfouadpharmacies.com  (2026-07-25, crawler)      []  200 · 1,328,616 B   |  flag  429 · 169 B
    .../products/la-roche… (2026-07-26, product page) []      974,865 B       |  flag       169 B

The crawler was fixed in place. `scraper/product_page.py` kept the old global flag, so "paste a
product link" stayed broken for every site the flag handicaps — the same defect surviving in the
copy nobody edited. The rule lives HERE now: HTTP/2 by default, HTTP/1.1 only as a retry after
the protocol error that motivated the workaround.
"""
from __future__ import annotations

from typing import Any


def launch_chromium(pw: Any, *, http2: bool = True, headless: bool = True, **kwargs: Any) -> Any:
    """Launch Chromium the way this project has measured to be safe.

    http2=True (default) keeps HTTP/2 on. Pass http2=False ONLY as the retry after
    `looks_like_http2_failure()` says the transport itself failed."""
    args = list(kwargs.pop("args", []) or [])
    if not http2 and "--disable-http2" not in args:
        args.append("--disable-http2")
    return pw.chromium.launch(headless=headless, args=args, **kwargs)


def looks_like_http2_failure(result: Any) -> bool:
    """Did this fetch die on the HTTP/2 transport itself?

    Chromium reports `net::ERR_HTTP2_PROTOCOL_ERROR` before any page loads — which is why the
    site that hit it attempted 1 page and succeeded on 0. Matched on the message, not a status:
    there is no HTTP response at all when the protocol negotiation is what failed."""
    blob = f"{getattr(result, 'error_message', '') or ''} {getattr(result, 'error_code', '') or ''}"
    return "http2" in blob.lower().replace("_", "").replace("-", "").replace(" ", "")
