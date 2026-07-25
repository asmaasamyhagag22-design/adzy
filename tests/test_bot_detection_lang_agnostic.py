"""LANGUAGE-INDEPENDENT bot/WAF detection (P-63).

The old detector matched only English display phrases over text[:5000], so a Cloudflare/WAF
challenge served with HTTP 200 in a non-English locale (Arabic/Turkish/French) was saved as if it
were the brand's homepage — a D-8 hole for the any-brand-any-country scope. These tests pin the
new language-independent paths: challenge-JS HTML fingerprints, WAF response headers, and a
block-status (403/429/503) returned with almost no visible text — none of which depend on wording.
"""
from __future__ import annotations

from scraper.errors import ErrorCode
from scraper.fetcher import _detect_bot_protection


def test_cloudflare_challenge_html_marker_caught_regardless_of_language():
    # A 200 challenge page whose visible text is Arabic (not in the English signal list) but whose
    # HTML bootstraps the Cloudflare challenge platform.
    html = '<html><head><script src="/cdn-cgi/challenge-platform/h/b/orchestrate/chl/v1"></script></head><body>جارٍ التحقق</body></html>'
    assert _detect_bot_protection("جارٍ التحقق من متصفحك", "لحظة", http_status=200, html=html) is ErrorCode.BOT_PROTECTION


def test_waf_header_marker_caught():
    assert _detect_bot_protection("", "", http_status=403, html="",
                                  headers={"cf-mitigated": "challenge"}) is ErrorCode.BOT_PROTECTION


def test_block_status_with_empty_body_caught_in_any_language():
    """403/503 with a near-empty body is a block regardless of language.

    429 WAS ASSERTED HERE AND HAS BEEN MOVED OUT, with the measurement. A 429 is a rate limit —
    "slower", not "no" — and treating it as a permanent block told the owner that
    alfouadpharmacies.com refuses crawlers when the same URL returns HTTP 200 and 1.3 MB over an
    ordinary connection. It also skipped the 429-specific patience budget this project already
    built (`_is_rate_limited` + `HOMEPAGE_RATE_LIMIT_RETRIES`). The Turkish phrase still proves the
    language-independence this file exists for — via a status that really does mean refusal.
    See tests/test_http2_workaround_is_not_global.py."""
    assert _detect_bot_protection("Erişim engellendi", "", http_status=403, html="") is ErrorCode.BOT_PROTECTION
    assert _detect_bot_protection("", "", http_status=503, html="") is ErrorCode.BOT_PROTECTION


def test_non_english_text_phrase_caught():
    assert _detect_bot_protection("insan olduğunuzu doğrulayın", "") is ErrorCode.BOT_PROTECTION


def test_real_page_with_200_and_content_is_not_flagged():
    # A genuine homepage: 200, real content, no WAF markers — must NOT be flagged.
    text = "Qasr ElKbabgi — the largest grill palace in Egypt. Branches in Madinaty and Sheikh Zayed."
    html = f"<html><body><h1>Qasr ElKbabgi</h1><p>{text}</p></body></html>"
    assert _detect_bot_protection(text, "Qasr ElKbabgi", http_status=200, html=html) is None


def test_real_page_that_happens_to_return_503_but_has_full_content_is_not_a_bot_wall():
    # Defensive: a 503 with substantial real text (rare maintenance page with content) is NOT
    # flagged as a bot wall by the status path (which requires an empty body).
    text = "We are performing scheduled maintenance. Our full menu and store hours are below. " * 3
    assert _detect_bot_protection(text, "Maintenance", http_status=503, html="") is None
