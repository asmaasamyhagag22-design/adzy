"""deep_search -> profile wiring: a bot-blocked scrape that recovered SERP evidence must yield a
grounded (SECONDARY) profile instead of a hard stop — while first-party extraction still wins and
the moat holds (every recovered field cites a real source URL, tagged rule:deep_search).

This is the fix for the Pepsi case: pepsi.com 403s every headless browser (Imperva), deep_search
recovers name/description/social from web search, and that recovery now makes the scrape 'ready' and
becomes cited profile fields.
"""
from __future__ import annotations

from datetime import datetime, timezone

from scraper.schemas import (
    DeepSearchResult, DeepSearchSource, ReadinessReport, RobotsRecord,
    ScrapeManifest, ScrapeMeta, SiteMetadata,
)
from scraper.readiness import compute_readiness
from business_profile.rules.orchestrator import apply_rules
from business_profile.schemas import SourceType


def _deep_search(**kw) -> DeepSearchResult:
    base = dict(
        used=True, provider="serper", query="acme",
        business_name="Acme Official Site",
        description="We make premium widgets for every workshop.",
        social_links=["https://www.instagram.com/acme/", "https://www.facebook.com/AcmeInc/"],
        sources=[DeepSearchSource(title="Acme Official Site", url="https://acme.com/",
                                  snippet="premium widgets", rank=1)],
    )
    base.update(kw)
    return DeepSearchResult(**base)


def _manifest(*, deep_search=None, og_site_name=None) -> ScrapeManifest:
    return ScrapeManifest(
        scrape_meta=ScrapeMeta(input_url="https://acme.com/", normalized_url="https://acme.com/",
                               scraped_at=datetime.now(timezone.utc), final_url=None),
        robots=RobotsRecord(checked=True, allowed=True),
        # placeholder — compute_readiness recomputes; apply_rules reads deep_search, not this
        readiness=ReadinessReport(has_homepage=False, has_internal_pages=False,
                                  has_contact_signals=False, has_visual_signals=False,
                                  has_cta_candidates=False, has_metadata=False,
                                  ready_for_extraction=False),
        site_metadata=SiteMetadata(og_site_name=og_site_name),
        deep_search=deep_search,
    )


# ---- readiness ----

def test_deep_search_recovery_makes_scrape_ready():
    m = _manifest(deep_search=_deep_search())
    assert compute_readiness(m).ready_for_extraction is True


def test_no_deep_search_and_no_homepage_stays_not_ready():
    # regression guard: nothing recovered -> still a hard stop (don't build from thin air)
    assert compute_readiness(_manifest()).ready_for_extraction is False
    # an unused/empty deep_search must NOT flip readiness
    assert compute_readiness(_manifest(
        deep_search=DeepSearchResult(used=True))).ready_for_extraction is False


# ---- profile build ----

def test_name_recovered_and_cleaned_from_deep_search():
    prof = apply_rules(_manifest(deep_search=_deep_search()))
    assert prof.name.value == "Acme"                      # 'Acme Official Site' -> 'Acme'
    ev = prof.name.evidence[0]
    assert ev.extractor == "rule:deep_search:name"
    assert ev.page_url == "https://acme.com/"             # a REAL source URL (the moat)
    assert prof.name.source_type == SourceType.EXTRACTED


def test_description_and_social_recovered_and_cited():
    prof = apply_rules(_manifest(deep_search=_deep_search()))
    assert prof.description.value.startswith("We make premium widgets")
    assert prof.description.evidence[0].extractor == "rule:deep_search:description"
    plats = {s.platform for s in prof.social_presence}
    assert {"instagram", "facebook"} <= plats
    assert all(s.evidence[0].extractor == "rule:deep_search:social" for s in prof.social_presence)
    assert "name:deep_search" in prof.extraction_meta.rule_extractors_used


def test_first_party_name_wins_over_deep_search():
    # a real og:site_name must NOT be overridden by the secondary deep_search name
    prof = apply_rules(_manifest(deep_search=_deep_search(), og_site_name="Acme Widgets"))
    assert prof.name.value == "Acme Widgets"
    assert prof.name.evidence[0].extractor != "rule:deep_search:name"


def test_moat_every_recovered_field_cites_a_real_url():
    prof = apply_rules(_manifest(deep_search=_deep_search()))
    fields = [prof.name, prof.description] + list(prof.social_presence)
    for f in fields:
        assert f.evidence, "a recovered field with no citation would break the moat"
        for ev in f.evidence:
            assert ev.page_url.startswith("http")
            assert ev.extractor.startswith("rule:deep_search")
