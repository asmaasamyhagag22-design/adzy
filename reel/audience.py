# -*- coding: utf-8 -*-
"""What language the AUDIENCE speaks — which is not what we happened to crawl.

OWNER (2026-08-01), on a finished reel: «دار الفوائد صيدلية في مصر وعندها عربي — عملت إنجليزي
ليه؟». The voiceover was, verbatim: "Tired of dandruff that just won't quit? Find real, original
solutions at…" — an English commercial for an Egyptian pharmacy.

THE CHAIN, MEASURED END TO END:

    the site declares an Arabic edition (hreflang)   ->  the crawler captures it            OK
                                                     ->  competitor/matrix.py uses it       OK
                                                     ->  the BusinessProfile               MISSING
                                                     ->  brand_is_arabic reads languages=['en']
                                                     ->  the whole ad is English

`scraper/extractors/language.declared_languages` reads exactly this and was written for exactly
this reason. It reaches the competitive matrix and stops there, so the one decision that sets the
language a customer HEARS never sees it.

And the evidence it does see is circular: `languages: ['en']` is a record of which edition WE
crawled, and the tagline and description are English because that is the edition we read. The
system inferred "this brand is English" from a fact it produced itself.

THE ORDER BELOW IS THE ARGUMENT. What a site DECLARES about itself outranks what one crawl
happened to fetch, and where the customers ARE outranks both — a pharmacy in Cairo serves Arabic
speakers whether or not its storefront software ships an English interface. Nothing here
enumerates a country or a market: the country comes off the profile and the language comes off a
table of official languages, so a market nobody has run yet works the day it appears (D-8).
"""
from __future__ import annotations

import logging
import re
from typing import Any, Optional

logger = logging.getLogger(__name__)

_AR = re.compile(r"[؀-ۿ]")

# Country -> the language its CUSTOMERS speak. Not exhaustive and not meant to be: it is consulted
# only when a site declares nothing, and an unknown country simply falls through to the evidence
# below rather than being guessed at.
_COUNTRY_LANG = {
    "eg": "Arabic", "sa": "Arabic", "ae": "Arabic", "kw": "Arabic", "qa": "Arabic",
    "bh": "Arabic", "om": "Arabic", "jo": "Arabic", "lb": "Arabic", "iq": "Arabic",
    "ly": "Arabic", "sd": "Arabic", "sy": "Arabic", "ye": "Arabic", "ps": "Arabic",
    "ma": "Arabic", "dz": "Arabic", "tn": "Arabic",
    "tr": "Turkish", "ir": "Persian", "pk": "Urdu", "id": "Indonesian",
    "fr": "French", "de": "German", "es": "Spanish", "it": "Italian", "pt": "Portuguese",
    "br": "Portuguese", "ru": "Russian", "cn": "Chinese", "jp": "Japanese", "kr": "Korean",
}

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

    # LAST, THE RESOLVER THAT ACTUALLY READS THE EVIDENCE — and the reason this function was
    # answering the wrong question for the dashboard.
    #
    # Everything above is a lookup: a `country` field, a name, a ccTLD. `poster.locale.country_of`
    # reads the profile's own evidence — the phone numbers, the addresses, the currency — and it is
    # the resolver the poster and the seed casting already trust.
    #
    # MEASURED 2026-08-01 on AlFouad, and it is exactly the defect the owner reported in July
    # («دار الفوائد صيدلية في مصر وعندها عربي — عملت إنجليزي ليه؟»): the CLI's saved profile carries
    # `country: "eg"` and resolved to Arabic, while the SAME BRAND stored by the dashboard has no
    # `country` field, a `.com` domain, `languages: ['en']` — and resolved to ENGLISH. Two country
    # resolvers in one codebase, and the thinner one decided what language the ad was spoken in.
    # `country_of` returns "Egypt" for BOTH.
    try:
        from poster.locale import country_of
        named_country = str(country_of(profile) or "").strip().lower()
        for k, v in named.items():
            if k in named_country:
                return v
    except Exception:  # noqa: BLE001 — an unknown market falls through to the script evidence
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
    """(language_name, country_code, why) — what the AUDIENCE speaks, and the evidence for it.

    `why` exists so a wrong answer is arguable rather than mysterious: it names which rung of the
    ladder decided, which is the difference between a bug someone can find and a bug someone
    shrugs at."""
    country = _country_code(profile)

    # 1) WHAT THE SITE DECLARES ABOUT ITSELF. hreflang is the brand publishing its own answer;
    #    a single crawl is our answer about the brand. Theirs wins.
    declared = [str(x).lower()[:2] for x in ((profile or {}).get("declared_languages") or [])]
    non_en = [d for d in declared if d and d != "en"]
    if non_en:
        lang = _LANG_NAME.get(non_en[0], non_en[0])
        return lang, country, f"the site declares a {lang} edition (hreflang)"

    # 2) WHERE THE CUSTOMERS ARE. A pharmacy in Cairo serves Arabic speakers whether or not its
    #    storefront software ships an English interface.
    if country and country in _COUNTRY_LANG:
        lang = _COUNTRY_LANG[country]
        return lang, country, f"the brand trades in '{country}', whose customers speak {lang}"

    # 3) THE BRAND'S OWN WORDS, in its own script — real evidence, but the weakest rung, because
    #    it is exactly the evidence a crawl of the English edition manufactures.
    blob = " ".join(_fv(profile, k) for k in ("name", "tagline", "description"))
    if _AR.search(blob):
        return "Arabic", country, "the brand writes about itself in Arabic"

    # 4) WHAT THE CRAWL MEASURED — the DOMINANT language, not merely a language that is present.
    #
    # This used to take the first NON-ENGLISH code in the list, which inverted the one piece of
    # evidence the rung actually has. `scraper/language.detect_languages` builds its entries with
    # `counter.most_common()`, so the list is RANKED BY PROPORTION and carries a `proportion` on
    # every entry; taking "the first thing that isn't English" throws that ranking away and lets a
    # single Arabic page outvote an entire English site.
    #
    # MEASURED 2026-08-06 on the stored Volume Cafe record (Doha): `languages: ['en', 'ar']` — so
    # English dominates — resolved to **Arabic**, and that answer reached the reel, the poster and
    # (once the calendar was aligned) the content plan. `process.md` had already measured this
    # brand independently and written down the opposite conclusion: en @ 100%, and *"hl='ar' would
    # have been wrong — the site is English"*. The system was overruling its own measurement.
    #
    # Every other stored brand is decided on a higher rung and is byte-identical.
    dominant = _dominant_language((profile or {}).get("languages") or [])
    if dominant:
        lang = _LANG_NAME.get(dominant, dominant)
        return lang, country, f"the crawl measured {lang} as the site's dominant language"

    return "English", country, "no declaration, no known market, and nothing but English found"


def audience_is_arabic(profile: dict) -> bool:
    """The one-line form, for callers that only branch on Arabic-or-not."""
    return audience_language_for(profile)[0] == "Arabic"


_NAME_TO_CODE = {v: k for k, v in _LANG_NAME.items()}


def audience_language_code(profile: dict) -> str:
    """The ISO code the reel director expects ('ar', 'en', ...)."""
    return _NAME_TO_CODE.get(audience_language_for(profile)[0], "en")
