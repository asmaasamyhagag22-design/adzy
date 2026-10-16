# -*- coding: utf-8 -*-
"""CS-48 (2026-07-18): lite_peer_dims validated only the PRE-redirect URL, then urlopen followed a
302 to any host — an SSRF sink (a public URL 302-ing to http://169.254.169.254/ or 127.0.0.1).
The redirect target must be re-validated. Hermetic — exercises the redirect handler directly, no
network."""
from __future__ import annotations

import urllib.request

import pytest


def test_redirect_to_a_private_host_is_refused():
    from scraper.url_utils import SafeRedirectHandler as _SafeRedirectHandler
    h = _SafeRedirectHandler()
    req = urllib.request.Request("https://public.example/start")

    class _FP:  # a minimal fp stand-in
        def read(self, *a):
            return b""
        def close(self):
            pass

    # a 302 to a link-local / loopback target must raise, not follow
    for bad in ("http://169.254.169.254/latest/meta-data/",
                "http://127.0.0.1:8000/admin",
                "http://localhost/",
                "http://10.0.0.5/"):
        with pytest.raises(Exception):
            h.redirect_request(req, _FP(), 302, "Found", {}, bad)


def test_redirect_to_a_public_host_is_allowed():
    from scraper.url_utils import SafeRedirectHandler as _SafeRedirectHandler
    h = _SafeRedirectHandler()
    req = urllib.request.Request("https://public.example/start")

    class _FP:
        def read(self, *a):
            return b""
        def close(self):
            pass

    new = h.redirect_request(req, _FP(), 302, "Found", {}, "https://www.example.com/landing")
    assert new is not None and new.full_url == "https://www.example.com/landing"
