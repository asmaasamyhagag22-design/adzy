"""Tests for robots.txt Sitemap: directive extraction.

PR-1 added populated `RobotsRecord.sitemap_urls` to the existing
load_robots() function. Confirm that population happens without
disturbing the existing behavior (allowed/disallowed/crawl-delay).
"""
from __future__ import annotations

import io
import sys
import types
from unittest.mock import patch

_fake = types.ModuleType("playwright")
_fake_sync = types.ModuleType("playwright.sync_api")
class _Stub: ...
for _n in ("Browser", "BrowserContext", "Page", "sync_playwright"):
    setattr(_fake_sync, _n, _Stub)
_fake_sync.TimeoutError = type("TimeoutError", (Exception,), {})
_fake_sync.Error = type("Error", (Exception,), {})
sys.modules.setdefault("playwright", _fake)
sys.modules.setdefault("playwright.sync_api", _fake_sync)

from scraper.robots import load_robots  # noqa: E402


class _FakeResponse:
    def __init__(self, body: bytes):
        self._body = body
        self.headers = {}

    def read(self, n=None):
        return self._body if n is None else self._body[:n]

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


def _patch_urlopen_with(body: bytes):
    def _fake(req, timeout=None):
        return _FakeResponse(body)
    # This file tests robots parsing, not redirect policy.  The F-01 continuity
    # suite exercises the real guarded opener; here the bounded transport is a fake.
    return patch("scraper.robots.open_public_url", _fake)


def test_robots_extracts_single_sitemap_directive():
    body = (
        b"User-agent: *\n"
        b"Disallow: /admin\n"
        b"Sitemap: https://example.com/sitemap.xml\n"
    )
    with _patch_urlopen_with(body):
        _, record = load_robots("https://example.com/")
    assert record.allowed is True
    assert record.sitemap_urls == ["https://example.com/sitemap.xml"]


def test_robots_extracts_multiple_sitemap_directives():
    body = (
        b"Sitemap: https://example.com/sitemap-1.xml\n"
        b"User-agent: *\n"
        b"Disallow:\n"
        b"Sitemap: https://example.com/sitemap-2.xml\n"
    )
    with _patch_urlopen_with(body):
        _, record = load_robots("https://example.com/")
    assert record.sitemap_urls == [
        "https://example.com/sitemap-1.xml",
        "https://example.com/sitemap-2.xml",
    ]


def test_robots_without_sitemap_directive_keeps_empty_list():
    body = (
        b"User-agent: *\n"
        b"Disallow: /admin\n"
    )
    with _patch_urlopen_with(body):
        _, record = load_robots("https://example.com/")
    assert record.sitemap_urls == []
    # And the existing allowed-check still works
    assert record.allowed is True


def test_robots_unreachable_keeps_empty_sitemap_urls():
    """When robots.txt itself is unreachable, we degrade to 'assume
    allowed' AND sitemap_urls stays an empty list (not None)."""
    def _raise(req, timeout=None):
        raise OSError("connection refused")
    with patch("scraper.robots.open_public_url", _raise):
        _, record = load_robots("https://example.com/")
    assert record.allowed is True  # assume-allowed fallback
    assert record.sitemap_urls == []


def test_robots_relative_sitemap_url_resolves():
    """Some robots.txt files give relative sitemap paths. Our extractor
    must resolve them against the site root."""
    body = b"Sitemap: /sitemaps/index.xml\n"
    with _patch_urlopen_with(body):
        _, record = load_robots("https://example.com/")
    assert record.sitemap_urls == ["https://example.com/sitemaps/index.xml"]
