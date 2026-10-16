"""Tests for scraper.sitemap — parsing, filtering, capping, recursion.

Pure unit tests. No network. We monkeypatch `urlopen` to return synthetic
sitemap bodies, then assert the parser produces the right shape.
"""
from __future__ import annotations

import gzip
import io
import sys
import types
from unittest.mock import patch

# Stub playwright before any project imports (same pattern as other tests).
_fake = types.ModuleType("playwright")
_fake_sync = types.ModuleType("playwright.sync_api")
class _Stub: ...
for _n in ("Browser", "BrowserContext", "Page", "sync_playwright"):
    setattr(_fake_sync, _n, _Stub)
_fake_sync.TimeoutError = type("TimeoutError", (Exception,), {})
_fake_sync.Error = type("Error", (Exception,), {})
sys.modules.setdefault("playwright", _fake)
sys.modules.setdefault("playwright.sync_api", _fake_sync)

from scraper.schemas import RobotsRecord  # noqa: E402
from scraper.sitemap import (  # noqa: E402
    MAX_SITEMAP_URLS,
    discover_sitemap_urls,
    extract_sitemap_urls_from_robots,
)


# ---------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------

class _FakeResponse:
    """Minimal response object compatible with `with urlopen(req) as r:` usage."""

    def __init__(self, body: bytes, headers: dict | None = None):
        self._body = body
        self.headers = headers or {}

    def read(self, n: int | None = None) -> bytes:
        if n is None:
            return self._body
        return self._body[:n]

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


def _make_urlopen(url_to_response: dict[str, _FakeResponse | Exception]):
    """Return a callable that emulates urllib.request.urlopen against a fixed map."""
    def _fake_urlopen(request, timeout=None):
        url = getattr(request, "full_url", None) or str(request)
        if url not in url_to_response:
            raise OSError(f"unexpected fetch: {url}")
        v = url_to_response[url]
        if isinstance(v, Exception):
            raise v
        return v
    return _fake_urlopen


_HOST = "https://example.com"


def _urlset_xml(urls: list[str]) -> bytes:
    body = '<?xml version="1.0" encoding="UTF-8"?>\n'
    body += '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
    for u in urls:
        body += f"  <url><loc>{u}</loc></url>\n"
    body += "</urlset>\n"
    return body.encode("utf-8")


def _sitemap_index_xml(sitemap_urls: list[str]) -> bytes:
    body = '<?xml version="1.0" encoding="UTF-8"?>\n'
    body += '<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
    for u in sitemap_urls:
        body += f"  <sitemap><loc>{u}</loc></sitemap>\n"
    body += "</sitemapindex>\n"
    return body.encode("utf-8")


def _robots_with_no_sitemap() -> RobotsRecord:
    return RobotsRecord(checked=True, allowed=True, sitemap_urls=[])


def _robots_with_sitemap(sitemap_url: str) -> RobotsRecord:
    return RobotsRecord(
        checked=True, allowed=True,
        sitemap_urls=[sitemap_url],
    )


# ---------------------------------------------------------------------
# robots.txt Sitemap directive extraction
# ---------------------------------------------------------------------

def test_extract_sitemap_urls_from_robots_basic():
    body = (
        "User-agent: *\n"
        "Disallow: /admin\n"
        "Sitemap: https://example.com/sitemap.xml\n"
    )
    urls = extract_sitemap_urls_from_robots(body, "https://example.com/")
    assert urls == ["https://example.com/sitemap.xml"]


def test_extract_sitemap_urls_from_robots_multiple_directives():
    body = (
        "Sitemap: https://example.com/sitemap1.xml\n"
        "User-agent: *\n"
        "Sitemap: https://example.com/sitemap2.xml\n"
        "Disallow:\n"
    )
    urls = extract_sitemap_urls_from_robots(body, "https://example.com/")
    assert urls == [
        "https://example.com/sitemap1.xml",
        "https://example.com/sitemap2.xml",
    ]


def test_extract_sitemap_urls_from_robots_relative_resolves():
    body = "Sitemap: /sitemaps/index.xml\n"
    urls = extract_sitemap_urls_from_robots(body, "https://example.com/")
    assert urls == ["https://example.com/sitemaps/index.xml"]


def test_extract_sitemap_urls_from_robots_case_insensitive_and_dedupes():
    body = (
        "sitemap: https://example.com/a.xml\n"
        "SITEMAP: https://example.com/a.xml\n"   # duplicate, different case
        "  Sitemap:   https://example.com/b.xml  \n"  # whitespace
    )
    urls = extract_sitemap_urls_from_robots(body, "https://example.com/")
    assert urls == ["https://example.com/a.xml", "https://example.com/b.xml"]


def test_extract_sitemap_urls_from_robots_empty():
    assert extract_sitemap_urls_from_robots("", "https://example.com/") == []
    assert extract_sitemap_urls_from_robots(
        "User-agent: *\nDisallow:\n", "https://example.com/"
    ) == []


# ---------------------------------------------------------------------
# urlset parsing
# ---------------------------------------------------------------------

def test_urlset_parses_and_returns_urls():
    sm_url = f"{_HOST}/sitemap.xml"
    fake = _make_urlopen({
        sm_url: _FakeResponse(_urlset_xml([
            f"{_HOST}/menu",
            f"{_HOST}/about",
            f"{_HOST}/contact",
        ])),
    })
    with patch("scraper.sitemap.open_public_url", fake):
        result = discover_sitemap_urls(f"{_HOST}/", _robots_with_sitemap(sm_url))
    assert result.sitemap_used is True
    assert result.sitemap_urls_found == 3
    assert set(result.urls) == {
        f"{_HOST}/menu", f"{_HOST}/about", f"{_HOST}/contact",
    }


def test_urlset_rejects_cross_host_urls():
    sm_url = f"{_HOST}/sitemap.xml"
    fake = _make_urlopen({
        sm_url: _FakeResponse(_urlset_xml([
            f"{_HOST}/menu",
            "https://other-site.com/menu",   # must be filtered out
            f"{_HOST}/contact",
        ])),
    })
    with patch("scraper.sitemap.open_public_url", fake):
        result = discover_sitemap_urls(f"{_HOST}/", _robots_with_sitemap(sm_url))
    # All three were "found" in the raw XML
    assert result.sitemap_urls_found == 3
    # But only same-host survive
    assert set(result.urls) == {f"{_HOST}/menu", f"{_HOST}/contact"}


def test_urlset_dedupes_normalized_urls():
    """Two URL forms that normalize_url collapses must dedupe.

    The crawler's normalize_url treats `?` (empty query) as identical to
    no query, so those two forms must end up as one entry. Trailing
    slashes are NOT collapsed (the crawler treats /menu and /menu/ as
    distinct), so they remain as two entries.
    """
    sm_url = f"{_HOST}/sitemap.xml"
    fake = _make_urlopen({
        sm_url: _FakeResponse(_urlset_xml([
            f"{_HOST}/menu",
            f"{_HOST}/menu?",         # empty query → collapses with /menu
            f"{_HOST}/contact",
            f"{_HOST}/contact",       # exact duplicate → must dedupe
        ])),
    })
    with patch("scraper.sitemap.open_public_url", fake):
        result = discover_sitemap_urls(f"{_HOST}/", _robots_with_sitemap(sm_url))
    # /menu and /menu? collapse → 1; /contact dedupes → 1; total 2
    assert set(result.urls) == {f"{_HOST}/menu", f"{_HOST}/contact"}


def test_urlset_caps_at_max_sitemap_urls():
    sm_url = f"{_HOST}/sitemap.xml"
    huge = [f"{_HOST}/page-{i}" for i in range(MAX_SITEMAP_URLS + 50)]
    fake = _make_urlopen({sm_url: _FakeResponse(_urlset_xml(huge))})
    with patch("scraper.sitemap.open_public_url", fake):
        result = discover_sitemap_urls(f"{_HOST}/", _robots_with_sitemap(sm_url))
    assert len(result.urls) == MAX_SITEMAP_URLS
    assert "url_cap_reached" in result.notes


# ---------------------------------------------------------------------
# sitemap-index (1-level recursion)
# ---------------------------------------------------------------------

def test_sitemap_index_recurses_one_level():
    root_url = f"{_HOST}/sitemap-index.xml"
    child1_url = f"{_HOST}/sitemap-1.xml"
    child2_url = f"{_HOST}/sitemap-2.xml"

    fake = _make_urlopen({
        root_url: _FakeResponse(_sitemap_index_xml([child1_url, child2_url])),
        child1_url: _FakeResponse(_urlset_xml([f"{_HOST}/a", f"{_HOST}/b"])),
        child2_url: _FakeResponse(_urlset_xml([f"{_HOST}/c"])),
    })
    with patch("scraper.sitemap.open_public_url", fake):
        result = discover_sitemap_urls(f"{_HOST}/", _robots_with_sitemap(root_url))
    assert set(result.urls) == {f"{_HOST}/a", f"{_HOST}/b", f"{_HOST}/c"}
    assert result.sitemap_used is True


def test_sitemap_index_does_not_recurse_two_levels():
    """A sitemap-index inside a sitemap-index must not fan out further."""
    root_url = f"{_HOST}/sitemap-index.xml"
    nested_index_url = f"{_HOST}/sitemap-nested-index.xml"
    deep_child_url = f"{_HOST}/sitemap-deep.xml"

    fake = _make_urlopen({
        root_url: _FakeResponse(_sitemap_index_xml([nested_index_url])),
        nested_index_url: _FakeResponse(_sitemap_index_xml([deep_child_url])),
        # If we were (incorrectly) recursing, we'd fetch this. We must not.
        deep_child_url: _FakeResponse(_urlset_xml([f"{_HOST}/deep"])),
    })
    with patch("scraper.sitemap.open_public_url", fake):
        result = discover_sitemap_urls(f"{_HOST}/", _robots_with_sitemap(root_url))
    # No URLs surfaced — the deep level was correctly NOT followed.
    assert result.urls == []
    assert any("sitemap_index_too_deep" in n for n in result.notes)


def test_sitemap_index_filters_cross_host_children():
    root_url = f"{_HOST}/sitemap-index.xml"
    same_host_child = f"{_HOST}/sitemap-1.xml"
    other_host_child = "https://other.com/sitemap-evil.xml"

    fake_responses = {
        root_url: _FakeResponse(_sitemap_index_xml([same_host_child, other_host_child])),
        same_host_child: _FakeResponse(_urlset_xml([f"{_HOST}/legit"])),
    }
    # We do NOT register other_host_child — if discovery tries to fetch it,
    # the fake will raise OSError and the test will fail.
    with patch("scraper.sitemap.open_public_url", _make_urlopen(fake_responses)):
        result = discover_sitemap_urls(f"{_HOST}/", _robots_with_sitemap(root_url))
    assert f"{_HOST}/legit" in result.urls


# ---------------------------------------------------------------------
# Gzip
# ---------------------------------------------------------------------

def test_gzipped_sitemap_decoded():
    sm_url = f"{_HOST}/sitemap.xml.gz"
    raw = _urlset_xml([f"{_HOST}/menu"])
    gz_body = gzip.compress(raw)
    fake = _make_urlopen({sm_url: _FakeResponse(gz_body)})
    with patch("scraper.sitemap.open_public_url", fake):
        result = discover_sitemap_urls(f"{_HOST}/", _robots_with_sitemap(sm_url))
    assert f"{_HOST}/menu" in result.urls


def test_gzipped_via_content_encoding():
    sm_url = f"{_HOST}/sitemap.xml"
    raw = _urlset_xml([f"{_HOST}/about"])
    gz_body = gzip.compress(raw)
    fake = _make_urlopen({
        sm_url: _FakeResponse(gz_body, headers={"Content-Encoding": "gzip"}),
    })
    with patch("scraper.sitemap.open_public_url", fake):
        result = discover_sitemap_urls(f"{_HOST}/", _robots_with_sitemap(sm_url))
    assert f"{_HOST}/about" in result.urls


# ---------------------------------------------------------------------
# Malformed / unreachable
# ---------------------------------------------------------------------

def test_malformed_xml_returns_empty_no_crash():
    sm_url = f"{_HOST}/sitemap.xml"
    fake = _make_urlopen({sm_url: _FakeResponse(b"<not valid xml")})
    with patch("scraper.sitemap.open_public_url", fake):
        result = discover_sitemap_urls(f"{_HOST}/", _robots_with_sitemap(sm_url))
    assert result.urls == []
    assert any("sitemap_invalid" in n for n in result.notes)
    assert result.sitemap_used is False


def test_unreachable_sitemap_returns_empty_no_crash():
    sm_url = f"{_HOST}/sitemap.xml"
    fake = _make_urlopen({sm_url: OSError("connection refused")})
    with patch("scraper.sitemap.open_public_url", fake):
        result = discover_sitemap_urls(f"{_HOST}/", _robots_with_sitemap(sm_url))
    assert result.urls == []
    assert any("sitemap_unreachable" in n for n in result.notes)


def test_no_sitemap_in_robots_falls_back_to_default_path():
    """When robots.txt has no Sitemap: directive, discovery should try /sitemap.xml."""
    default_sm = f"{_HOST}/sitemap.xml"
    fake = _make_urlopen({
        default_sm: _FakeResponse(_urlset_xml([f"{_HOST}/x"])),
    })
    with patch("scraper.sitemap.open_public_url", fake):
        result = discover_sitemap_urls(f"{_HOST}/", _robots_with_no_sitemap())
    assert f"{_HOST}/x" in result.urls
    assert default_sm in result.sitemaps_attempted


def test_neither_robots_sitemap_nor_default_exists():
    """When everything fails, return empty but never raise."""
    default_sm = f"{_HOST}/sitemap.xml"
    fake = _make_urlopen({default_sm: OSError("404")})
    with patch("scraper.sitemap.open_public_url", fake):
        result = discover_sitemap_urls(f"{_HOST}/", _robots_with_no_sitemap())
    assert result.urls == []
    assert result.sitemap_used is False
