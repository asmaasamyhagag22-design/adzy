"""Language detection for the scraped pages.

Combines two signals:
- <html lang="..."> attribute(s) seen on pages
- langdetect probabilities on the concatenated body text

Outputs proportions per language code (ISO 639-1).
"""
from __future__ import annotations

from collections import Counter
from typing import Optional

# langdetect is non-deterministic by default; seed for reproducibility
try:
    from langdetect import DetectorFactory, detect_langs, LangDetectException
    DetectorFactory.seed = 42
    _LANGDETECT_OK = True
except Exception:
    _LANGDETECT_OK = False

from .schemas import LanguageEntry


def detect_languages(
    page_texts: list[str],
    html_lang_hints: Optional[list[str]] = None,
) -> list[LanguageEntry]:
    # MEASURED CONTENT FIRST (P-67). langdetect on the real body text is the authoritative signal;
    # the `<html lang>` attribute is frequently a template DEFAULT ("en-US" on an Arabic site — the
    # exact trap scraper/url_utils.py:region_from_site documents) and used to be counted co-equally
    # (weight +1 per page vs langdetect's 0..1), so a few boilerplate lang="en" tags outvoted pages
    # of measured Arabic. That produced languages:["en"] for elkbabgi (Arabic-first) and drove P-20.
    counter: Counter[str] = Counter()
    if _LANGDETECT_OK:
        for text in page_texts:
            if not text or len(text.strip()) < 40:
                continue
            try:
                # Use a chunk of the text — full pages are slow
                sample = text[:4000]
                results = detect_langs(sample)
                for r in results[:2]:  # top-2 per page
                    code = str(r.lang).lower()
                    # weight by probability
                    counter[code] += float(r.prob)
            except LangDetectException:
                continue

    # html lang is a WEAK PRIOR: only a tie-breaker when content WAS measured (its total influence
    # is capped well below one page's langdetect weight, so it can never override the measured
    # majority), OR the sole signal when no body text was detectable at all.
    if html_lang_hints:
        hint_codes: Counter[str] = Counter()
        for h in html_lang_hints:
            if not h:
                continue
            code = h.split("-")[0].lower()
            if 2 <= len(code) <= 3:
                hint_codes[code] += 1
        if hint_codes:
            if counter:  # measured content exists -> hints are a tiny tie-breaker only
                scale = 0.25 / sum(hint_codes.values())
                for code, n in hint_codes.items():
                    counter[code] += n * scale
            else:        # no detectable text -> the html lang hint is all we have
                counter.update(hint_codes)

    if not counter:
        return []

    total = sum(counter.values())
    entries = [
        LanguageEntry(code=code, proportion=count / total)
        for code, count in counter.most_common()
    ]
    return entries
