"""Phase 15: country evidence must not be invented by phone normalization.

Hermetic behavioral coverage for the complete boundary chain:
scraper contact -> BusinessProfile -> merger -> EvidenceLedger -> BrandStore -> locale.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from api.brand_store import BrandStore
from api.brand_translate import profile_to_brand
from business_profile.llm.validator import ValidatedPayload, ValidationDiagnostics
from business_profile.merger import merge_profile
from business_profile.rules.from_contact import extract_contact as extract_profile_contact
from business_profile.rules.orchestrator import apply_rules
from grounding.ledger import EvidenceLedger
from poster.locale import arabic_dialect_for, country_of
from scraper.extractors.contact import extract_contact as extract_scrape_contact
from scraper.schemas import (
    ContactInfo,
    LanguageEntry,
    PageRecord,
    PageTier,
    PageType,
    ReadinessReport,
    RobotsRecord,
    ScrapeManifest,
    ScrapeMeta,
    Section,
    TextBlock,
)


def _manifest(
    site_url: str,
    *,
    contact: ContactInfo | None = None,
    text: str = "",
    language: str = "ar",
) -> ScrapeManifest:
    pages = []
    if text:
        page_url = site_url.rstrip("/") + "/contact"
        pages = [
            PageRecord(
                url=page_url,
                final_url=page_url,
                http_status=200,
                fetched_at=datetime.now(timezone.utc),
                fetch_duration_ms=1,
                page_type=PageType.CONTACT,
                tier=PageTier.HIGH,
                viewport_width=1280,
                viewport_height=720,
                text_blocks=[
                    TextBlock(
                        block_id="contact_phone_1",
                        page_url=page_url,
                        tag="p",
                        text=text,
                        section=Section.MAIN,
                        selector="main > p",
                    )
                ],
            )
        ]
    return ScrapeManifest(
        scrape_meta=ScrapeMeta(
            input_url=site_url,
            normalized_url=site_url,
            final_url=site_url,
            scraped_at=datetime.now(timezone.utc),
        ),
        robots=RobotsRecord(checked=True, allowed=True),
        readiness=ReadinessReport(
            has_homepage=True,
            has_internal_pages=False,
            has_contact_signals=True,
            has_visual_signals=False,
            has_cta_candidates=False,
            has_metadata=False,
            ready_for_extraction=True,
        ),
        languages=[LanguageEntry(code=language, proportion=1.0)],
        contact=contact or ContactInfo(),
        pages=pages,
    )


def _scraped_tel(site_url: str, raw: str) -> ContactInfo:
    page_url = site_url.rstrip("/") + "/contact"
    return extract_scrape_contact(
        {page_url: f'<a href="tel:{raw}">Call</a>'},
        {page_url: []},
        site_url=site_url,
    )


def _empty_payload() -> ValidatedPayload:
    return ValidatedPayload(
        identity=None,
        offerings=None,
        positioning=None,
        trust=None,
        diagnostics=ValidationDiagnostics(),
    )


def test_unknown_country_local_phone_stays_raw_through_full_locale_chain():
    raw = "01012581929"
    scraped = _scraped_tel("https://brand.example/", raw)

    assert len(scraped.phones) == 1
    assert scraped.phones[0].raw == raw
    assert scraped.phones[0].e164 is None
    assert scraped.phones[0].is_short_code is False

    rules_profile = apply_rules(_manifest("https://brand.example/", contact=scraped))
    assert rules_profile.contact_channels.phones[0].raw == raw
    assert rules_profile.contact_channels.phones[0].e164 is None
    assert country_of(rules_profile) == ""
    assert "Egyptian" not in arabic_dialect_for(rules_profile)
    assert "natural conversational Arabic" in arabic_dialect_for(rules_profile)


@pytest.mark.parametrize(
    ("site_url", "raw", "expected", "country"),
    [
        ("https://brand.sa/", "0501234567", "+966501234567", "Saudi Arabia"),
        ("https://brand.ae/", "0501234567", "+971501234567", "the UAE"),
        ("https://brand.eg/", "01012581929", "+201012581929", "Egypt"),
    ],
)
def test_cctld_country_evidence_normalizes_local_phone(
    site_url: str, raw: str, expected: str, country: str,
):
    scraped = _scraped_tel(site_url, raw)
    assert [p.e164 for p in scraped.phones] == [expected]
    assert not any((p.e164 or "").startswith("+20") for p in scraped.phones) or country == "Egypt"

    profile = apply_rules(_manifest(site_url, contact=scraped))
    assert country_of(profile) == country
    dialect = arabic_dialect_for(profile)
    if country == "Egypt":
        assert "Egyptian Arabic" in dialect
    else:
        assert "Egyptian" not in dialect
        assert "Gulf/Khaleeji Arabic" in dialect


@pytest.mark.parametrize(
    ("international", "country"),
    [
        ("+966501234567", "Saudi Arabia"),
        ("+971501234567", "the UAE"),
        ("+201012581929", "Egypt"),
    ],
)
def test_international_phone_is_region_independent(international: str, country: str):
    scraped = _scraped_tel("https://brand.example/", international)
    assert [p.e164 for p in scraped.phones] == [international]
    profile = apply_rules(_manifest("https://brand.example/", contact=scraped))
    assert country_of(profile) == country


def test_profile_text_scan_uses_site_evidence_not_a_global_region():
    unknown = extract_profile_contact(
        _manifest("https://brand.example/", text="Call us on 010 1258 1929")
    )
    unknown_us = extract_profile_contact(
        _manifest("https://brand.example/", text="Call us on (888) 631-7760")
    )
    egypt = extract_profile_contact(
        _manifest("https://brand.eg/", text="Call us on 010 1258 1929")
    )
    saudi = extract_profile_contact(
        _manifest("https://brand.sa/", text="Call us on 050 123 4567")
    )
    uae = extract_profile_contact(
        _manifest("https://brand.ae/", text="Call us on 050 123 4567")
    )

    assert not any((p.e164 or "").startswith("+20") for p in unknown.phones)
    assert not any((p.e164 or "").startswith("+1") for p in unknown_us.phones)
    assert [p.e164 for p in egypt.phones] == ["+201012581929"]
    assert [p.e164 for p in saudi.phones] == ["+966501234567"]
    assert [p.e164 for p in uae.phones] == ["+971501234567"]


def test_unknown_raw_phone_is_not_certified_as_egypt_by_merge_ledger_or_store(tmp_path):
    raw = "01012581929"
    scraped = _scraped_tel("https://brand.example/", raw)
    rules_profile = apply_rules(_manifest("https://brand.example/", contact=scraped))
    merged = merge_profile(rules_profile, _empty_payload(), expected_llm=False)
    dumped = merged.model_dump(mode="json")

    assert dumped["contact_channels"]["phones"] == [
        {"e164": None, "raw": raw, "is_short_code": False}
    ]
    assert country_of(dumped) == ""

    ledger = EvidenceLedger.from_profile(dumped)
    contact_evidence = [e.raw for e in ledger.entries if e.source_type == "profile:contact"]
    assert raw in contact_evidence
    assert not any(value.startswith("+20") for value in contact_evidence)

    brand = profile_to_brand(dumped, source_url="https://brand.example/")
    brand["_profile"] = dumped
    store = BrandStore(base=tmp_path / "brands")
    store.save(brand)
    loaded = store.get(brand["domain_key"])
    assert loaded is not None
    assert loaded["phone"] == raw
    assert loaded["_profile"]["contact_channels"]["phones"][0]["e164"] is None
    assert country_of(loaded["_profile"]) == ""


def test_unknown_site_still_preserves_short_code_without_country_inference():
    scraped = _scraped_tel("https://brand.example/", "19914")
    assert scraped.phones[0].raw == "19914"
    assert scraped.phones[0].e164 is None
    assert scraped.phones[0].is_short_code is True
    profile = apply_rules(_manifest("https://brand.example/", contact=scraped))
    assert country_of(profile) == ""
