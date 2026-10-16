"""C1 gate: phone region comes from the SITE's own reliable signal, never a blanket
country — so a foreign national number is not fabricated into a wrong home-market one.

Regression origin (measured 2026-07-05): the Saudi site nahdionline.com published the
hotline 920024673 via a `tel:` link. Parsed under a hardcoded default_region="EG" it
became a VALID-looking but WRONG Egyptian number +20920024673 (stored as the brand's
phone in 2/3 saved manifests); the correct number is +966920024673. The site's /ar-sa
locale path is a reliable country signal; without one, local digits stay unresolved.

Hermetic: no network, no LLM. Pure functions over synthetic HTML.
"""
from scraper.url_utils import region_from_site
from scraper.extractors.contact import extract_contact


def test_region_from_locale_path_beats_generic_tld():
    # nahdi: a Saudi business on a GENERIC .com whose only country signal is /ar-sa.
    assert region_from_site("https://www.nahdionline.com/ar-sa") == "SA"
    assert region_from_site("https://shop.example.com/en-gb/cart") == "GB"


def test_region_from_cctld():
    assert region_from_site("https://te.eg/") == "EG"
    assert region_from_site("https://web.vodafone.com.eg/") == "EG"   # multi-label suffix
    assert region_from_site("https://boutique.example.fr/") == "FR"


def test_region_none_when_no_reliable_signal():
    # Generic TLD, no locale path -> no reliable country signal. html lang is NOT
    # consulted (en-US boilerplate mislabels non-US sites), so this stays None.
    assert region_from_site("https://daturial.com/") is None
    assert region_from_site("https://example.net/about") is None
    # A language-only locale segment carries no region.
    assert region_from_site("https://example.com/ar/home") is None


def test_nahdi_saudi_number_not_fabricated_as_egyptian():
    """The core C1 fix: /ar-sa resolves to SA, so 920024673 -> +966..., never +20..."""
    page = "https://www.nahdionline.com/ar-sa"
    html = '<html><body><a href="tel:920024673">اتصل بنا</a></body></html>'
    info = extract_contact({page: html}, {page: []}, site_url=page)
    assert len(info.phones) == 1
    assert info.phones[0].e164 == "+966920024673"
    assert not info.phones[0].e164.startswith("+20")


def test_generic_tld_local_number_remains_raw_without_country_evidence():
    """A .com plus national digits cannot prove a country or an E.164 prefix."""
    page = "https://daturial.com/contact"
    html = '<html><body><a href="tel:01012581929">Call us</a></body></html>'
    info = extract_contact({page: html}, {page: []}, site_url=page)
    assert len(info.phones) == 1
    assert info.phones[0].raw == "01012581929"
    assert info.phones[0].e164 is None
    assert info.phones[0].is_short_code is False


def test_explicit_plus_prefixed_number_is_region_independent():
    """A +-prefixed number carries its own country and must be untouched by region logic
    (verified on a non-EG country code from a signal-less site)."""
    page = "https://brilliantearth.com/contact"
    html = '<html><body><a href="tel:+14155206526">US line</a></body></html>'
    info = extract_contact({page: html}, {page: []}, site_url=page)
    assert len(info.phones) == 1
    assert info.phones[0].e164 == "+14155206526"


def test_explicit_default_region_overrides_site_derivation():
    """Backward-compat: an explicit default_region wins (used by older callers/tests)."""
    page = "https://www.nahdionline.com/ar-sa"
    html = '<html><body><a href="tel:920024673">x</a></body></html>'
    info = extract_contact({page: html}, {page: []}, default_region="EG", site_url=page)
    assert info.phones[0].e164 == "+20920024673"
