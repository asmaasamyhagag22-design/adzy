# -*- coding: utf-8 -*-
"""A site that declares an Arabic version must not be reported as single-language.

THE DEFECT, measured end to end on the owner's own client (alfouadpharmacies.com, 2026-07-27):

    <link rel="alternate" hreflang="ar" ...>   present on ALL 41 crawled pages
    the word العربية (the switcher label)       present on ALL 41 pages
    URLs the crawler actually visited           41, of which /ar = ZERO
    profile.languages                          ['en']
    SWOT site_readiness shipped to the client  "Single-language site (missing Arabic/English)"

`grep -rn hreflang --include=*.py` over the whole repo returned NOTHING. The site states its own
answer in a machine-readable standard tag, the scraper parsed past it 41 times, and the analysis
then told the client something false about their own website.

This is the worst class of error this project can make: not a missing feature, a CONFIDENT WRONG
STATEMENT about the customer's business, contradicted by the customer's own markup.

The character-count I used to "verify" it earlier is exactly the wrong instrument — it measures
WHAT WAS CRAWLED, not what exists. It said 5.5% Arabic and I read that as evidence about the site;
it was evidence about the crawl.

`_is_bilingual` therefore stops guessing from crawled text alone: a declared alternate is the
site's own answer and outranks a character count. When neither is available the answer stays None —
UNKNOWN, never False — because "we did not find it" and "it is not there" are different claims.
"""
from __future__ import annotations

import pytest


def test_a_declared_arabic_alternate_makes_the_site_bilingual():
    from competitor.matrix import _is_bilingual
    assert _is_bilingual(["en"], declared=["ar", "en", "x-default"]) is True, (
        "the site declares hreflang=ar on every page; reporting it single-language is a false "
        "statement about the client's own site")


def test_the_declaration_outranks_a_crawl_that_only_saw_english():
    """The crawler followed no Arabic URL, so the crawled languages are ['en'] — which is a fact
    about the crawl, not about the site."""
    from competitor.matrix import _is_bilingual
    assert _is_bilingual(["en"], declared=["en", "ar"]) is True


def test_crawled_languages_still_work_when_nothing_is_declared():
    from competitor.matrix import _is_bilingual
    assert _is_bilingual(["ar", "en"]) is True
    assert _is_bilingual(["en", "fr"]) is False


def test_no_evidence_at_all_is_unknown_not_false():
    """A brand nobody could measure must not be marked as failing the dimension — that is the
    'absence read as a finding' failure this project keeps paying for."""
    from competitor.matrix import _is_bilingual
    assert _is_bilingual([], declared=[]) is None
    assert _is_bilingual(None, declared=None) is None


def test_the_extractor_captures_declared_alternates():
    """Nothing in the repo read hreflang. The scrape must carry it, or the analysis has nothing
    better than crawled text to reason from."""
    from scraper.extractors.language import declared_languages
    html = ('<html lang="en"><head>'
            '<link rel="alternate" hreflang="ar" href="https://x.test/ar/">'
            '<link rel="alternate" hreflang="en" href="https://x.test/en/">'
            '<link rel="alternate" hreflang="x-default" href="https://x.test/">'
            '</head><body>hi</body></html>')
    assert declared_languages(html) == ["ar", "en"], "hreflang alternates were not captured"


def test_x_default_is_not_a_language():
    from scraper.extractors.language import declared_languages
    html = '<link rel="alternate" hreflang="x-default" href="/">'
    assert declared_languages(html) == []


def test_a_page_with_no_alternates_declares_nothing():
    from scraper.extractors.language import declared_languages
    assert declared_languages("<html lang='en'><body>x</body></html>") == []
    assert declared_languages("") == []


@pytest.mark.parametrize("region_tag,expected", [
    ("ar-EG", ["ar"]),
    ("en-GB", ["en"]),
])
def test_region_tags_reduce_to_the_language(region_tag, expected):
    """hreflang carries region subtags; the dimension is about language."""
    from scraper.extractors.language import declared_languages
    html = f'<link rel="alternate" hreflang="{region_tag}" href="/x">'
    assert declared_languages(html) == expected
