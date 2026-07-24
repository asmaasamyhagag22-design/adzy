"""P-94 / CS-03: the studio's PAID GET routes must reject crawl / prefetch / cross-site CSRF
triggers, while the studio's own same-origin EventSource/fetch still works. They stay GET because
EventSource is GET-only, so this guard is the compensating control.

CS-03: P-94 guarded only `/generate/*`, leaving `/analyze` (the whole pipeline — the biggest
spender in the system), `/ask` (LLM + embeddings) and `/hitl/preview` (LLM critic) reachable by the
same unattended GET. Paid is paid.
"""
from __future__ import annotations

import pytest

from dashboard.server import _PAID_GET_ROUTES, _paid_get_trusted


def _hdr(d):
    # http.server headers expose .get(name, default); a plain dict matches that shape.
    return {k.lower(): v for k, v in d.items()}


def test_same_origin_eventsource_is_allowed():
    # what the studio page's EventSource actually sends
    assert _paid_get_trusted(_hdr({"Sec-Fetch-Site": "same-origin",
                                   "Sec-Fetch-Mode": "cors",
                                   "Sec-Fetch-Dest": "empty"})) is True


def test_cross_site_csrf_is_rejected():
    # a malicious page doing <img src="http://localhost:8770/generate/reel?slug=x">
    assert _paid_get_trusted(_hdr({"Sec-Fetch-Site": "cross-site",
                                   "Sec-Fetch-Dest": "image"})) is False


def test_prefetch_is_rejected():
    assert _paid_get_trusted(_hdr({"Sec-Fetch-Site": "same-origin",
                                   "Sec-Purpose": "prefetch;prerender"})) is False
    assert _paid_get_trusted(_hdr({"Purpose": "prefetch"})) is False


def test_direct_navigation_is_rejected():
    # address-bar paste / bookmark -> Sec-Fetch-Site: none. The studio never does this.
    assert _paid_get_trusted(_hdr({"Sec-Fetch-Site": "none",
                                   "Sec-Fetch-Mode": "navigate"})) is False


def test_headerless_local_client_is_allowed():
    # curl / very old client with no Sec-Fetch metadata: already needs local access to a
    # loopback-bound server, so we don't break it.
    assert _paid_get_trusted(_hdr({})) is True


# ---- CS-03: the guard must cover EVERY spending route, not just the two it was written for ----

def test_every_paid_route_is_in_the_guarded_set():
    """Each of these was traced to a real spend site. If a route is added to the studio that spends,
    it belongs here — a guard that covers only the routes someone remembered is the P-94 bug."""
    for route in ("/analyze",            # _stream_analyze -> run.analyze() = the whole pipeline
                  "/ask",                # _serve_ask -> archive_qa (own caller + embeddings)
                  "/hitl/preview",       # run.hitl_preview -> default_caller() + _arabic_rendering
                  "/generate/poster",    # Imagen/Gemini
                  "/generate/reel"):     # Veo
        assert route in _PAID_GET_ROUTES, f"{route} spends money but is not guarded"


def test_free_routes_are_not_guarded():
    """The guard costs a real request its life — it must not touch routes that spend nothing."""
    for route in ("/", "/studio", "/asset", "/dashboard", "/products",
                  "/product_by_url", "/scrape_qa", "/favicon.ico"):
        assert route not in _PAID_GET_ROUTES


@pytest.mark.parametrize("route", sorted(_PAID_GET_ROUTES))
def test_paid_route_rejects_prefetch_and_cross_site(route):
    """Applied per-route so a future route added to the set inherits the coverage automatically."""
    # the studio's own call — must survive
    assert _paid_get_trusted(_hdr({"Sec-Fetch-Site": "same-origin"})) is True
    # a crawler / prefetch / cross-site <img> must never trigger this route
    assert _paid_get_trusted(_hdr({"Sec-Fetch-Site": "cross-site"})) is False
    assert _paid_get_trusted(_hdr({"Sec-Fetch-Site": "none"})) is False
    assert _paid_get_trusted(_hdr({"Sec-Purpose": "prefetch"})) is False
