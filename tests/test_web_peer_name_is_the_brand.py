# -*- coding: utf-8 -*-
"""A competitor's NAME must be the brand, not the SEO title that ranked.

MEASURED (nanocare, 2026-07-21): the deliverable listed these as competitors —

    "أفضل مكمل غذائي في مصر 2026"        (actually inshapesupplements.com)
    "متجر مكملات غذائية في مصر"           (actually beseha.com)
    "Supplements Online Egypt – Authentic Sports Nutrition"   (actually ifit-eg.com)

The businesses are real; the NAMES are search-engine page titles. `_clean_title` takes the text
before the first separator, which on an SEO-optimised page is the keyword phrase, not the brand.
A client reading "أفضل مكمل غذائي في مصر 2026" as a competitor sees a broken system.

The rule keys on the DOMAIN, not a banned-words list, so it works in any language: prefer the
title segment that shares a token with the registrable domain; failing that, prettify the domain
itself. Hermetic."""
from __future__ import annotations

from competitor.web_discovery import _clean_title


def test_the_segment_matching_the_domain_wins():
    got = _clean_title("أفضل مكمل غذائي في مصر 2026 | InShape Supplements",
                       "www.inshapesupplements.com")
    assert "InShape" in got
    assert "أفضل" not in got


def test_an_seo_only_title_falls_back_to_the_domain():
    got = _clean_title("متجر مكملات غذائية في مصر", "beseha.com")
    assert "مكملات" not in got
    assert "beseha" in got.lower()


def test_a_real_brand_first_is_left_alone():
    assert _clean_title("EgySupps | Sports Nutrition", "egysupps.com") == "EgySupps"


def test_a_hyphenated_domain_still_matches():
    got = _clean_title("Supplements Online Egypt – iFit EG", "ifit-eg.com")
    assert "iFit" in got


def test_no_title_uses_the_domain():
    assert "beseha" in _clean_title("", "beseha.com").lower()


def test_no_title_and_no_host_is_still_safe():
    assert _clean_title("", None) == "competitor"
