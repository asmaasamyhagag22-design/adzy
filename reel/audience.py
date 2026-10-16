# -*- coding: utf-8 -*-
"""The reel's language and country decisions, kept deliberately independent.

F-06 contract: the profile's evidenced content language decides the language; country is returned
separately for market context and may choose an Arabic dialect only after the language is Arabic.
An English or unknown-language brand never becomes Arabic merely because it trades in an
Arabic-speaking country.
"""
from __future__ import annotations

import re
from typing import Any

_LANG_NAME = {
    "ar": "Arabic", "en": "English", "fr": "French", "de": "German", "es": "Spanish",
    "it": "Italian", "pt": "Portuguese", "ru": "Russian", "tr": "Turkish", "fa": "Persian",
    "ur": "Urdu", "id": "Indonesian", "zh": "Chinese", "ja": "Japanese", "ko": "Korean",
}


def _fv(profile: dict, key: str) -> str:
    f = (profile or {}).get(key)
    if isinstance(f, dict):
        f = f.get("value")
    return str(f or "").strip()


def _country_code(profile: dict) -> str:
    """Two-letter country, from the profile or from the domain's ccTLD."""
    c = _fv(profile, "country").lower()
    if len(c) == 2 and c.isalpha():
        return c
    named = {"egypt": "eg", "مصر": "eg", "saudi": "sa", "السعودية": "sa", "uae": "ae",
             "الامارات": "ae", "الإمارات": "ae", "kuwait": "kw", "qatar": "qa",
             "jordan": "jo", "lebanon": "lb", "morocco": "ma", "tunisia": "tn"}
    for k, v in named.items():
        if k in c:
            return v
    url = _fv(profile, "source_url").lower()
    m = re.search(r"https?://[^/]*\.([a-z]{2})(?:[/:]|$)", url)
    if m and m.group(1) not in ("co", "com", "org", "net"):
        return m.group(1)
    # ...and the `.com.eg` / `.co.uk` shape the pattern above deliberately steps over.
    m = re.search(r"https?://[^/]*\.(?:com|co|org|net)\.([a-z]{2})(?:[/:]|$)", url)
    if m:
        return m.group(1)

    # Last, use the shared evidence resolver (addresses, phone numbers, currency, etc.). This
    # remains country evidence only; `audience_language_for` never treats it as language evidence.
    try:
        from poster.locale import country_of
        named_country = str(country_of(profile) or "").strip().lower()
        for k, v in named.items():
            if k in named_country:
                return v
    except Exception:  # noqa: BLE001 — an unknown market remains unknown
        pass
    return ""


def _dominant_language(entries: Any) -> str:
    """The two-letter code the crawl measured MOST of, or "".

    Handles both shapes the pipeline stores. A live extraction carries `LanguageEntry`-like
    objects/dicts with a `proportion`; the webapp's saved records flatten them to bare strings, and
    those keep their order because `detect_languages` emits `counter.most_common()`. So: rank by
    proportion when it survived, otherwise trust the order — never "the first one that isn't
    English", which is what this replaced.
    """
    best_code, best_share, first = "", -1.0, ""
    for e in (entries or []):
        if isinstance(e, dict):
            code, share = str(e.get("code") or "").lower()[:2], float(e.get("proportion") or 0.0)
        else:
            code = str(getattr(e, "code", None) or e or "").lower()[:2]
            share = float(getattr(e, "proportion", 0.0) or 0.0)
        if not code:
            continue
        first = first or code
        if share > best_share:
            best_code, best_share = code, share
    # All-zero proportions means they were flattened away — the list order is the ranking.
    return best_code if best_share > 0 else first


def audience_language_for(profile: dict) -> tuple:
    """Return ``(content_language_name, country_code, evidence_reason)``.

    Language comes only from the shared F-06 content-language contract. Country is deliberately
    absent from that decision; callers may use it later for dialect/market context."""
    country = _country_code(profile)
    from poster.locale import content_language_of

    code = content_language_of(profile)
    if code:
        lang = _LANG_NAME.get(code, code)
        if (profile or {}).get("languages"):
            why = f"the profile measured {lang} as its dominant content language"
        else:
            why = f"the site declares one unambiguous {lang} content edition (hreflang)"
        return lang, country, why

    return "English", country, "no evidenced content language; defaulting safely to English"


def audience_is_arabic(profile: dict) -> bool:
    """The one-line form, for callers that only branch on Arabic-or-not."""
    return audience_language_for(profile)[0] == "Arabic"


def audience_language_code(profile: dict) -> str:
    """The ISO code the reel director expects ('ar', 'en', ...)."""
    from poster.locale import content_language_of

    return content_language_of(profile) or "en"
