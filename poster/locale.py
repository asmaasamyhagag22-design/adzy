"""Evidence-derived brand locale — ONE shared primer for every image-prompt builder.

FIX-C3/D (2026-07-11, owner: "لازم البلد والثقافة تأثر"): an Egyptian government brand got
visibly non-Egyptian generic-Western people because the culture anchor lived ONLY on the
poster's classic path — the oneshot engine, the concept builder and the reel had zero locale.
This module derives the brand's country from PROFILE EVIDENCE (never hardcoded):
    ccTLD  ->  service_areas (an evidenced field)  ->  phone e164 prefix  ->  languages cue
and renders the proven `_region_line` wording parameterized by that country. Unknown locale ->
("", "") — an HONEST fallback: no cultural claim is injected when the evidence doesn't carry one.
Universal by construction: works for any country; nothing Egypt-specific.
"""
from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

# ccTLD -> country. The MENA core (the pilot market) + the major world markets. A static
# lookup, no deps; extend freely — every entry is evidence-driven (the brand's own domain).
CCTLD_COUNTRY = {
    # MENA
    "eg": "Egypt", "sa": "Saudi Arabia", "ae": "the UAE", "ma": "Morocco", "jo": "Jordan",
    "kw": "Kuwait", "qa": "Qatar", "lb": "Lebanon", "dz": "Algeria", "tn": "Tunisia",
    "iq": "Iraq", "om": "Oman", "bh": "Bahrain", "ly": "Libya", "sy": "Syria",
    "ye": "Yemen", "sd": "Sudan", "ps": "Palestine", "tr": "Turkey", "ir": "Iran",
    # Africa
    "ng": "Nigeria", "ke": "Kenya", "za": "South Africa", "gh": "Ghana", "et": "Ethiopia",
    # Europe
    "uk": "the UK", "de": "Germany", "fr": "France", "es": "Spain", "it": "Italy",
    "nl": "the Netherlands", "pt": "Portugal", "gr": "Greece", "pl": "Poland",
    "se": "Sweden", "ch": "Switzerland", "at": "Austria", "be": "Belgium", "ie": "Ireland",
    # Asia-Pacific
    "in": "India", "pk": "Pakistan", "bd": "Bangladesh", "id": "Indonesia", "my": "Malaysia",
    "sg": "Singapore", "th": "Thailand", "vn": "Vietnam", "ph": "the Philippines",
    "jp": "Japan", "kr": "South Korea", "cn": "China", "hk": "Hong Kong", "tw": "Taiwan",
    "au": "Australia", "nz": "New Zealand",
    # Americas
    "us": "the USA", "ca": "Canada", "mx": "Mexico", "br": "Brazil", "ar": "Argentina",
    "cl": "Chile", "co": "Colombia", "pe": "Peru",
}

# e164 dial prefix -> country (longest-prefix match). Compact, common markets.
_E164_COUNTRY = {
    "+20": "Egypt", "+966": "Saudi Arabia", "+971": "the UAE", "+212": "Morocco",
    "+962": "Jordan", "+965": "Kuwait", "+974": "Qatar", "+961": "Lebanon",
    "+213": "Algeria", "+216": "Tunisia", "+964": "Iraq", "+968": "Oman",
    "+973": "Bahrain", "+218": "Libya", "+249": "Sudan", "+970": "Palestine",
    "+90": "Turkey", "+234": "Nigeria", "+254": "Kenya", "+27": "South Africa",
    "+44": "the UK", "+49": "Germany", "+33": "France", "+34": "Spain", "+39": "Italy",
    "+91": "India", "+92": "Pakistan", "+62": "Indonesia", "+60": "Malaysia",
    "+65": "Singapore", "+81": "Japan", "+82": "South Korea", "+86": "China",
    "+61": "Australia", "+1": "the USA/Canada", "+52": "Mexico", "+55": "Brazil",
}


# ---------------------------------------------------------------------------
# ISO-3166-1 alpha-2 for a resolved country NAME. Added 2026-07-15 (D-8) because the
# SERP needs a country CODE ("qa") while `country_of` speaks display names ("Qatar").
#
# ONE inversion, HERE, in the module that owns the country facts. A second table is what
# `poster/art_director.py:582` already is (18 MENA entries vs this module's ~60) and two
# tables drift; a third would be worse. Derived from CCTLD_COUNTRY at import so a new ccTLD
# entry automatically gains its code — nothing to keep in sync by hand.
#
# A ccTLD is USUALLY its ISO alpha-2, but not always. Verified by hand against all 62 keys
# (pycountry is not installed here): `.uk` is the ONLY mismatch — ISO-3166-1 for the United
# Kingdom is GB, not UK.
#   UNVERIFIED: Google's `gl` accepts the legacy "uk" as well as "gb"; we send the ISO code
#   because that is what the parameter is documented to take. Not exercised — no UK brand has
#   ever been through this system (every scrape on disk is EG/SA/US).
_CCTLD_ISO_OVERRIDE = {"uk": "gb"}


def _build_country_codes() -> dict[str, str]:
    """name -> ISO alpha-2. A name reachable from two different ccTLDs is AMBIGUOUS and is
    dropped rather than resolved arbitrarily (there are none today; this keeps it true)."""
    out: dict[str, str] = {}
    ambiguous: set[str] = set()
    for tld, name in CCTLD_COUNTRY.items():
        code = _CCTLD_ISO_OVERRIDE.get(tld, tld)
        if name in out and out[name] != code:
            ambiguous.add(name)
        out[name] = code
    for name in ambiguous:
        out.pop(name, None)
    return out


_COUNTRY_CODE = _build_country_codes()


_COUNTRY_NAME = {code: name for name, code in _COUNTRY_CODE.items()}


def country_name_for(code: str) -> str:
    """ISO-3166 alpha-2 -> the display name `country_of` speaks: "qa" -> "Qatar". '' when the code
    is unknown (NEVER a guess). The inverse of `country_code`; both live here beside the country
    facts so there is exactly ONE country<->code map (D-8). Used to put the MARKET name into a trend
    query's TEXT (trends/sources.py) — the owner measured that `gl` alone can't carry the market."""
    return _COUNTRY_NAME.get((code or "").strip().lower(), "")


def country_code(country: str) -> str:
    """ISO-3166-1 alpha-2 for a country name as `country_of` returns it: "Qatar" -> "qa".

    '' when the name is unknown, ambiguous, or empty — NEVER a guess. The empty string is the
    honest answer and callers must treat it as "no country claim": `SerperTrendSource` turns it
    into `gl=None`, so the request carries no market rather than a wrong one.

    Deliberately NOT handled: `_E164_COUNTRY`'s `"+1": "the USA/Canada"` (:51) is not a
    CCTLD_COUNTRY value, so it resolves to '' here. That is correct — a +1 phone genuinely does
    not tell us which of the two countries the brand is in, and inventing one would be the
    fabrication this function exists to avoid.
    """
    return _COUNTRY_CODE.get((country or "").strip(), "")


def _get(obj: Any, key: str, default: Any = None) -> Any:
    val = obj.get(key, default) if isinstance(obj, dict) else getattr(obj, key, default)
    return default if val is None else val


def _fv(obj: Any, key: str) -> str:
    v = _get(obj, key)
    if isinstance(v, dict) and "value" in v:
        v = v.get("value")
    v = getattr(v, "value", v)
    return str(v).strip() if v else ""


def country_of(profile: Any) -> str:
    """The brand's country from profile evidence, strongest signal first. '' when unknown."""
    p = profile or {}
    if isinstance(p, dict) and isinstance(p.get("profile"), dict):
        p = p["profile"]

    # 1. ccTLD of the brand's own domain (the most reliable signal)
    url = _fv(p, "source_url") or _fv(p, "url") or _fv(p, "website")
    try:
        host = urlparse(url if "://" in url else "https://" + url).netloc.lower()
    except Exception:
        host = ""
    if host:
        tld = host.rsplit(".", 1)[-1]
        if tld in CCTLD_COUNTRY:
            return CCTLD_COUNTRY[tld]

    # 2. service_areas — an evidenced profile field ("Egypt", "Cairo, Egypt", ...)
    for area in (_get(p, "service_areas") or []):
        a = area.get("value") if isinstance(area, dict) else getattr(area, "value", area)
        a = str(a or "").strip()
        for country in set(CCTLD_COUNTRY.values()):
            plain = country.replace("the ", "")
            if plain and plain.lower() in a.lower():
                return country

    # 3. phone e164 prefix (longest match wins)
    cc = _get(p, "contact_channels") or {}
    for ph in (_get(cc, "phones") or []):
        e164 = ph.get("e164") if isinstance(ph, dict) else getattr(ph, "e164", None)
        e164 = str(e164 or "")
        if e164.startswith("+"):
            for pref in sorted(_E164_COUNTRY, key=len, reverse=True):
                if e164.startswith(pref):
                    return _E164_COUNTRY[pref]
    return ""


def search_locale(profile: Any) -> tuple[str, str]:
    """(gl, hl) for a web search on the brand's behalf — both from the brand's OWN evidence.

    `gl` = ISO-3166 alpha-2 of the country `country_of` resolves ("qa").
    `hl` = the brand's DOMINANT content language, MEASURED from its own page text
           (`scraper/language.py:detect_languages` counts langdetect proportions and emits
           `counter.most_common()`, so the list is ordered by dominance; the profile keeps that
           order and drops the number — `business_profile/rules/orchestrator.py:112`).

    ("", "") when the evidence is silent — the caller must send NO market rather than a guessed
    one (`SerperTrendSource` turns each "" into None, so the key is omitted entirely).

    `hl` IS NOT DERIVED FROM `gl` — deliberately. "The country is Arab, therefore search in
    Arabic" is the inference D-8 forbids: it is `strategy/builder.py:154`'s "any Arabic brand =>
    Egyptian dialect" with the arrow reversed. A Doha cafe whose own site measures 100% English
    gets `("qa", "en")`. The country and the language are two independent facts and each needs
    its own evidence.
    """
    p = profile or {}
    if isinstance(p, dict) and isinstance(p.get("profile"), dict):
        p = p["profile"]
    gl = country_code(country_of(p))
    hl = ""
    for x in (_get(p, "languages") or []):
        code = getattr(x, "code", None) or (x.get("code") if isinstance(x, dict) else x)
        code = str(code or "").strip().lower()[:2]          # "en-US" -> "en"
        if code:
            hl = code                                        # [0] is the dominant one
            break
    return gl, hl


# The Arabic dialect/register buckets. Keyed on `country_of`'s display-name strings (note the
# "the " prefix on some). This map lives HERE — beside `country_of` and `CCTLD_COUNTRY`, the country
# facts it depends on — so there is exactly ONE country->dialect map. It was born in
# `strategy/builder.py` (P-04, for the content calendar); relocated 2026-07-18 (CS-04) so the POSTER
# copy can reuse it without a poster->strategy dependency on a private symbol, and without a fourth
# hardcoded table (the owner's standing rule: duplicate tables ARE the disease — CS-13/P-08).
_GULF_COUNTRIES = {"Saudi Arabia", "the UAE", "Qatar", "Kuwait", "Bahrain", "Oman"}
_LEVANT_COUNTRIES = {"Lebanon", "Jordan", "Syria", "Palestine", "Iraq"}
_MAGHREB_COUNTRIES = {"Morocco", "Algeria", "Tunisia", "Libya"}


def arabic_region_of(profile: Any) -> str:
    """The canonical Arabic dialect BUCKET for the brand's evidenced country: one of
    'egypt' | 'gulf' | 'levant' | 'maghreb' | '' (unknown). ONE bucket source so the register
    WORDING (`arabic_dialect_for`) and the reel's native-VOICE maps (ElevenLabs / edge-tts in
    `reel/voiceover.py`) can never disagree about which region a country belongs to. Keyed on
    `country_of`'s display names — the same country facts every other locale reader uses (D-8)."""
    c = country_of(profile or {})
    if c == "Egypt":
        return "egypt"
    if c in _GULF_COUNTRIES:
        return "gulf"
    if c in _LEVANT_COUNTRIES:
        return "levant"
    if c in _MAGHREB_COUNTRIES:
        return "maghreb"
    return ""


def arabic_dialect_for(profile: Any) -> str:
    """The Arabic register keyed on the brand's EVIDENCED country (P-04, D-8), with a neutral
    default — NOT a hardcoded 'Egyptian Arabic' for every Arabic brand. A Gulf/Levantine/Maghrebi
    brand's copy was being written in Masri; now each gets its own register, and an unknown-country
    Arabic brand gets a neutral conversational register rather than Egyptian. Takes the whole
    profile and resolves the region itself (via `arabic_region_of`), so callers pass `profile`.
    """
    return {
        "egypt": "Egyptian Arabic (عامية مصرية)",
        "gulf": "Gulf/Khaleeji Arabic (لهجة خليجية)",
        "levant": "Levantine Arabic (لهجة شامية)",
        "maghreb": "the brand's Maghrebi register (or Modern Standard Arabic)",
    }.get(arabic_region_of(profile), "natural conversational Arabic in the brand's own regional register")


def _casting_exclusions(profile: Any) -> str:
    """What the people should NOT look like — DERIVED from the brand's own region.

    MEASURED (2026-07-25): this used to be one fixed string, "NOT a generic 'Arab' or
    Gulf/Khaleeji look, NOT Western/European casting", appended to every country. For a Qatari
    brand that reads: be authentically Qatari, and do not look Khaleeji. Qatar IS the Gulf. Same
    self-contradiction for Saudi Arabia, the UAE, Kuwait, Bahrain and Oman — every Gulf poster the
    system has ever produced.

    How it got there is this project's own failure class: the exclusion is CORRECT for the incident
    it was written from (Egyptian brands kept being rendered with a generic Gulf look). Generalising
    swapped the country name and left the negation fixed, so a true statement about Egypt became the
    answer to a different question.

    Now the line rules out the neighbouring Arab looks a brand is NOT part of, and never its own.
    Western/European casting stays excluded everywhere — that exclusion is about the brand's market
    versus the model's default, not about one region versus another."""
    region = arabic_region_of(profile)
    langs = [str(getattr(x, "code", None) or (x.get("code") if isinstance(x, dict) else x) or "")
             for x in (_get(profile, "languages") or [])]
    arabic_market = bool(region) or any(str(l).lower().startswith("ar") for l in langs)
    if not arabic_market:
        # CAUGHT WHILE FIXING THE GULF CASE: keeping "NOT Western/European casting" for a French or
        # American brand is the SAME self-contradiction one level out — it tells a European brand
        # not to look European. With no regional evidence the positive anchor ("authentically from
        # {country}") is the honest whole instruction.
        return ""
    buckets = {"gulf": "Gulf/Khaleeji", "levant": "Levantine", "maghreb": "North African"}
    # Rule out the neighbouring looks the brand is NOT part of — never its own. An Arabic-market
    # country that falls in no bucket (Egypt, Sudan, Yemen) is not part of ANY of them, so all
    # three are excluded; that is precisely the protection the original line was written for.
    others = [label for key, label in buckets.items() if key != region]
    return f"NOT a generic {' or '.join(others)} look, NOT Western/European casting"


def brand_locale(profile: Any) -> tuple[str, str]:
    """(country, primer_line) for image prompts. The primer is the proven anti-drift wording
    (no clichés: 'their own everyday style', never named dress items). When only the LANGUAGE
    is known (Arabic content, no country evidence), an honest regional cue is returned; when
    nothing is known, ('', '') — inject no cultural claim."""
    p = profile or {}
    if isinstance(p, dict) and isinstance(p.get("profile"), dict):
        p = p["profile"]
    country = country_of(p)
    if country:
        excl = _casting_exclusions(p)
        return country, (
            f"PEOPLE & SETTING — every person and environment is authentically from {country}: "
            f"real local people in their own everyday style and dress"
            f"{f' ({excl})' if excl else ''}; "
            f"architecture, streets, interiors and signage that belong in {country}."
        )
    langs = [str(getattr(x, "code", None) or (x.get("code") if isinstance(x, dict) else x) or "").lower()
             for x in (_get(p, "languages") or [])]
    if any(str(l).startswith("ar") for l in langs):
        return "", ("PEOPLE & SETTING — an authentic Arabic-speaking-region environment with real "
                    "local people; NOT Western or European architecture, streets, signage, or people.")
    return "", ""
