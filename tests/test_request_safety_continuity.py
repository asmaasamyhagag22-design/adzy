"""F-01: destination safety must survive redirects and browser subrequests.

Hermetic by construction: DNS, urllib's opener, and the browser are all fakes.  No
socket is opened and every hostname lives below the reserved ``.invalid`` TLD.
"""
from __future__ import annotations

import socket
import urllib.error
import urllib.request

import pytest

from scraper.errors import ErrorCode


_START = "https://start.public.invalid/"
_MIDDLE = "https://middle.public.invalid/next"
_SAFE_FINAL = "https://cdn.public.invalid/final"
_BLOCKED = "https://blocked.destination.invalid/denied"


@pytest.fixture
def fake_dns(monkeypatch):
    """Classify synthetic public hosts as public and the blocked host as loopback."""
    def _resolve(host, port, *args, **kwargs):
        ip = "127.0.0.1" if str(host).lower() == "blocked.destination.invalid" else "93.184.216.34"
        return [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", (ip, port))]

    monkeypatch.setattr("scraper.url_utils.socket.getaddrinfo", _resolve)


class _Response:
    def __init__(self, body: bytes = b"ok", *, url: str = _START):
        self._body = body
        self.url = url
        self.status = 200
        self.headers = {}

    def read(self, size=None):
        return self._body if size is None else self._body[:size]

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


class _RedirectFP:
    def read(self, *_args):
        return b""

    def close(self):
        pass


class _ScriptedOpener:
    """Tiny urllib transport that follows scripted redirects through any supplied policy."""
    def __init__(self, redirects, body, handlers=()):
        self.redirects = list(redirects)
        self.body = body
        self.handlers = list(handlers)
        self.consumed: list[str] = []

    def open(self, request, timeout=None):
        req = request if isinstance(request, urllib.request.Request) else urllib.request.Request(request)
        self.consumed.append(req.full_url)
        for target in self.redirects:
            redirect_handler = next(
                (h for h in self.handlers
                 if isinstance(h, urllib.request.HTTPRedirectHandler)),
                None,
            )
            if redirect_handler is not None:
                req = redirect_handler.redirect_request(
                    req, _RedirectFP(), 302, "Found", {}, target
                )
            else:
                req = urllib.request.Request(target)
            # This records a destination only after the redirect policy allowed following it.
            self.consumed.append(target)
        return _Response(self.body, url=req.full_url)


class _UrllibScenario:
    def __init__(self, monkeypatch, redirects, body=b"ok"):
        self.legacy_openers: list[_ScriptedOpener] = []
        self.policy_openers: list[_ScriptedOpener] = []

        def _legacy_urlopen(request, timeout=None, context=None, **_kwargs):
            # urllib's historical direct path follows redirects automatically.  An HTTPS
            # context is transport configuration, not a destination-safety policy.
            opener = _ScriptedOpener(redirects, body)
            self.legacy_openers.append(opener)
            return opener.open(request, timeout=timeout)

        monkeypatch.setattr(urllib.request, "urlopen", _legacy_urlopen)

        def _build_opener(*handlers):
            opener = _ScriptedOpener(redirects, body, handlers)
            self.policy_openers.append(opener)
            return opener

        monkeypatch.setattr(urllib.request, "build_opener", _build_opener)

    @property
    def consumed(self):
        if self.policy_openers:
            return self.policy_openers[-1].consumed
        if self.legacy_openers:
            return self.legacy_openers[-1].consumed
        return []


def test_robots_redirect_destination_is_checked_before_follow(fake_dns, monkeypatch):
    from scraper.robots import load_robots

    scenario = _UrllibScenario(monkeypatch, [_BLOCKED], b"User-agent: *\nAllow: /\n")
    _parser, record = load_robots(_START, timeout=0.01)

    assert scenario.consumed == [_START + "robots.txt"]
    assert _BLOCKED not in scenario.consumed
    assert record.allowed is True
    assert "unavailable" in (record.note or "").lower()


def test_sitemap_redirect_destination_is_checked_before_follow(fake_dns, monkeypatch):
    import scraper.sitemap as sitemap

    body = b"<urlset><url><loc>https://start.public.invalid/a</loc></url></urlset>"
    scenario = _UrllibScenario(monkeypatch, [_BLOCKED], body)
    # sitemap.py bound stdlib urlopen at module import; point that legacy seam at the
    # scripted stdlib call.  The repaired implementation no longer consumes this seam.
    monkeypatch.setattr(sitemap, "urlopen", urllib.request.urlopen, raising=False)
    assert sitemap._fetch_sitemap_bytes(_START + "sitemap.xml") is None
    assert scenario.consumed == [_START + "sitemap.xml"]
    assert _BLOCKED not in scenario.consumed


def test_remote_image_redirect_destination_is_checked_before_follow(fake_dns, monkeypatch):
    from poster.imagen_edit_provider import _fetch_bytes

    scenario = _UrllibScenario(monkeypatch, [_BLOCKED], b"synthetic-image")
    assert _fetch_bytes(_START + "logo.png") is None
    assert scenario.consumed == [_START + "logo.png"]
    assert _BLOCKED not in scenario.consumed


def test_poster_product_asset_redirect_is_checked_before_follow(fake_dns, monkeypatch):
    from poster.product_composite import _fetch

    scenario = _UrllibScenario(monkeypatch, [_BLOCKED], b"synthetic-product-image")
    assert _fetch(_START + "product.png") is None
    assert scenario.consumed == [_START + "product.png"]
    assert _BLOCKED not in scenario.consumed


def test_poster_logo_opener_checks_redirect_before_follow(fake_dns, monkeypatch):
    from poster.template import _open_image_url

    scenario = _UrllibScenario(monkeypatch, [_BLOCKED], b"logo")
    with pytest.raises(urllib.error.HTTPError):
        _open_image_url(_START + "logo.png", 1)
    assert scenario.consumed == [_START + "logo.png"]


def test_scraped_logo_signal_fetch_checks_redirect_before_follow(fake_dns, monkeypatch):
    from scraper.extractors.visual import _logo_svg_signals

    scenario = _UrllibScenario(monkeypatch, [_BLOCKED], b'<svg fill="#123456"/>')
    assert _logo_svg_signals(_START + "logo.svg", _START) == []
    assert scenario.consumed == [_START + "logo.svg"]


def test_same_origin_ajax_script_fetch_checks_redirect_before_follow(fake_dns, monkeypatch):
    from scraper.ajax_details import _default_script_fetch

    scenario = _UrllibScenario(monkeypatch, [_BLOCKED], b"console.log('safe')")
    assert _default_script_fetch(_START + "app.js") is None
    assert scenario.consumed == [_START + "app.js"]


def test_non_browser_multi_hop_stops_at_first_blocked_destination(fake_dns, monkeypatch):
    from poster.imagen_edit_provider import _fetch_bytes

    scenario = _UrllibScenario(monkeypatch, [_MIDDLE, _BLOCKED, _SAFE_FINAL], b"image")
    assert _fetch_bytes(_START + "asset.png") is None
    assert scenario.consumed == [_START + "asset.png", _MIDDLE]


def test_non_browser_public_redirect_control_still_succeeds(fake_dns, monkeypatch):
    from poster.imagen_edit_provider import _fetch_bytes

    scenario = _UrllibScenario(monkeypatch, [_SAFE_FINAL], b"public-image")
    assert _fetch_bytes(_START + "asset.png") == b"public-image"
    assert scenario.consumed == [_START + "asset.png", _SAFE_FINAL]


def test_robots_and_sitemap_public_redirect_controls_still_succeed(fake_dns, monkeypatch):
    from scraper.robots import load_robots

    robots = _UrllibScenario(monkeypatch, [_SAFE_FINAL], b"User-agent: *\nAllow: /\n")
    _parser, record = load_robots(_START, timeout=0.01)
    assert record.allowed is True and record.note is None
    assert robots.consumed == [_START + "robots.txt", _SAFE_FINAL]

    import scraper.sitemap as sitemap
    xml = b"<urlset><url><loc>https://start.public.invalid/a</loc></url></urlset>"
    sitemap_scenario = _UrllibScenario(monkeypatch, [_SAFE_FINAL], xml)
    assert sitemap._fetch_sitemap_bytes(_START + "sitemap.xml") == xml
    assert sitemap_scenario.consumed == [_START + "sitemap.xml", _SAFE_FINAL]


def test_public_redirect_normalization_case_default_port_and_encoded_path(fake_dns, monkeypatch):
    from poster.imagen_edit_provider import _fetch_bytes

    normalized_variant = "https://CDN.PUBLIC.INVALID:443/assets/%2E%2E/logo.png"
    scenario = _UrllibScenario(monkeypatch, [normalized_variant], b"image")
    assert _fetch_bytes(_START + "asset.png") == b"image"
    assert scenario.consumed == [_START + "asset.png", normalized_variant]


class _BrowserRequest:
    def __init__(self, url: str, resource_type: str):
        self.url = url
        self.resource_type = resource_type


class _BrowserRoute:
    def __init__(self, page, url: str, resource_type: str):
        self._page = page
        self.request = _BrowserRequest(url, resource_type)
        self.aborted = False
        self.continued = False

    def abort(self):
        self.aborted = True
        self._page.blocked.append(self.request.url)

    def continue_(self):
        self.continued = True
        self._page.consumed.append(self.request.url)


class _BrowserResponse:
    status = 200
    headers = {}


class _BrowserPage:
    def __init__(self, navigation_chain, subresources=()):
        self.navigation_chain = list(navigation_chain)
        self.subresources = list(subresources)
        self.url = self.navigation_chain[0]
        self.handler = None
        self.consumed: list[str] = []
        self.blocked: list[str] = []
        self.closed = False
        self._resources_dispatched = False

    def set_default_timeout(self, _value):
        pass

    def set_default_navigation_timeout(self, _value):
        pass

    def route(self, _pattern, handler):
        self.handler = handler

    def on(self, _event, _handler):
        pass

    def _dispatch(self, url, resource_type):
        route = _BrowserRoute(self, url, resource_type)
        assert self.handler is not None
        self.handler(route)
        return route

    def goto(self, _url, wait_until=None):
        from playwright.sync_api import Error as PlaywrightError

        for destination in self.navigation_chain:
            route = self._dispatch(destination, "document")
            if route.aborted:
                raise PlaywrightError("net::ERR_FAILED")
            self.url = destination
        return _BrowserResponse()

    def wait_for_load_state(self, _state, timeout=None):
        if self._resources_dispatched:
            return
        self._resources_dispatched = True
        for url, resource_type in self.subresources:
            self._dispatch(url, resource_type)

    def content(self):
        return "<html><body>" + ("safe rendered content " * 8) + "</body></html>"

    def evaluate(self, script, *args):
        if "document.body" in str(script):
            return "safe rendered content " * 8
        return None

    def title(self):
        return "Synthetic public page"

    def screenshot(self, **_kwargs):
        return b"png"

    def close(self):
        self.closed = True


class _BrowserContext:
    def __init__(self, page):
        self.page = page
        self.new_page_calls = 0

    def new_page(self):
        self.new_page_calls += 1
        return self.page


def _browser_fetch(navigation_chain, subresources=()):
    from scraper.fetcher import _fetch_page_once

    page = _BrowserPage(navigation_chain, subresources)
    context = _BrowserContext(page)
    result = _fetch_page_once(context, navigation_chain[0], light=False, two_phase=False)
    return result, page, context


def test_browser_final_redirect_is_blocked_before_destination_is_consumed(fake_dns):
    result, page, _context = _browser_fetch([_START, _BLOCKED])
    assert result.error_code is ErrorCode.BLOCKED_URL
    assert _BLOCKED in page.blocked
    assert _BLOCKED not in page.consumed


def test_browser_intermediate_redirect_is_blocked_even_if_final_would_be_public(fake_dns):
    result, page, _context = _browser_fetch([_START, _MIDDLE, _BLOCKED, _SAFE_FINAL])
    assert result.error_code is ErrorCode.BLOCKED_URL
    assert page.consumed == [_START, _MIDDLE]
    assert _BLOCKED in page.blocked
    assert _SAFE_FINAL not in page.consumed


@pytest.mark.parametrize("resource_type", ["image", "script", "xhr", "stylesheet", "document"])
def test_browser_unsafe_subresource_or_frame_is_blocked(fake_dns, resource_type):
    result, page, _context = _browser_fetch([_START], [(_BLOCKED, resource_type)])
    assert result.ok
    assert _BLOCKED in page.blocked
    assert _BLOCKED not in page.consumed


def test_browser_public_redirect_and_cdn_subresource_controls_remain_allowed(fake_dns):
    cdn_asset = "https://cdn.public.invalid/brand/logo.png"
    result, page, _context = _browser_fetch(
        [_START, _SAFE_FINAL], [(cdn_asset, "image")]
    )
    assert result.ok
    assert page.blocked == []
    assert page.consumed == [_START, _SAFE_FINAL, cdn_asset]


def test_browser_internal_data_and_blob_resources_remain_allowed(fake_dns):
    result, page, _context = _browser_fetch(
        [_START], [("data:image/png;base64,AA==", "image"),
                   ("blob:https://start.public.invalid/id", "image"),
                   ("about:blank", "document")]
    )
    assert result.ok
    assert page.blocked == []


@pytest.mark.parametrize("destination", ["file:///synthetic/local", "ftp://files.invalid/a"])
def test_browser_external_non_http_schemes_are_rejected_if_intercepted(fake_dns, destination):
    result, page, _context = _browser_fetch([_START], [(destination, "document")])
    assert result.ok
    assert destination in page.blocked
    assert destination not in page.consumed


def test_browser_fails_closed_when_request_interceptor_cannot_be_installed(fake_dns):
    from scraper.fetcher import _fetch_page_once

    class _NoInterceptorPage(_BrowserPage):
        def route(self, _pattern, _handler):
            raise RuntimeError("route unavailable")

    page = _NoInterceptorPage([_START])
    context = _BrowserContext(page)
    result = _fetch_page_once(context, _START)
    assert result.error_code is ErrorCode.BLOCKED_URL
    assert page.consumed == []
    assert page.closed is True


def test_existing_initial_unsafe_url_rejection_still_precedes_browser_creation(fake_dns):
    page = _BrowserPage([_BLOCKED])
    context = _BrowserContext(page)
    from scraper.fetcher import _fetch_page_once

    result = _fetch_page_once(context, _BLOCKED)
    assert result.error_code is ErrorCode.BLOCKED_URL
    assert context.new_page_calls == 0
