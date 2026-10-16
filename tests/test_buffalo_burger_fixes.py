"""Regression tests for the Buffalo Burger extraction fixes (2026-05).

Findings the user reported on a real scrape:
  1. tel:19914 hotline silently dropped (E.164 validation rejected it).
  2. "View Menu", "Cart", "Go To Cart", "Call Us 19914", "Download App",
     "Delivery", "Order Online" not detected as CTA candidates.
  3. /branches/all/menu misclassified as BRANCHES_LOCATIONS instead of MENU.
  4. /offer-detail/* falling through to OTHER; /cart-details too.
  5. robots.txt advertised sitemap on a different host (canonical).

The fixes are narrowly scoped — only patterns supported by the Buffalo
Burger evidence were added. Speculative additions ("Get the app", "Find
a store") were intentionally NOT added until the benchmark proves the
need.
"""
from __future__ import annotations

import sys
import types
from unittest.mock import patch

_fake = types.ModuleType("playwright")
_fake_sync = types.ModuleType("playwright.sync_api")
class _Stub: ...
for _n in ("Browser", "BrowserContext", "Page", "sync_playwright"):
    setattr(_fake_sync, _n, _Stub)
_fake_sync.TimeoutError = type("TimeoutError", (Exception,), {})
_fake_sync.Error = type("Error", (Exception,), {})
sys.modules.setdefault("playwright", _fake)
sys.modules.setdefault("playwright.sync_api", _fake_sync)


# =====================================================================
# Fix 1 — Hotline / short-code phone support
# =====================================================================

def test_tel_hotline_19914_extracted_as_short_code():
    """tel:19914 must become a PhoneRecord with raw='19914', e164=None,
    is_short_code=True. The href IS the evidence."""
    from scraper.extractors.contact import extract_contact

    page_url = "https://buffaloburger.com/"
    html = '<html><body><a href="tel:19914">Call Us 19914</a></body></html>'
    info = extract_contact({page_url: html}, {page_url: []})
    assert len(info.phones) == 1
    p = info.phones[0]
    assert p.raw == "19914"
    assert p.e164 is None
    assert p.is_short_code is True


def test_tel_normal_egyptian_number_still_produces_e164():
    """Regression guard: normal phone numbers must still validate to E.164."""
    from scraper.extractors.contact import extract_contact

    page_url = "https://x.example/"
    html = '<html><body><a href="tel:+201001234567">Call us</a></body></html>'
    info = extract_contact({page_url: html}, {page_url: []})
    assert len(info.phones) == 1
    p = info.phones[0]
    assert p.e164 == "+201001234567"
    assert p.is_short_code is False


def test_tel_alphanumeric_junk_is_dropped():
    """tel:contact-us (no digits at all) must NOT be stored."""
    from scraper.extractors.contact import extract_contact

    page_url = "https://x.example/"
    info = extract_contact(
        {page_url: '<html><body><a href="tel:contact-us">Bad</a></body></html>'},
        {page_url: []},
    )
    assert info.phones == []


def test_tel_too_short_is_dropped_but_ambiguous_local_phone_is_retained():
    """tel:1 (1 digit, below 3-digit floor) is rejected.
    A longer tel: value with no country context remains raw and unresolved;
    it is not misclassified as a short code or fabricated as E.164."""
    from scraper.extractors.contact import extract_contact

    page_url = "https://x.example/"

    # 1-digit: rejected (below 3-digit minimum for short codes)
    info1 = extract_contact(
        {page_url: '<html><body><a href="tel:1">x</a></body></html>'},
        {page_url: []},
    )
    assert info1.phones == []

    # 12 digits: too long for a short code, but the tel: link is useful raw evidence.
    info2 = extract_contact(
        {page_url: '<html><body><a href="tel:123456789012">x</a></body></html>'},
        {page_url: []},
    )
    assert len(info2.phones) == 1
    assert info2.phones[0].raw == "123456789012"
    assert info2.phones[0].e164 is None
    assert info2.phones[0].is_short_code is False


def test_business_profile_contact_channels_carry_hotline():
    """End-to-end: the manifest's short-code PhoneRecord must reach the
    business profile's ContactChannels.phones list."""
    from datetime import datetime, timezone
    from business_profile.rules.from_contact import extract_contact as bp_extract
    from scraper.schemas import (
        ContactInfo, PhoneRecord, ReadinessReport, RobotsRecord,
        ScrapeManifest, ScrapeMeta, SiteMetadata,
    )

    manifest = ScrapeManifest(
        scrape_meta=ScrapeMeta(
            input_url="https://buffaloburger.com/",
            normalized_url="https://buffaloburger.com/",
            scraped_at=datetime.now(timezone.utc),
        ),
        robots=RobotsRecord(checked=True, allowed=True),
        readiness=ReadinessReport(
            has_homepage=True, has_internal_pages=False,
            has_contact_signals=True, has_visual_signals=False,
            has_cta_candidates=False, has_metadata=False,
            ready_for_extraction=True,
        ),
        site_metadata=SiteMetadata(),
        contact=ContactInfo(phones=[
            PhoneRecord(
                e164=None, raw="19914", is_short_code=True,
                evidence_block_id=None, page_url="https://buffaloburger.com/",
            ),
        ]),
    )
    channels = bp_extract(manifest)
    assert len(channels.phones) == 1
    p = channels.phones[0]
    assert p.raw == "19914"
    assert p.e164 is None
    assert p.is_short_code is True


def test_phones_e164_alias_returns_only_full_numbers():
    """Backward-compat: the deprecated `phones_e164` property must return
    ONLY validated E.164 numbers. Short codes are not included by this
    name (since they aren't E.164). Readers wanting all dialable phones
    must migrate to `phones`."""
    from business_profile.schemas import ContactChannels, PhoneChannel

    channels = ContactChannels(phones=[
        PhoneChannel(e164="+201001234567", raw="+20 100 123 4567", is_short_code=False),
        PhoneChannel(e164=None, raw="19914", is_short_code=True),
    ])
    assert channels.phones_e164 == ["+201001234567"]


def test_phones_e164_legacy_construction_still_works():
    """Backward-compat: code that constructs ContactChannels with the
    pre-2026-05 `phones_e164=[...]` kwarg must keep working — the model
    validator migrates the input into the new `phones` list."""
    from business_profile.schemas import ContactChannels

    channels = ContactChannels(phones_e164=["+201001234567", "+201007654321"])
    assert len(channels.phones) == 2
    assert all(p.e164 is not None for p in channels.phones)
    assert all(p.is_short_code is False for p in channels.phones)
    # The property still works
    assert channels.phones_e164 == ["+201001234567", "+201007654321"]


# =====================================================================
# Fix 2 — Restaurant CTA vocabulary
# =====================================================================

def test_restaurant_cta_anchors_are_detected():
    """All Buffalo Burger CTA anchors must classify as CTA."""
    from scraper.extractors.links import _is_cta_anchor

    must_be_cta = [
        "View Menu",
        "Menu",                       # exact-only
        "menu",                       # lowercase exact
        "Cart",
        "Go To Cart",
        "Call Us",
        "Call Us 19914",              # uses "call us" prefix
        "Download App",
        "Download our mobile application",  # uses "download" prefix
        "Delivery",
        "Order Online",
        # Arabic
        "اطلب دلوقتي",
        "حمل التطبيق",
    ]
    for anchor in must_be_cta:
        assert _is_cta_anchor(anchor) is True, f"expected CTA: {anchor!r}"


def test_menu_substring_is_not_cta_after_fix():
    """Critical: 'menu of the day' must NOT be a CTA. This was a real
    risk after adding 'menu' to the verb list."""
    from scraper.extractors.links import _is_cta_anchor

    must_not_be_cta = [
        "menu of the day",
        "Menu items",
        "Today's menu selection",
        "cart of the week",     # 'cart' is also exact-only
        "deliveryside",         # 'delivery' as a prefix of a longer word
    ]
    for anchor in must_not_be_cta:
        assert _is_cta_anchor(anchor) is False, (
            f"expected NOT CTA: {anchor!r}"
        )


def test_existing_ctas_still_detected_after_vocab_expansion():
    """Regression guard for the original CTA verbs."""
    from scraper.extractors.links import _is_cta_anchor

    for anchor in ("Book Now", "Subscribe", "Learn more about us", "Contact Us"):
        assert _is_cta_anchor(anchor) is True

    for anchor in ("About", "Home", "FAQ", ""):
        assert _is_cta_anchor(anchor) is False


# =====================================================================
# Fix 3 — Segment-based page-type classification
# =====================================================================

def test_branches_all_menu_classifies_as_menu():
    """The Buffalo Burger case: /branches/all/menu must be MENU.
    Before the segment-based fix, this was BRANCHES_LOCATIONS because
    'branches' is matched as a substring of the full path."""
    from scraper.classify.page_type import classify_url
    from scraper.schemas import PageTier, PageType

    pt, tier = classify_url("https://buffaloburger.com/branches/all/menu")
    assert pt == PageType.MENU
    assert tier == PageTier.HIGH


def test_segment_walk_finds_most_specific_first():
    """Confirm the last-segment-first ordering."""
    from scraper.classify.page_type import classify_url
    from scraper.schemas import PageType

    # /menu/category/burgers → last seg 'burgers' (no match) →
    # 'category' (no match) → 'menu' (MENU match)
    pt, _ = classify_url("https://x.example/menu/category/burgers")
    assert pt == PageType.MENU


def test_existing_single_segment_paths_unchanged():
    """Regression guard: single-segment paths must classify exactly as
    they did before the segment-based fix."""
    from scraper.classify.page_type import classify_url
    from scraper.schemas import PageType

    assert classify_url("https://x.example/branches")[0] == PageType.BRANCHES_LOCATIONS
    assert classify_url("https://x.example/our-branches")[0] == PageType.BRANCHES_LOCATIONS
    assert classify_url("https://x.example/menu")[0] == PageType.MENU
    assert classify_url("https://x.example/contact-us")[0] == PageType.CONTACT
    assert classify_url("https://x.example/about")[0] == PageType.ABOUT
    # Critical disambiguation cases from the original pattern ordering:
    assert classify_url("https://x.example/catering-services")[0] == PageType.CATERING
    assert classify_url("https://x.example/store-locator")[0] == PageType.BRANCHES_LOCATIONS


# =====================================================================
# Fix 4 — /offer-detail/* and cart-skip
# =====================================================================

def test_offer_detail_classifies_as_offers():
    from scraper.classify.page_type import classify_url
    from scraper.schemas import PageType

    assert classify_url("https://x.example/offer-detail/123")[0] == PageType.OFFERS
    assert classify_url("https://x.example/offer-detail")[0] == PageType.OFFERS
    # Regression
    assert classify_url("https://x.example/offers")[0] == PageType.OFFERS


def test_cart_and_checkout_pages_are_skip():
    """Transactional pages get SKIP tier so the crawler doesn't waste
    budget. They're still captured as CTA *links* via the verb list."""
    from scraper.classify.page_type import classify_url
    from scraper.schemas import PageTier

    for path in ("/cart", "/cart-details", "/checkout", "/basket",
                 "/my-cart", "/shopping-cart"):
        pt, tier = classify_url(f"https://x.example{path}")
        assert tier == PageTier.SKIP, f"{path} should be SKIP, got {tier}"


def test_shop_pages_still_classify_as_products():
    """Regression guard: adding the cart SKIP entry must not break /shop,
    /store, /shop-now, which contain 'shop'/'store' substrings."""
    from scraper.classify.page_type import classify_url
    from scraper.schemas import PageType

    assert classify_url("https://x.example/shop")[0] == PageType.PRODUCTS
    assert classify_url("https://x.example/shop-now")[0] == PageType.PRODUCTS
    assert classify_url("https://x.example/store")[0] == PageType.PRODUCTS


# =====================================================================
# Fix 5 — Cross-host sitemap rejection diagnostics
# =====================================================================

class _FakeResponse:
    def __init__(self, body: bytes):
        self._body = body
        self.headers = {}

    def read(self, n=None):
        return self._body if n is None else self._body[:n]

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


def test_cross_host_sitemap_rejected_with_clear_note():
    """When robots.txt at one host advertises a sitemap on a DIFFERENT
    canonical host, sitemap discovery must reject it AND note that
    rejection explicitly. No URLs from the foreign sitemap pollute the
    crawl queue, and the diagnostic clearly says why."""
    from scraper.schemas import RobotsRecord
    from scraper.sitemap import discover_sitemap_urls

    # Input host: buffaloburger.com
    # Sitemap directive: thebuffaloburger.com (different registrable host)
    robots = RobotsRecord(
        checked=True, allowed=True,
        sitemap_urls=["https://thebuffaloburger.com/sitemap.xml"],
    )

    # Even with urlopen mocked to "succeed," the cross-host guard must
    # reject before any fetch happens.
    def _should_not_fetch(req, timeout=None):
        raise AssertionError(
            "discover_sitemap_urls attempted to fetch a cross-host sitemap"
        )

    with patch("scraper.sitemap.open_public_url", _should_not_fetch):
        result = discover_sitemap_urls("https://buffaloburger.com/", robots)

    assert result.urls == []
    assert result.sitemap_used is False
    # Diagnostic note must clearly say why
    assert any(
        n.startswith("sitemap_cross_host_rejected:") for n in result.notes
    ), f"expected clear cross-host rejection note, got: {result.notes}"
    # And the rejected URL is NOT in sitemaps_attempted, because we never
    # tried it — that field should reflect honest history.
    assert "https://thebuffaloburger.com/sitemap.xml" not in result.sitemaps_attempted


def test_same_host_sitemap_still_works_regression():
    """Regression guard: sitemaps on the SAME host must still be fetched
    after the cross-host rejection logic."""
    from scraper.schemas import RobotsRecord
    from scraper.sitemap import discover_sitemap_urls

    urlset_xml = (
        b'<?xml version="1.0" encoding="UTF-8"?>\n'
        b'<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        b'<url><loc>https://buffaloburger.com/menu</loc></url>'
        b'</urlset>'
    )

    robots = RobotsRecord(
        checked=True, allowed=True,
        sitemap_urls=["https://buffaloburger.com/sitemap.xml"],
    )

    def _fake_urlopen(req, timeout=None):
        return _FakeResponse(urlset_xml)

    with patch("scraper.sitemap.open_public_url", _fake_urlopen):
        result = discover_sitemap_urls("https://buffaloburger.com/", robots)
    assert "https://buffaloburger.com/menu" in result.urls
    assert result.sitemap_used is True
