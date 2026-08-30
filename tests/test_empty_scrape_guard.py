"""EMPTY-SCRAPE GUARD (P-61) — a page whose visible text is below MIN_RENDERED_CHARS must be
flagged EMPTY_RENDERED_DOM regardless of its <body> tag.

Root cause it fixes: the old guard was `not rendered_text.strip() and "<body" not in html.lower()`.
Almost every HTML document contains "<body", so the second clause was ~always False and the guard
never fired — a failed-SPA / JS-wall / blank-shell page (marasim: 0 clean chars) came back ok=True
and the crawler counted it as pages_succeeded. Now the check is on visible-text length.

Both the primary empty-DOM check and the timeout-salvage path share the MIN_RENDERED_CHARS floor;
these tests exercise the salvage decision (a pure function) and the threshold directly, hermetically.
"""
from __future__ import annotations

from types import SimpleNamespace

from scraper.config import MIN_RENDERED_CHARS
from scraper.fetcher import _salvage_partial_dom


class _FakePage:
    def __init__(self, url, html, text):
        self._url = url
        self._html = html
        self._text = text

    @property
    def url(self):
        return self._url

    def content(self):
        return self._html

    def evaluate(self, _js):
        return self._text

    def screenshot(self, **_k):
        return b"\x89PNG"


def _result():
    return SimpleNamespace(final_url="", html="", rendered_text="",
                           full_screenshot_bytes=None, viewport_screenshot_bytes=None,
                           partial_content=False, partial_reason="", content_sha256="",
                           bytes_downloaded=0)


def test_salvage_rejects_empty_shell_with_body_tag():
    # The exact P-61 shape: a <body> tag is present but there is no visible text. Old code returned
    # True (html non-empty); now it must return False so the page is NOT salvaged as usable.
    page = _FakePage("https://x.com/", "<html><body></body></html>", "")
    assert _salvage_partial_dom(page, _result(), "timeout") is False


def test_salvage_rejects_whitespace_only_text():
    page = _FakePage("https://x.com/", "<html><body>   \n  </body></html>", "   \n  ")
    assert _salvage_partial_dom(page, _result(), "timeout") is False


def test_salvage_keeps_real_content():
    text = "Welcome to Acme Coffee. We roast specialty beans daily in Cairo. Order online now."
    assert len(text.strip()) >= MIN_RENDERED_CHARS
    page = _FakePage("https://x.com/", f"<html><body><p>{text}</p></body></html>", text)
    r = _result()
    assert _salvage_partial_dom(page, r, "timeout") is True
    assert r.partial_content is True
