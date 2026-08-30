# -*- coding: utf-8 -*-
"""A site that BLOCKS us must be told apart from one that is broken — and say what to do.

MEASURED (pepsi.com, 2026-07-21). The run stopped correctly (0 pages → no hollow profile), but the
message shown was:

    !! could not read this site — 0 pages scraped (code=<ErrorCode.BOT_PROTECTION: 'BOT_PROTECTION'>
    message="Bot protection detected (title='')" page_url='https://www.pepsi.com/'
    occurred_at=datetime.datetime(2026, 7, 21, 14, 26, 41, 511777, tzinfo=datetime.timezone.utc))

Two problems for someone who is not a programmer: the payload is a Python repr, and the three
causes it offers ("blocks crawlers, is down, or has a bad certificate") are very different
situations with very different next steps — while the failure object already SAYS which one it is.

Verified independently: pepsi.com and pepsico.com both return HTTP 403 to an ordinary browser
user-agent, so this is a deliberate block, not our bug and not a temporary glitch. Hermetic."""
from __future__ import annotations

from competitor.full_run import scrape_failure_message


class _F:
    def __init__(self, code, message=""):
        self.code = code
        self.message = message

    def __str__(self):
        return f"<ErrorCode.{self.code}: '{self.code}'> message={self.message!r}"


def test_a_deliberate_block_is_named_as_such():
    msg = scrape_failure_message([_F("BOT_PROTECTION", "Bot protection detected")])
    assert "بيمنع" in msg or "حجب" in msg
    assert "ErrorCode" not in msg and "datetime" not in msg     # no Python repr for the client


def test_a_block_says_what_to_do_next():
    msg = scrape_failure_message([_F("BOT_PROTECTION")])
    assert any(w in msg for w in ("رابط", "فرع", "الرسمي")), msg   # an actionable alternative


def test_rate_limiting_is_still_its_own_case():
    msg = scrape_failure_message([_F("RATE_LIMITED", "429")])
    assert "429" in msg and "استني" in msg
    # A 429 must never be presented as a deliberate block. Checking for the BLOCK wording, not the
    # bare word "بيمنع" — the correct sentence contains it as "مش بيمنعنا" (does NOT block us).
    assert "حماية بوت" not in msg


def test_an_unreachable_site_is_not_called_a_blocker():
    msg = scrape_failure_message([_F("DNS_ERROR", "name resolution failed")])
    assert "حماية بوت" not in msg
    assert "نوصل" in msg                # "مقدرناش نوصل للموقع" — unreachable, not blocking


def test_an_unknown_failure_degrades_without_a_repr():
    msg = scrape_failure_message([_F("SOMETHING_NEW", "odd")])
    assert msg and "ErrorCode" not in msg


def test_no_failures_at_all_still_returns_a_sentence():
    assert scrape_failure_message([])
