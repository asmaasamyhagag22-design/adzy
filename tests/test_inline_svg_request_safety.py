"""F-01: the inline-SVG logo browser is a request sink fed by UNTRUSTED scraped HTML.

`capture_inline_svg_logo` loads a scraped homepage into Chromium via `set_content`. Every
request that HTML provokes is a request WE issue on the target's behalf, so each one has to
clear the same destination policy the primary Playwright fetch path applies — a resource type
alone can never make a destination safe.

Hermetic by construction: DNS is faked, the browser is a fake, and every hostname lives below
the reserved `.invalid` TLD. No socket is opened and no browser is launched.
"""
from __future__ import annotations

import ipaddress
import socket

import pytest

from scraper.inline_svg_logo import _BLOCKED_RESOURCES, _route_request

# The destination policy is shared with `scraper.fetcher`, which imports Playwright.
pytest.importorskip("playwright.sync_api")


_SAFE = "https://cdn.public.invalid/theme.css"
_LOOPBACK = "http://blocked.destination.invalid/admin"
_METADATA = "http://169.254.169.254/latest/meta-data/iam/security-credentials/"
_PRIVATE = "http://10.0.0.7/internal"


def _logo_shaped_png() -> bytes:
    """A transparent capture with a logo-shaped ink fraction, so `_gate` accepts it."""
    import io

    from PIL import Image

    im = Image.new("RGBA", (40, 40), (0, 0, 0, 0))
    for x in range(8):
        for y in range(8):
            im.putpixel((x, y), (20, 30, 40, 255))       # 64/1600 = 4% opaque
    buf = io.BytesIO()
    im.save(buf, format="PNG")
    return buf.getvalue()


@pytest.fixture
def fake_dns(monkeypatch):
    """Resolve only the synthetic `.invalid` names; a literal IP still answers as itself.

    A fixture that mapped every host to a public address would resolve `169.254.169.254` to a
    public IP and quietly prove the opposite of what these tests claim.
    """
    _NAMES = {
        "cdn.public.invalid": "93.184.216.34",
        "blocked.destination.invalid": "127.0.0.1",
    }

    def _resolve(host, port, *args, **kwargs):
        name = str(host).lower()
        try:
            ip = str(ipaddress.ip_address(name))       # a literal IP is its own answer
        except ValueError:
            ip = _NAMES.get(name)
            if ip is None:
                raise socket.gaierror(f"unmapped host in a hermetic test: {name}")
        return [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", (ip, port))]

    monkeypatch.setattr("scraper.url_utils.socket.getaddrinfo", _resolve)


def test_the_fixture_does_not_launder_a_literal_private_ip(fake_dns):
    """Guards the guard: the fake DNS must not turn a metadata address into a public one."""
    from scraper.url_utils import is_safe_public_url

    assert is_safe_public_url(_SAFE) is True
    assert is_safe_public_url(_METADATA) is False
    assert is_safe_public_url(_PRIVATE) is False
    assert is_safe_public_url(_LOOPBACK) is False


class _Request:
    def __init__(self, url: str, resource_type: str):
        self.url = url
        self.resource_type = resource_type


class _Route:
    """The two outcomes Playwright offers a route handler, recorded rather than performed."""

    def __init__(self, url: str, resource_type: str):
        self.request = _Request(url, resource_type)
        self.aborted = False
        self.continued = False

    def abort(self):
        self.aborted = True

    def continue_(self):
        self.continued = True


def _decide(url: str, resource_type: str) -> _Route:
    route = _Route(url, resource_type)
    _route_request(route)
    assert route.aborted != route.continued, "a route must be decided exactly once"
    return route


# ---------------------------------------------------------------------------
# The bypass: a continued resource type is not a safe destination
# ---------------------------------------------------------------------------

# Every Playwright resource type the blocklist does NOT name, i.e. every type the old
# handler continued on trust. Naming them individually is the point: the defect was that
# the type decided, so the regression has to walk the whole continued set.
_CONTINUED_TYPES = (
    "document",          # <iframe src=...>
    "stylesheet",        # <link rel=stylesheet href=...>
    "texttrack",
    "eventsource",
    "websocket",
    "manifest",
    "other",             # <link rel=prefetch>, ping, csp_report, ...
)


@pytest.mark.parametrize("resource_type", _CONTINUED_TYPES)
@pytest.mark.parametrize("url", [_LOOPBACK, _METADATA, _PRIVATE])
def test_an_unsafe_destination_is_aborted_whatever_the_resource_type(fake_dns, resource_type, url):
    route = _decide(url, resource_type)
    assert route.aborted is True, f"{resource_type} reached {url}"
    assert route.continued is False


def test_the_scraped_iframe_cannot_reach_cloud_metadata(fake_dns):
    """The concrete shape: attacker HTML carrying <iframe src='http://169.254.169.254/...'>."""
    assert _decide(_METADATA, "document").aborted is True


def test_the_scraped_stylesheet_cannot_reach_an_internal_host(fake_dns):
    """The concrete shape: attacker HTML carrying <link rel=stylesheet href='http://10.0.0.7/...'>."""
    assert _decide(_PRIVATE, "stylesheet").aborted is True


def test_a_non_http_scheme_is_not_continued_on_trust(fake_dns):
    """file:// and ftp:// are neither public HTTP(S) nor the browser-local set."""
    for url in ("file:///C:/Windows/win.ini", "ftp://blocked.destination.invalid/x"):
        assert _decide(url, "document").aborted is True, url


# ---------------------------------------------------------------------------
# Positive controls: the feature still works
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("resource_type", _CONTINUED_TYPES)
def test_a_safe_public_destination_is_still_continued(fake_dns, resource_type):
    route = _decide(_SAFE, resource_type)
    assert route.continued is True, resource_type
    assert route.aborted is False


@pytest.mark.parametrize("resource_type", _BLOCKED_RESOURCES)
def test_the_existing_resource_policy_still_blocks_its_types_on_a_safe_host(fake_dns, resource_type):
    """The fix adds a destination gate; it does not widen what the capture is allowed to load."""
    assert _decide(_SAFE, resource_type).aborted is True


@pytest.mark.parametrize("url", ["about:blank", "data:text/css,body{}", "blob:https://x/y"])
def test_browser_local_schemes_follow_the_primary_paths_treatment(fake_dns, url):
    """`data:`/`blob:`/`about:` are not network destinations — the same call the fetcher makes."""
    assert _decide(url, "document").continued is True


def test_this_path_shares_the_primary_browser_predicate_rather_than_a_second_policy(fake_dns):
    """One policy, or the two boundaries drift apart the next time either is tuned."""
    from scraper.fetcher import _browser_request_is_allowed

    for url in (_SAFE, _LOOPBACK, _METADATA, _PRIVATE, "about:blank"):
        expected_allowed = _browser_request_is_allowed(url)
        assert _decide(url, "document").continued is expected_allowed, url


# ---------------------------------------------------------------------------
# Redirect continuity
# ---------------------------------------------------------------------------

def test_a_safe_first_hop_does_not_authorize_an_unsafe_redirect_destination(fake_dns):
    """Playwright routes each redirect hop as its own request; the decision must be stateless."""
    first = _decide(_SAFE, "stylesheet")
    assert first.continued is True
    second = _decide(_LOOPBACK, "stylesheet")     # the 302 destination
    assert second.aborted is True, "a safe first hop laundered an unsafe destination"


def test_repeated_decisions_do_not_accumulate_permission(fake_dns):
    for _ in range(3):
        assert _decide(_SAFE, "document").continued is True
    for _ in range(3):
        assert _decide(_METADATA, "document").aborted is True


# ---------------------------------------------------------------------------
# The guard cannot be skipped, and workers cannot step around it
# ---------------------------------------------------------------------------

def test_an_unavailable_destination_policy_fails_closed(monkeypatch):
    """If the shared predicate cannot be consulted, the request is refused, not trusted."""
    import scraper.inline_svg_logo as mod

    def _boom(_url):
        raise RuntimeError("policy unavailable")

    monkeypatch.setattr(mod, "_destination_is_allowed", _boom)
    assert _decide(_SAFE, "document").aborted is True


def test_the_capture_blocks_service_workers_and_guards_before_loading_untrusted_html(monkeypatch):
    """A Service Worker intercepts fetches BENEATH `route`, so the capture context blocks them.

    Drives the real `capture_inline_svg_logo` against a fake Playwright module: no browser is
    launched and no socket is opened, but the production wiring is the code under test.
    """
    pytest.importorskip("playwright.sync_api")
    import playwright.sync_api as pw_api

    import scraper.inline_svg_logo as mod

    seen = {"context_kwargs": None, "routed": None, "order": []}

    logo_png = _logo_shaped_png()

    class _Elem:
        def screenshot(self, **_kw):
            return logo_png

    class _Page:
        def set_content(self, _html, **_kw):
            seen["order"].append("set_content")

        def evaluate(self, _script, *_args):
            return True

        def query_selector(self, _sel):
            return _Elem()

    class _Context:
        def __init__(self, **kwargs):
            seen["context_kwargs"] = kwargs

        def route(self, pattern, handler):
            seen["routed"] = (pattern, handler)
            seen["order"].append("route")

        def new_page(self):
            return _Page()

    class _Browser:
        def new_context(self, **kwargs):
            return _Context(**kwargs)

        def close(self):
            seen["order"].append("close")

    class _PlaywrightSession:
        chromium = type("_Chromium", (), {"launch": lambda self: _Browser()})()

        def __enter__(self):
            return self

        def __exit__(self, *_a):
            return False

    monkeypatch.setattr(pw_api, "sync_playwright", lambda: _PlaywrightSession())

    out = mod.capture_inline_svg_logo("<html><body><svg/></body></html>", 1)

    assert out is not None, "the capture path no longer reaches its processing boundary"
    assert out[0].startswith("data:image/png;base64,")
    assert seen["context_kwargs"] is not None, "the capture still uses an unconfigured default context"
    assert seen["context_kwargs"].get("service_workers") == "block"
    assert seen["routed"] is not None, "no request guard was installed"
    assert seen["routed"][1] is mod._route_request
    assert seen["order"].index("route") < seen["order"].index("set_content"), (
        "untrusted HTML was loaded before the request guard was installed")
