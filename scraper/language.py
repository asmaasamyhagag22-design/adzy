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

import re as _re

# A 2-3 letter ALL-CAPS token is an ISO-style code (SA, SAU, AE, ARE, …), never prose.
_CODE_TOKEN = _re.compile(r"^[A-Z]{2,3}$")
# The separators dropdown/token dumps are glued with (·•|/ and comma-runs).
_LIST_SEPARATORS = "·•|"


def _prose_only(text: str) -> str:
    """Keep only the lines a LANGUAGE detector should ever see.

    MEASURED (nanocare, 2026-07-24): a login page's visible text was dominated by a phone
    country-picker dump — "Saudi Arabia · SA · SAU · UAE · AE · ARE · …" — and langdetect
    labeled that token salad CATALAN at probability 1.0, shipping languages=['ar','ca'] for
    an Egyptian brand. A token dump is not prose in ANY language, so it must never vote.

    Universal signals only (D-8 — country pickers/currency lists exist in every market):
      * ISO-code density: a line whose tokens are ≥20% 2-3-letter ALL-CAPS codes is a code
        list, not a sentence;
      * separator density: a line glued by ·/•/| at ≥0.25 per word is an inline list.
    Whatever prose remains votes; a page left empty contributes nothing — no signal beats
    a confidently wrong one."""
    kept: list[str] = []
    for line in str(text or "").splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        words = stripped.split()
        seps = sum(stripped.count(s) for s in _LIST_SEPARATORS)
        if words and seps / len(words) >= 0.25:
            continue
        codes = sum(1 for w in words if _CODE_TOKEN.match(w.strip(".,;:()[]")))
        if words and codes / len(words) >= 0.20:
            continue
        kept.append(stripped)
    return "\n".join(kept)


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
            text = _prose_only(text)        # token dumps are not a language — see _prose_only
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
