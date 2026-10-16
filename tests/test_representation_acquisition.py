"""F-06 client-side half: the browser locale must FOLLOW a representation, never choose one.

B2 removed `Accept-Language`, which closed server-side negotiation. It did not close the other
channel: Playwright's context `locale` also drives `navigator.language`, and a locale-adaptive
site reads that and routes itself. MEASURED 2026-08-12 against a local JS-adaptive origin — with
`Accept-Language` absent, `locale=en-US` still landed the crawl on `/en`, `ar-EG` on `/ar`,
`de-DE` on `/de`. The crawler was still picking the language it then measured.

So one preference-free HTTP GET happens first, the server picks, and the browser is told to
render THAT. When the server's answer names no language, the locale is a declared RENDERING
FALLBACK — a technical necessity that must never be read as evidence about the brand.

Hermetic: a fake transport stands in for the origin. No socket, no browser, no provider.
"""
from __future__ import annotations

import pytest

from scraper.representation import (
    RENDER_FALLBACK_LOCALE,
    RepresentationProbe,
    probe_neutral_representation,
    render_locale_for,
)


class _Response:
    def __init__(self, body: str, *, url: str, headers: dict | None = None):
        self._body = body.encode("utf-8")
        self.url = url
        self.headers = headers or {}

    def read(self, n=None):
        return self._body if n is None else self._body[:n]

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


def _opener_for(body: str, *, url: str = "https://site.public.invalid/",
                headers: dict | None = None):
    seen = []

    def _open(request, *, timeout=None, context=None):
        seen.append(dict(getattr(request, "headers", {}) or {}))
        return _Response(body, url=url, headers=headers)

    _open.seen = seen
    return _open


_ARABIC_DEFAULT = """<!doctype html><html lang="ar"><head>
<link rel="alternate" hreflang="en" href="/en">
<link rel="alternate" hreflang="ar" href="/">
<link rel="alternate" hreflang="x-default" href="/">
</head><body><h1>محتوى عربي</h1></body></html>"""

_SELECTOR = """<!doctype html><html><head>
<link rel="alternate" hreflang="en" href="/en">
<link rel="alternate" hreflang="ar" href="/ar">
<link rel="alternate" hreflang="x-default" href="/">
</head><body><p>choose a language</p></body></html>"""

_ENGLISH_ONLY = """<!doctype html><html lang="en"><body><h1>An agency</h1></body></html>"""


# ---------------------------------------------------------------------------
# The probe expresses no preference
# ---------------------------------------------------------------------------

def test_the_probe_sends_no_language_preference():
    opener = _opener_for(_ARABIC_DEFAULT)
    probe_neutral_representation("https://site.public.invalid/", opener=opener)
    sent = opener.seen[0]
    assert not any(k.lower() == "accept-language" for k in sent), (
        "the probe expressed a preference, so its answer would not be the server's own choice")


def test_the_probe_reads_the_language_the_server_chose():
    probe = probe_neutral_representation(
        "https://site.public.invalid/", opener=_opener_for(_ARABIC_DEFAULT))
    assert probe.observed_language == "ar"
    assert probe.language_is_evidence is True
    assert probe.is_language_selector is False


def test_a_failed_probe_is_unknown_not_a_guess():
    def _boom(request, *, timeout=None, context=None):
        raise OSError("origin unreachable")

    probe = probe_neutral_representation("https://site.public.invalid/", opener=_boom)
    assert probe.observed_language == ""
    assert probe.language_is_evidence is False
    assert probe.error


# ---------------------------------------------------------------------------
# The locale follows the representation
# ---------------------------------------------------------------------------

def test_the_render_locale_follows_an_evidenced_representation():
    probe = probe_neutral_representation(
        "https://site.public.invalid/", opener=_opener_for(_ARABIC_DEFAULT))
    locale, provenance = render_locale_for(probe)
    assert locale.lower().startswith("ar")
    assert provenance == "evidence"


def test_an_english_default_yields_an_english_render_locale():
    probe = probe_neutral_representation(
        "https://site.public.invalid/", opener=_opener_for(_ENGLISH_ONLY))
    locale, provenance = render_locale_for(probe)
    assert locale.lower().startswith("en")
    assert provenance == "evidence"


def test_a_non_arabic_third_language_works_the_same_way():
    """The contract is generic; nothing here is an Arabic/English special case."""
    german = """<!doctype html><html lang="de-AT"><body><p>Hallo</p></body></html>"""
    probe = probe_neutral_representation(
        "https://site.public.invalid/", opener=_opener_for(german))
    locale, provenance = render_locale_for(probe)
    assert probe.observed_language == "de"
    assert locale == "de-AT", "the region the representation stated should be preserved"
    assert provenance == "evidence"


# ---------------------------------------------------------------------------
# §5 — a rendering fallback is not evidence
# ---------------------------------------------------------------------------

def test_a_language_selector_page_is_ambiguous_not_english():
    probe = probe_neutral_representation(
        "https://site.public.invalid/", opener=_opener_for(_SELECTOR))
    assert probe.observed_language == ""
    assert probe.is_language_selector is True
    assert probe.language_is_evidence is False


def test_the_fallback_locale_is_declared_as_a_fallback():
    probe = probe_neutral_representation(
        "https://site.public.invalid/", opener=_opener_for(_SELECTOR))
    locale, provenance = render_locale_for(probe)
    assert locale == RENDER_FALLBACK_LOCALE
    assert provenance == "fallback", (
        "a technical rendering choice was reported as evidence about the brand")


def test_no_probe_at_all_still_declares_a_fallback():
    locale, provenance = render_locale_for(None)
    assert locale == RENDER_FALLBACK_LOCALE
    assert provenance == "fallback"


def test_the_fallback_locale_is_deterministic_not_host_derived(monkeypatch):
    for var in ("LANG", "LC_ALL", "LANGUAGE"):
        monkeypatch.setenv(var, "ar_EG.UTF-8")
    assert render_locale_for(None) == (RENDER_FALLBACK_LOCALE, "fallback")


# ---------------------------------------------------------------------------
# §8 / §10 — hreflang and x-default are kept as evidence, not as a language
# ---------------------------------------------------------------------------

def test_hreflang_is_kept_as_a_tag_to_url_mapping_with_regions():
    """Regions stay distinct keys; only the CASE is normalised (BCP-47 is case-insensitive)."""
    body = """<!doctype html><html lang="en"><head>
    <link rel="alternate" hreflang="en-US" href="/us/en">
    <link rel="alternate" hreflang="en-GB" href="/uk/en">
    <link rel="alternate" hreflang="ar" href="/ar">
    <link rel="alternate" hreflang="x-default" href="/">
    </head><body>x</body></html>"""
    probe = probe_neutral_representation("https://site.public.invalid/page",
                                         opener=_opener_for(body))
    assert probe.hreflang["en-us"] == "https://site.public.invalid/us/en"
    assert probe.hreflang["en-gb"] == "https://site.public.invalid/uk/en"
    assert probe.hreflang["ar"] == "https://site.public.invalid/ar"
    assert "x-default" not in probe.hreflang, "x-default is a route, not a language"


def test_x_default_is_stored_as_a_url_relation_only():
    probe = probe_neutral_representation(
        "https://site.public.invalid/", opener=_opener_for(_SELECTOR))
    assert probe.x_default_url == "https://site.public.invalid/"
    assert probe.observed_language == "", "x-default must not be resolved into a language"


def test_hreflang_order_is_not_preference():
    """Declaring Arabic first says nothing about which language the brand markets in."""
    arabic_first = """<!doctype html><html lang="en"><head>
    <link rel="alternate" hreflang="ar" href="/ar">
    <link rel="alternate" hreflang="en" href="/en">
    </head><body>x</body></html>"""
    probe = probe_neutral_representation("https://site.public.invalid/",
                                         opener=_opener_for(arabic_first))
    assert probe.observed_language == "en", "the first declared tag overrode the served language"
    assert render_locale_for(probe)[1] == "evidence"


# ---------------------------------------------------------------------------
# §12 — content negotiation provenance
# ---------------------------------------------------------------------------

def test_a_negotiating_server_is_recorded_as_such():
    probe = probe_neutral_representation(
        "https://site.public.invalid/",
        opener=_opener_for(_ARABIC_DEFAULT, headers={"Vary": "Accept-Encoding, Accept-Language"}))
    assert probe.vary_accept_language is True


def test_content_language_is_used_only_when_the_markup_says_nothing():
    no_lang = """<!doctype html><html><body><p>hello</p></body></html>"""
    probe = probe_neutral_representation(
        "https://site.public.invalid/",
        opener=_opener_for(no_lang, headers={"Content-Language": "ar"}))
    assert probe.observed_language == "ar"

    # Markup wins when both are present: a stale server default must not override the page.
    conflicting = probe_neutral_representation(
        "https://site.public.invalid/",
        opener=_opener_for(_ENGLISH_ONLY, headers={"Content-Language": "ar"}))
    assert conflicting.observed_language == "en"


# ---------------------------------------------------------------------------
# §11 — country still cannot reach the language chain
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("country", ["Egypt", "Qatar", "Saudi Arabia"])
def test_market_country_is_absent_from_representation_selection(country):
    """The probe has no country input at all — the strongest form of this guarantee."""
    probe = probe_neutral_representation(
        "https://site.public.invalid/", opener=_opener_for(_ENGLISH_ONLY))
    assert probe.observed_language == "en"
    from poster.locale import content_language_of
    assert content_language_of({"languages": [{"code": probe.observed_language}],
                                "locations": [{"country": country}]}) == "en"


def test_the_probe_signature_accepts_no_country_or_locale_input():
    import inspect
    params = set(inspect.signature(probe_neutral_representation).parameters)
    assert params == {"url", "timeout", "opener"}, (
        "a country/locale input here would reopen exactly the bias this closes")


# ---------------------------------------------------------------------------
# §18 — the bootstrap stays behind the F-01 guarded transport
# ---------------------------------------------------------------------------

def test_the_bootstrap_uses_the_shared_guarded_transport_by_default():
    """No second URL-safety policy: an unsafe destination is refused by the shared one."""
    import scraper.representation as rep

    calls = []
    real = rep.open_public_url

    def spy(request, *, timeout=None, context=None):
        calls.append(getattr(request, "full_url", request))
        return real(request, timeout=timeout, context=context)

    rep.open_public_url = spy
    try:
        probe = rep.probe_neutral_representation("http://169.254.169.254/latest/meta-data/")
    finally:
        rep.open_public_url = real

    assert calls, "the bootstrap bypassed the guarded transport entirely"
    assert probe.error, "an unsafe bootstrap destination was not refused"
    assert probe.observed_language == ""


def test_an_unsafe_destination_yields_no_language_and_no_crash():
    for url in ("http://127.0.0.1/admin", "http://10.0.0.7/internal", "file:///etc/passwd"):
        probe = probe_neutral_representation(url)
        assert probe.observed_language == ""
        assert render_locale_for(probe)[1] == "fallback"
