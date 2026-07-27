# -*- coding: utf-8 -*-
"""The languages a site DECLARES about itself — `<link rel="alternate" hreflang="...">`.

MEASURED 2026-07-27 on alfouadpharmacies.com: `hreflang="ar"` sits on all 41 crawled pages, the
Arabic switcher label (العربية) sits on all 41, and the crawler visited ZERO Arabic URLs. The
profile recorded `languages: ['en']` and the SWOT shipped "Single-language site (missing
Arabic/English)" to the client — about a site that states the opposite in a standard tag.

`grep -rn hreflang --include=*.py` over the repo returned nothing before this file existed.

WHY THIS AND NOT A CHARACTER COUNT. Counting Arabic characters in the crawled HTML measures WHAT
WAS CRAWLED, not what the site has: on this site it reads 5.5% Arabic and is entirely consistent
with both "no Arabic version" and "an Arabic version the crawler never opened". `hreflang` is the
site's own machine-readable answer, published for exactly this question.

Deliberately conservative: no alternates means an EMPTY list, never a claim of monolingualism.
"""
from __future__ import annotations

import re
from typing import List

# `rel` and `hreflang` appear in either order and with either quote style; keep the pattern
# tolerant rather than assuming one serializer's output.
_ALT = re.compile(
    r"<link\b(?=[^>]*\brel\s*=\s*[\"']?alternate)[^>]*\bhreflang\s*=\s*[\"']([^\"'>\s]+)",
    re.I)


def declared_languages(html: str) -> List[str]:
    """Sorted, de-duplicated primary language subtags this page declares an alternate for.

    `x-default` is a routing hint, not a language, and is dropped. Region subtags reduce to the
    language (`ar-EG` -> `ar`) because the dimension being answered is "which languages", not
    "which locales".
    """
    if not html:
        return []
    out = set()
    for tag in _ALT.findall(html):
        code = str(tag).strip().lower()
        if not code or code == "x-default":
            continue
        primary = code.split("-")[0].strip()
        if primary and primary.isalpha() and len(primary) <= 3:
            out.add(primary)
    return sorted(out)
