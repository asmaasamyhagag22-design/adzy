# -*- coding: utf-8 -*-
"""A page we failed to render is not the same thing as a site that refused us.

MEASURED (alfouadpharmacies.com, 2026-07-25). The run died with:

    !! الموقع ده بيمنع الزواحف عمداً (حماية بوت) — مش عطل عندنا ومش هيتفتح بإعادة المحاولة.
       code=BOT_PROTECTION  message="Bot protection detected (title='')"

The owner's reaction was the right one: «انا كنت بسكراب الفوائد عادي جدا ... شوف الاسكرابر جراله
ايه». Fetching the same URL with the same code minutes later:

    http_status 200 · error_code None · html 1,328,886 chars · rendered 5,570 chars
    title 'صيدلية الفؤاد | أفضل صيدلية أونلاين في مصر للعناية والجمال'
    challenge markers hit: []      vendor markers hit: []

So the site never blocked anything. The fetch failed transiently — and the verdict we printed was
`BOT_PROTECTION`, whose whole message tells the user this is PERMANENT and that retrying is
pointless: "مش هيتفتح بإعادة المحاولة". We told her to give up on a site that works.

THE DEFECT IS THE INFERENCE, NOT THE DETECTOR. Every positive signal was absent — no challenge
marker, no vendor marker, no WAF header, no block status. What was present was NOTHING AT ALL: an
empty title and empty text. Emptiness is not evidence of refusal; it is the absence of evidence,
and the two must not produce the same verdict. `BOT_PROTECTION` is a claim about the SITE'S
INTENT, and intent needs a positive marker.

`title=''` in the message was the tell, and it is in the message precisely so this is visible.

D-8: nothing here is site-specific. An empty render with no refusal marker is "we got nothing",
for any site in any country.
"""
from __future__ import annotations

from scraper.fetcher import _detect_bot_protection
from scraper.schemas import ErrorCode


# --- an empty render with NO refusal marker is not a block ---------------------------------------

def test_an_empty_render_with_no_marker_is_not_called_bot_protection():
    """The exact shape that misfired: nothing rendered, no title, HTTP 200, no markers."""
    assert _detect_bot_protection("", "", http_status=200, html="", headers={}) is None


def test_an_empty_render_on_a_200_with_ordinary_html_is_not_a_block():
    html = "<html><head></head><body><div id='app'></div></body></html>"
    assert _detect_bot_protection("", "", http_status=200, html=html, headers={}) is None


def test_a_timeout_shaped_result_is_not_a_block():
    """A fetch that timed out mid-navigation leaves partial HTML and no text."""
    assert _detect_bot_protection("   ", "", http_status=None,
                                  html="<html><body></body></html>", headers={}) is None


# --- everything that IS a real block must still be caught ----------------------------------------

def test_a_challenge_marker_still_reports_bot_protection():
    from scraper.config import BOT_CHALLENGE_MARKERS
    marker = sorted(BOT_CHALLENGE_MARKERS)[0]
    assert _detect_bot_protection("", "", http_status=200,
                                  html=f"<html>{marker}</html>") is ErrorCode.BOT_PROTECTION


def test_a_vendor_marker_on_an_interstitial_still_reports_bot_protection():
    from scraper.config import BOT_VENDOR_MARKERS
    marker = sorted(BOT_VENDOR_MARKERS)[0]
    assert _detect_bot_protection("", "", http_status=200,
                                  html=f"<html>{marker}</html>") is ErrorCode.BOT_PROTECTION


def test_a_block_status_with_no_text_still_reports_bot_protection():
    """429 WAS ASSERTED HERE AND HAS BEEN REMOVED, with the reason.

    This file was written before the A/B that identified the real cause. It listed 429 alongside
    403/503 as "a block status", and that is precisely the conflation that told the owner her
    working site was permanently closed to us: 429 means "slower", not "no". The narrower rule and
    its measurement live in `test_http2_workaround_is_not_global.py`; 403 and 503 keep their
    meaning here."""
    assert _detect_bot_protection("", "", http_status=403, html="") is ErrorCode.BOT_PROTECTION
    assert _detect_bot_protection("", "", http_status=503, html="") is ErrorCode.BOT_PROTECTION


def test_a_cloudflare_challenge_header_still_reports_bot_protection():
    assert _detect_bot_protection("", "", http_status=200, html="",
                                  headers={"cf-mitigated": "challenge"}) is ErrorCode.BOT_PROTECTION


def test_a_captcha_still_wins():
    assert _detect_bot_protection("please solve the captcha", "",
                                  http_status=200) is ErrorCode.CAPTCHA_DETECTED


def test_a_vendor_marker_on_a_FULLY_SERVED_page_is_still_not_a_block():
    """The pepsi.com lesson, re-locked: a protected site serving a real page is not refusing us."""
    from scraper.config import BOT_VENDOR_MARKERS
    marker = sorted(BOT_VENDOR_MARKERS)[0]
    body = "Pepsi Official Site. " * 40
    assert _detect_bot_protection(body, "Pepsi Official Site", http_status=200,
                                  html=f"<html>{marker}{body}</html>") is None
