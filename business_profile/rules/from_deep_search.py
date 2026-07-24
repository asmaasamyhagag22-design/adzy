"""Deep-search (SERP) fallback fields — the LEGITIMATE recovery path for sites that block scraping.

Big consumer brands (Pepsi, Nike, …) sit behind enterprise bot protection (Imperva/Akamai/Cloudflare)
that returns a 403 block page to any headless browser. `scraper.deep_search` recovers the brand's
name / description / social links from web-search results (real source URLs) — and this module turns
that recovered data into CITED EvidencedFields so the profile build has something grounded to work
with.

SECONDARY by construction: the source is Google's index of the site, not the site's own DOM. So each
field is cited to the real SERP source URL and tagged `extractor="rule:deep_search:*"` — the audit
trail stays honest ("this came from web search"), and first-party extraction always wins over it
(the orchestrator only falls back to these when the primary extractors found nothing).
"""
from __future__ import annotations

from scraper.deep_search import _social_platform
from scraper.schemas import ScrapeManifest

from ..schemas import (
    Confidence, EvidencedField, EvidenceItem, SocialAccount, SourceType,
)
from .from_metadata import _pick_brand_segment, _registrable_label, _strip_chrome


def _source_url(manifest: ScrapeManifest) -> str:
    """The URL to cite: the brand's own listing (rank-1 SERP source), else the scraped URL."""
    ds = manifest.deep_search
    if ds and ds.sources:
        best = min(ds.sources, key=lambda s: s.rank if s.rank and s.rank > 0 else 999)
        if best.url:
            return best.url
    return manifest.scrape_meta.final_url or manifest.scrape_meta.normalized_url or ""


def extract_name_from_deep_search(manifest: ScrapeManifest) -> EvidencedField[str]:
    """Brand name from the recovered listing title, cleaned with the SAME chrome/segment logic the
    metadata name extractor uses ('Pepsi Official Site' -> 'Pepsi'). The raw title stays the quote."""
    ds = manifest.deep_search
    if not (ds and ds.used and ds.business_name and ds.business_name.strip()):
        return EvidencedField.missing()
    raw = ds.business_name.strip()
    dom = _registrable_label(_source_url(manifest))
    cleaned = _strip_chrome(_pick_brand_segment(raw, dom, legacy_shortest=True)) or raw
    if not cleaned:
        return EvidencedField.missing()
    return EvidencedField(
        value=cleaned,
        evidence=[EvidenceItem(block_id=None, page_url=_source_url(manifest),
                               quote=raw, extractor="rule:deep_search:name")],
        confidence=Confidence.MEDIUM,   # a cleaned name off the official listing is fairly reliable
        source_type=SourceType.EXTRACTED,
    )


def extract_description_from_deep_search(manifest: ScrapeManifest) -> EvidencedField[str]:
    """The site's indexed meta-description (recovered via search)."""
    ds = manifest.deep_search
    if not (ds and ds.used and ds.description and ds.description.strip()):
        return EvidencedField.missing()
    desc = ds.description.strip()
    return EvidencedField(
        value=desc,
        evidence=[EvidenceItem(block_id=None, page_url=_source_url(manifest),
                               quote=desc[:200], extractor="rule:deep_search:description")],
        confidence=Confidence.LOW,      # a SERP snippet, not the full page — treat as directional
        source_type=SourceType.EXTRACTED,
    )


def extract_social_from_deep_search(manifest: ScrapeManifest) -> list[SocialAccount]:
    """The brand's official social accounts, recovered from search (real profile URLs)."""
    ds = manifest.deep_search
    if not (ds and ds.used):
        return []
    home = _source_url(manifest)
    out: list[SocialAccount] = []
    seen: set[str] = set()
    for url in (ds.social_links or []):
        if not url or url in seen:
            continue
        seen.add(url)
        out.append(SocialAccount(
            platform=_social_platform(url) or "unknown",
            url=url,
            evidence=[EvidenceItem(block_id=None, page_url=home, quote=url,
                                   extractor="rule:deep_search:social")],
        ))
    return out
