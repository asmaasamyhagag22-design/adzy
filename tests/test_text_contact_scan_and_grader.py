"""Tests for PR-text-contact-scan + grader correctness fixes (2026-05).

Two fixes ship together:

  Fix A1.1 — `business_profile/rules/from_contact.py` text-scanning pass.
  Catches phone numbers and emails that appear as plain text in manifest
  TextBlocks but weren't extracted by the scraper's href-only path.
  Critical safety property: false positives like "© 2018-2024" and build
  hashes must NOT become phone records.

  Fix A1.2 — `benchmark/graders.py` name-grading robustness.
  Three corrections to grade_name:
    - diacritic stripping (Zööba → zooba, matches)
    - non-Latin script fallback (Arabic brand name matches via URL slug)
    - smarter chrome detection (separator + chrome words, not length ratio)

Diagnosed from the 14-URL benchmark run on 2026-05-30:
  - 10 URLs scored 0.0 on contact_phone, 5 on contact_email
  - 6 URLs scored 0.0-0.5 on name (3 were grader bugs, not extractor bugs)
"""
from __future__ import annotations

import sys
import types
from datetime import datetime, timezone

_fake = types.ModuleType("playwright")
_fake_sync = types.ModuleType("playwright.sync_api")
class _Stub: ...
for _n in ("Browser", "BrowserContext", "Page", "sync_playwright"):
    setattr(_fake_sync, _n, _Stub)
_fake_sync.TimeoutError = type("TimeoutError", (Exception,), {})
_fake_sync.Error = type("Error", (Exception,), {})
sys.modules.setdefault("playwright", _fake)
sys.modules.setdefault("playwright.sync_api", _fake_sync)


# benchmark.graders / benchmark.report are planned but not yet built (the full
# spec lives in tests/test_benchmark_graders, which importorskips them). The
# grade_* tests below import benchmark.graders, so skip them cleanly until it
# lands instead of failing the suite — matching that sibling file's documented
# intent. Once benchmark/graders.py exists, these run automatically.
import importlib.util  # noqa: E402

import pytest  # noqa: E402

requires_graders = pytest.mark.skipif(
    importlib.util.find_spec("benchmark.graders") is None,
    reason="benchmark.graders not built yet (planned; spec in tests/test_benchmark_graders)",
)


# =====================================================================
# Fix A1.1 — text-scanning extraction in from_contact.py
# =====================================================================

def test_text_scan_full_international_phone():
    """Egyptian-formatted phone in plain text gets extracted to E.164."""
    from business_profile.rules.from_contact import _scan_text_for_phones

    out = _scan_text_for_phones("Call us +201001234567 weekdays 9am-5pm", "EG")
    assert "+201001234567" in out


def test_text_scan_egyptian_local_format():
    """Local Egyptian format (no country code) gets extracted when region=EG."""
    from business_profile.rules.from_contact import _scan_text_for_phones

    # 010 0123 4567 is a real Egyptian mobile format
    out = _scan_text_for_phones("Call us 010 0123 4567", "EG")
    assert "+201001234567" in out


def test_text_scan_international_phone_with_plus_prefix():
    """Phone with explicit + prefix gets caught regardless of region pass."""
    from business_profile.rules.from_contact import _scan_text_for_phones

    out = _scan_text_for_phones("UK office: +44 20 7946 0958", "EG")
    # Either of two valid E.164 formats — phonenumbers picks one
    assert any(x.startswith("+44") for x in out), (
        f"expected UK phone, got: {out}"
    )


def test_text_scan_rejects_copyright_year():
    """The 2018-2024 false-positive that bit us on Elkbabgi.
    "© 2018-2024" must NOT be extracted as a phone."""
    from business_profile.rules.from_contact import _scan_text_for_phones

    out = _scan_text_for_phones("© 2018-2024 Qasr Elkbabgi, Inc", "EG")
    assert out == [], f"copyright year leaked as phone: {out}"


def test_text_scan_rejects_tracking_ids():
    """Build hashes, order IDs, tracking numbers must not match as phones."""
    from business_profile.rules.from_contact import _scan_text_for_phones

    out = _scan_text_for_phones("Order #1779730170 placed at 1747217859", "EG")
    assert out == [], f"tracking IDs leaked as phones: {out}"


def test_text_scan_us_formatted_phone_requires_us_context():
    """A local US number is valid only when the caller supplies US context."""
    from business_profile.rules.from_contact import _scan_text_for_phones

    text = (
        "WhatsApp Us  Show QR Code to Chat  Start WhatsApp Chat  or "
        "Call us  (888) 631-7760  Available Mon-Fri, 8:00 AM - 6:00 PM"
    )
    assert _scan_text_for_phones(text, region="EG") == []
    out = _scan_text_for_phones(text, region="US")
    assert "+18886317760" in out, (
        f"US-formatted phone should be extracted, got: {out}"
    )


def test_text_scan_us_region_does_not_create_false_positives():
    """Regression guard for Fix C1: adding the US region pass must NOT
    extract phone numbers from real site text snippets we've seen fail
    in actual benchmark manifests.

    These are concrete examples from the 2026-05-30 run that should
    stay empty after extending the region list."""
    from business_profile.rules.from_contact import _scan_text_for_phones

    fake_strings = [
        # Elkbabgi copyright year
        "© 2018-2024 Qasr Elkbabgi, Inc",
        # GA / pixel tracking IDs (15-digit IDs found in McDonald's HTML)
        "Google Analytics: 776909369139028",
        # Shipping/operating hours
        "Available Mon-Fri, 8:00 AM - 6:00 PM",
        "🚚 In a hurry? Place your order between 8 AM and 12 PM",
        # Addresses
        "Visit our office at 13 El.Nozha St., 4th floor, Cairo 11431",
        # Date ranges, SKUs
        "Active 2017-2026 across 39 countries",
        "Order item #SKU-555-1234 placed Tuesday",
    ]
    for text in fake_strings:
        out = _scan_text_for_phones(text, region="EG")
        assert out == [], (
            f"Fix C1 introduced false positive on: {text!r}\n"
            f"  extracted phones: {out}"
        )


def test_text_scan_short_code_hotline_with_trigger():
    """A 5-digit hotline in a line containing 'Call us' gets extracted."""
    from business_profile.rules.from_contact import _scan_text_for_short_codes

    out = _scan_text_for_short_codes("Call us 19914 for orders")
    assert "19914" in out


def test_text_scan_short_code_with_arabic_trigger():
    """'اتصل' (Arabic 'call') triggers short-code extraction."""
    from business_profile.rules.from_contact import _scan_text_for_short_codes

    out = _scan_text_for_short_codes("اتصل بنا 19914 للطلبات")
    assert "19914" in out


def test_text_scan_short_code_rejects_year_without_trigger():
    """Year/digit sequences with no trigger word return empty."""
    from business_profile.rules.from_contact import _scan_text_for_short_codes

    out = _scan_text_for_short_codes("© 2018-2024 all rights reserved")
    assert out == []


def test_text_scan_short_code_rejects_order_numbers():
    """Numbers in shipping/order context (no contact trigger) don't match."""
    from business_profile.rules.from_contact import _scan_text_for_short_codes

    # 'shipping' and 'placed' aren't in the trigger list
    out = _scan_text_for_short_codes("Tracking #12345 - shipping today")
    assert out == []


def test_text_scan_short_code_strips_too_long():
    """A 10-digit sequence in a 'call us' context is NOT a short code."""
    from business_profile.rules.from_contact import _scan_text_for_short_codes

    # A 10-digit number in a 'call us' line — this is a full phone,
    # not a short code. The short-code matcher's regex requires 3-7 digits
    # AND uses negative-lookbehind/lookahead to forbid adjacent digits.
    # So '0100123456' (10 digits) is NOT matched.
    out = _scan_text_for_short_codes("Call us at 0100123456 weekdays")
    # The matcher requires (?<!\d)(\d{3,7})(?!\d) — no match because the
    # full 10-digit string has no 3-7-digit slice with non-digit boundaries
    assert out == []


# =====================================================================
# PR-D / 2026-06 — real-data pollution-pattern tests
# =====================================================================
# These tests use ACTUAL text snippets from the 2026-06-01 benchmark
# manifests (alameda, AUC SCE, Buffalo Burger, Glamira, etc.). They are
# the patterns the previous short-code logic over-matched on.

def test_pollution_postal_code_in_address_rejected():
    """AUC SCE: 'Cairo 11835, Egypt Hotline 16723' has the hotline 16723
    in one clause and a postal code 11835 in another. Only 16723 should
    be extracted."""
    from business_profile.rules.from_contact import _scan_text_for_short_codes

    text = "P.O. Box 74 New Cairo 11835, Egypt Hotline 16723"
    out = _scan_text_for_short_codes(text)
    assert out == ["16723"], f"postal code leaked: {out}"


def test_pollution_arabic_postal_code_rejected():
    """AUC SCE Arabic equivalent — '11835، مصر الخط الساخن 16723'.
    The clause-split must work for Arabic comma '،' as well as ASCII ','."""
    from business_profile.rules.from_contact import _scan_text_for_short_codes

    text = "القاهرة 11835، مصر الخط الساخن 16723"
    out = _scan_text_for_short_codes(text)
    assert out == ["16723"], f"Arabic postal code leaked: {out}"


def test_pollution_egp_price_rejected():
    """Buffalo Burger cart: 'EGP 159.00 ... Call Us 19914'.
    Only the hotline should be extracted — the price '159' is below
    5-digit minimum anyway, but this test guards against future regex
    changes that might allow shorter sequences."""
    from business_profile.rules.from_contact import _scan_text_for_short_codes

    text = "Total Including VAT EGP 159.00 Submit Feedback Call Us 19914 CART"
    out = _scan_text_for_short_codes(text)
    assert out == ["19914"]


def test_pollution_hyphenated_tax_number_rejected():
    """As-Salam footer: 'Tax No: 298-758-970 Developed by Secret Agency'.
    Each fragment (298, 758, 970) is 3 digits — below minimum 5 — but
    also hyphenated. Double-rejected. No trigger word, so no extraction
    even without the hyphen guard."""
    from business_profile.rules.from_contact import _scan_text_for_short_codes

    text = "All Rights Reserved ASSIH. Tax No: 298-758-970 Developed by Secret Agency"
    out = _scan_text_for_short_codes(text)
    assert out == []


def test_pollution_buffalo_full_footer_only_hotline():
    """Real Buffalo Burger footer text. Block contains 'Talk to us 19914'
    AND copyright year AND many prices. Only the hotline should extract."""
    from business_profile.rules.from_contact import _scan_text_for_short_codes

    text = (
        "Login Signup Talk to us 19914 العربية © 2022 Buffalo Burger inc. "
        "Submit Feedback Call Us 19914 CART DETAILS"
    )
    out = _scan_text_for_short_codes(text)
    assert out == ["19914"]


def test_pollution_glamira_us_phone_no_short_code_substrings():
    """End-to-end manifest test: when the full E.164 phone +18886317760
    is extracted via the US-region pass, the short-code scanner must not
    leak fragments 888, 631, 7760 alongside it. Substring dedup (D3)."""
    from datetime import datetime, timezone
    from business_profile.rules.from_contact import extract_contact
    from scraper.schemas import (
        ContactInfo, PageRecord, PageTier, PageType, ReadinessReport,
        RobotsRecord, ScrapeManifest, ScrapeMeta, Section, SiteMetadata,
        TextBlock,
    )

    manifest = ScrapeManifest(
        scrape_meta=ScrapeMeta(
            input_url="https://x.us/",
            normalized_url="https://x.us/",
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
        contact=ContactInfo(),
        pages=[
            PageRecord(
                url="https://x.us/contact",
                final_url="https://x.us/contact",
                page_type=PageType.CONTACT,
                tier=PageTier.HIGH,
                fetched_at=datetime.now(timezone.utc),
                http_status=200, fetch_duration_ms=1000,
                viewport_width=1366, viewport_height=800,
                text_blocks=[
                    TextBlock(
                        block_id="b1",
                        page_url="https://x.us/contact",
                        tag="p",
                        # Real Glamira manifest text:
                        text=("WhatsApp Us\nShow QR Code to Chat\n"
                              "Start WhatsApp Chat\nor\n"
                              "Call us\n(888) 631-7760\n"
                              "Available Mon-Fri, 8:00 AM - 6:00 PM"),
                        section=Section.MAIN,
                        selector="body > p",
                    ),
                ],
            ),
        ],
    )
    channels = extract_contact(manifest)
    # Should contain ONLY the full E.164, no fragments
    e164s = [p.e164 for p in channels.phones if p.e164]
    short_codes = [p.raw for p in channels.phones if p.is_short_code]
    assert "+18886317760" in e164s, f"US phone missing: {channels.phones}"
    # No 888/631/7760 substring fragments should leak
    for sc in short_codes:
        assert sc not in "18886317760", (
            f"fragment short code {sc!r} leaked alongside full E.164. "
            f"All phones: {channels.phones}"
        )


def test_pollution_hotline_with_colon_kept():
    """As-Salam: 'HOTLINE: 19885' must still extract — colon is NOT a
    clause separator, so the trigger and digits stay in the same clause."""
    from business_profile.rules.from_contact import _scan_text_for_short_codes

    out = _scan_text_for_short_codes("HOTLINE: 19885")
    assert out == ["19885"]


def test_pollution_arabic_appointment_booking():
    """Andalusia: 'حجز موعد 16781 البريد' — 'حجز موعد' (book appointment)
    is the trigger phrase. The hotline 16781 follows."""
    from business_profile.rules.from_contact import _scan_text_for_short_codes

    out = _scan_text_for_short_codes("حجز موعد 16781 البريد الإلكتروني")
    assert "16781" in out


def test_pollution_clause_split_isolates_unrelated_digits():
    """A block with two clauses, only one containing a trigger — the
    5-digit in the no-trigger clause stays unextracted."""
    from business_profile.rules.from_contact import _scan_text_for_short_codes

    # Two clauses: "Visit Cairo 12345" (no trigger), "Hotline 67890" (trigger)
    text = "Visit Cairo 12345. Hotline 67890"
    out = _scan_text_for_short_codes(text)
    assert out == ["67890"], f"clause isolation broken: {out}"


def test_pollution_year_alone_does_not_match_5_digits():
    """A 4-digit copyright year like '2026' cannot match because the
    regex requires exactly 5 digits."""
    from business_profile.rules.from_contact import _scan_text_for_short_codes

    out = _scan_text_for_short_codes("Call us anytime © 2026")
    assert out == []


def test_text_scan_email_extracted():
    """Standard email in plain text gets extracted."""
    from business_profile.rules.from_contact import _scan_text_for_emails

    out = _scan_text_for_emails("Reach us at info@example.com for questions")
    assert "info@example.com" in out


def test_text_scan_email_rejects_image_at_filename():
    """Logo paths like '/static/logo@2x.png' must NOT match as email."""
    from business_profile.rules.from_contact import _scan_text_for_emails

    out = _scan_text_for_emails("Image at /static/logo@2x.png loads fast")
    assert out == [], f"image filename matched as email: {out}"


def test_text_scan_email_dedupes():
    """Same email appearing 3 times in the text yields 1 record."""
    from business_profile.rules.from_contact import _scan_text_for_emails

    text = "info@x.com or info@x.com or visit info@x.com"
    out = _scan_text_for_emails(text)
    assert out == ["info@x.com"]


def test_extract_contact_text_scan_merges_into_phones():
    """End-to-end: a manifest with a TextBlock containing a phone in plain
    text produces a PhoneChannel via the text-scan path. Doesn't require
    any href links."""
    from datetime import datetime, timezone
    from business_profile.rules.from_contact import extract_contact
    from scraper.schemas import (
        ContactInfo, PageRecord, PageTier, PageType, ReadinessReport,
        RobotsRecord, ScrapeManifest, ScrapeMeta, Section, SiteMetadata,
        TextBlock,
    )

    manifest = ScrapeManifest(
        scrape_meta=ScrapeMeta(
            input_url="https://x.example/",
            normalized_url="https://x.example/",
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
        contact=ContactInfo(),  # no href-derived phones
        pages=[
            PageRecord(
                url="https://x.example/contact",
                final_url="https://x.example/contact",
                page_type=PageType.CONTACT,
                tier=PageTier.HIGH,
                fetched_at=datetime.now(timezone.utc),
                http_status=200, fetch_duration_ms=1000,
                viewport_width=1366, viewport_height=800,
                text_blocks=[
                    TextBlock(
                        block_id="b1",
                        page_url="https://x.example/contact",
                        tag="p",
                        text="For inquiries call us +201001234567 or email info@x.example",
                        section=Section.MAIN,
                        selector="body > p",
                    ),
                ],
            ),
        ],
    )
    channels = extract_contact(manifest)
    # Phone should have been picked up
    assert any(p.e164 == "+201001234567" for p in channels.phones), (
        f"text-scan phone missing: {[(p.e164, p.raw) for p in channels.phones]}"
    )
    # Email should have been picked up
    assert "info@x.example" in channels.emails


def test_extract_contact_text_scan_does_not_duplicate_href_phones():
    """If the manifest already has a phone from a tel: href, the text-scan
    pass must not duplicate it even if it also appears in text."""
    from datetime import datetime, timezone
    from business_profile.rules.from_contact import extract_contact
    from scraper.schemas import (
        ContactInfo, PageRecord, PageTier, PageType, PhoneRecord,
        ReadinessReport, RobotsRecord, ScrapeManifest, ScrapeMeta,
        Section, SiteMetadata, TextBlock,
    )

    manifest = ScrapeManifest(
        scrape_meta=ScrapeMeta(
            input_url="https://x.example/",
            normalized_url="https://x.example/",
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
                e164="+201001234567", raw="+20 100 123 4567",
                is_short_code=False, evidence_block_id=None,
                page_url="https://x.example/",
            ),
        ]),
        pages=[
            PageRecord(
                url="https://x.example/contact",
                final_url="https://x.example/contact",
                page_type=PageType.CONTACT,
                tier=PageTier.HIGH,
                fetched_at=datetime.now(timezone.utc),
                http_status=200, fetch_duration_ms=1000,
                viewport_width=1366, viewport_height=800,
                text_blocks=[
                    TextBlock(
                        block_id="b1",
                        page_url="https://x.example/contact",
                        tag="p",
                        text="Call us +201001234567",
                        section=Section.MAIN,
                        selector="body > p",
                    ),
                ],
            ),
        ],
    )
    channels = extract_contact(manifest)
    # Exactly one phone — the text-scan must not duplicate the href-derived one
    assert len(channels.phones) == 1
    assert channels.phones[0].e164 == "+201001234567"


# =====================================================================
# Fix A1.2 — grader correctness for name field
# =====================================================================

@requires_graders
def test_grader_diacritic_normalization():
    """'Zööba' (with umlauts) matches expected 'Zooba' via NFKD."""
    from benchmark.graders import _norm
    assert _norm("Zööba") == "zooba"
    assert _norm("Café") == "cafe"
    assert _norm("résumé") == "resume"


@requires_graders
def test_grader_chrome_detection_with_pipe_separator():
    """Brand with chrome after pipe gets cleanly peeled."""
    from benchmark.graders import _strip_chrome
    cleaned, was_chrome = _strip_chrome("buffalo burger | online ordering")
    assert was_chrome is True
    assert cleaned == "buffalo burger"


@requires_graders
def test_grader_chrome_detection_with_dash_separator():
    """Brand with chrome after dash gets cleanly peeled."""
    from benchmark.graders import _strip_chrome
    cleaned, was_chrome = _strip_chrome(
        "raw african's beauty hub - get the raw experience!"
    )
    assert was_chrome is True
    assert "raw african" in cleaned


@requires_graders
def test_grader_no_chrome_on_institutional_name():
    """'AUC School of Continuing Education' has no chrome — full name is the brand."""
    from benchmark.graders import _strip_chrome
    cleaned, was_chrome = _strip_chrome("auc school of continuing education")
    assert was_chrome is False
    assert cleaned == "auc school of continuing education"


@requires_graders
def test_grader_zooba_with_umlauts_matches():
    """The real failure case: 'Zööba' as extracted name should match the brand."""
    from datetime import datetime, timezone
    from benchmark.graders import grade_name
    from business_profile.schemas import (
        BusinessProfile, ContactChannels, Confidence, EvidencedField,
        ExtractionMeta, ProfileQuality, SourceType,
    )

    profile = BusinessProfile(
        source_url="https://zoobaeats.com/",
        name=EvidencedField(
            value="Zööba", source_type=SourceType.EXTRACTED,
            confidence=Confidence.HIGH,
        ),
        extraction_meta=ExtractionMeta(
            manifest_path="x.json", extracted_at=datetime.now(timezone.utc),
        ),
        quality=ProfileQuality(
            has_name=True, has_category=False, has_offerings=False,
            has_audience=False, has_value_propositions=False, has_tone=False,
            has_contact=False, has_visual=False,
            fields_extracted=1, fields_inferred=0, fields_missing=7,
            ready_for_strategy=False,
        ),
    )
    url_meta = {
        "id": "zooba", "brand": "Zooba", "vertical": "restaurant",
        "tier": "messy", "expected_languages": ["en"],
    }
    g = grade_name(profile, url_meta)
    assert g.score == 1.0


@requires_graders
def test_grader_arabic_name_matches_via_url_slug():
    """A profile whose extracted name is in Arabic matches the brand
    when the URL id contains a substantial brand token. The real
    failure case: Andalusia extracted as 'مستشفى أندلسية'."""
    from datetime import datetime, timezone
    from benchmark.graders import grade_name
    from business_profile.schemas import (
        BusinessProfile, Confidence, EvidencedField,
        ExtractionMeta, ProfileQuality, SourceType,
    )

    profile = BusinessProfile(
        source_url="https://andalusiaegypt.com/",
        name=EvidencedField(
            value="مستشفى أندلسية", source_type=SourceType.EXTRACTED,
            confidence=Confidence.HIGH,
        ),
        extraction_meta=ExtractionMeta(
            manifest_path="x.json", extracted_at=datetime.now(timezone.utc),
        ),
        quality=ProfileQuality(
            has_name=True, has_category=False, has_offerings=False,
            has_audience=False, has_value_propositions=False, has_tone=False,
            has_contact=False, has_visual=False,
            fields_extracted=1, fields_inferred=0, fields_missing=7,
            ready_for_strategy=False,
        ),
    )
    url_meta = {
        "id": "andalusia", "brand": "Andalusia Hospitals Egypt",
        "vertical": "clinic", "tier": "messy",
        "expected_languages": ["ar"],
    }
    g = grade_name(profile, url_meta)
    assert g.score == 1.0


@requires_graders
def test_grader_institutional_full_name_not_penalized():
    """AUC SCE case: extracted 'AUC School of Continuing Education' is the
    full institutional name, not chrome. Should score 1.0."""
    from datetime import datetime, timezone
    from benchmark.graders import grade_name
    from business_profile.schemas import (
        BusinessProfile, Confidence, EvidencedField,
        ExtractionMeta, ProfileQuality, SourceType,
    )

    profile = BusinessProfile(
        source_url="https://sce.aucegypt.edu/",
        name=EvidencedField(
            value="AUC School of Continuing Education",
            source_type=SourceType.EXTRACTED,
            confidence=Confidence.HIGH,
        ),
        extraction_meta=ExtractionMeta(
            manifest_path="x.json", extracted_at=datetime.now(timezone.utc),
        ),
        quality=ProfileQuality(
            has_name=True, has_category=False, has_offerings=False,
            has_audience=False, has_value_propositions=False, has_tone=False,
            has_contact=False, has_visual=False,
            fields_extracted=1, fields_inferred=0, fields_missing=7,
            ready_for_strategy=False,
        ),
    )
    url_meta = {
        "id": "auc_sce", "brand": "AUC SCE",
        "vertical": "education", "tier": "typical",
        "expected_languages": ["en"],
    }
    g = grade_name(profile, url_meta)
    assert g.score == 1.0


@requires_graders
def test_grader_wrong_brand_still_fails():
    """Regression guard: an unrelated extracted name still fails grading."""
    from datetime import datetime, timezone
    from benchmark.graders import grade_name
    from business_profile.schemas import (
        BusinessProfile, Confidence, EvidencedField,
        ExtractionMeta, ProfileQuality, SourceType,
    )

    profile = BusinessProfile(
        source_url="https://x.example/",
        name=EvidencedField(
            value="Totally Different Company",
            source_type=SourceType.EXTRACTED,
            confidence=Confidence.HIGH,
        ),
        extraction_meta=ExtractionMeta(
            manifest_path="x.json", extracted_at=datetime.now(timezone.utc),
        ),
        quality=ProfileQuality(
            has_name=True, has_category=False, has_offerings=False,
            has_audience=False, has_value_propositions=False, has_tone=False,
            has_contact=False, has_visual=False,
            fields_extracted=1, fields_inferred=0, fields_missing=7,
            ready_for_strategy=False,
        ),
    )
    url_meta = {
        "id": "elkbabgi", "brand": "Qasr Elkbabgi",
        "vertical": "restaurant", "tier": "typical",
        "expected_languages": ["en", "ar"],
    }
    g = grade_name(profile, url_meta)
    assert g.score == 0.0
