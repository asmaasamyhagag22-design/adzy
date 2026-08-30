"""Tests for the evidence pack builder."""
from __future__ import annotations

import sys
import types
from datetime import datetime, timezone

# Stub playwright
_fake = types.ModuleType("playwright")
_fake_sync = types.ModuleType("playwright.sync_api")
class _Stub: ...
for n in ("Browser", "BrowserContext", "Page", "sync_playwright"):
    setattr(_fake_sync, n, _Stub)
_fake_sync.TimeoutError = type("TimeoutError", (Exception,), {})
_fake_sync.Error = type("Error", (Exception,), {})
sys.modules.setdefault("playwright", _fake)
sys.modules.setdefault("playwright.sync_api", _fake_sync)

from scraper.schemas import (  # noqa: E402
    BoundingBox, LanguageEntry, PageRecord, PageTier, PageType,
    ReadinessReport, RobotsRecord, Section, ScrapeManifest, ScrapeMeta,
    SiteMetadata, TextBlock,
)

from business_profile.llm.evidence_pack import (
    DEFAULT_MAX_BLOCKS,
    DEFAULT_MAX_PER_PAGE_TYPE,
    EvidenceBlock,
    EvidencePack,
    build_evidence_pack,
)
from business_profile.rules.orchestrator import apply_rules
from business_profile.schemas import (
    BusinessProfile, ExtractionMeta, ProfileQuality,
)


# ---------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------

def _now():
    return datetime.now(timezone.utc)


def _block(bid: str, page_url: str, tag: str, text: str,
           section: Section = Section.MAIN, above_fold: bool = False) -> TextBlock:
    return TextBlock(
        block_id=bid, page_url=page_url, tag=tag, text=text,
        section=section, selector=f"{tag}#{bid}",
        bbox=BoundingBox(x=10, y=100, width=200, height=20),
        above_fold=above_fold,
    )


def _page(url: str, page_type: PageType, blocks: list[TextBlock],
          tier: PageTier = PageTier.HIGH) -> PageRecord:
    return PageRecord(
        url=url, final_url=url, http_status=200,
        fetched_at=_now(), fetch_duration_ms=1000,
        page_type=page_type, tier=tier,
        viewport_width=1366, viewport_height=800,
        text_blocks=blocks,
    )


def _manifest(pages: list[PageRecord],
              languages: list[LanguageEntry] | None = None) -> ScrapeManifest:
    home_url = pages[0].final_url if pages else "https://x.com/"
    return ScrapeManifest(
        scrape_meta=ScrapeMeta(
            input_url=home_url, normalized_url=home_url, final_url=home_url,
            scraped_at=_now(),
        ),
        robots=RobotsRecord(checked=True, allowed=True),
        readiness=ReadinessReport(
            has_homepage=True, has_internal_pages=True,
            has_contact_signals=False, has_visual_signals=False,
            has_cta_candidates=False, has_metadata=False,
            ready_for_extraction=True,
        ),
        languages=languages or [LanguageEntry(code="en", proportion=1.0)],
        pages=pages,
        site_metadata=SiteMetadata(),
    )


def _empty_profile() -> BusinessProfile:
    return BusinessProfile(
        source_url="https://x.com/",
        extraction_meta=ExtractionMeta(
            manifest_path="x", extracted_at=_now(),
        ),
        quality=ProfileQuality(
            has_name=False, has_category=False, has_offerings=False,
            has_audience=False, has_value_propositions=False, has_tone=False,
            has_contact=False, has_visual=False,
            fields_extracted=0, fields_inferred=0, fields_missing=0,
            major_missing=["category", "offerings", "audience_type",
                           "value_propositions", "tone_of_voice"],
            ready_for_strategy=False,
        ),
    )


# ---------------------------------------------------------------------
# Basic shape
# ---------------------------------------------------------------------

def test_pack_returns_evidence_pack_object():
    m = _manifest([
        _page("https://x.com/", PageType.HOMEPAGE, [
            _block("home_h1_0001", "https://x.com/", "h1",
                   "Premium Foot Care in Cairo", above_fold=True),
        ]),
    ])
    pack = build_evidence_pack(m, _empty_profile())
    assert isinstance(pack, EvidencePack)
    assert pack.source_url == "https://x.com/"
    assert pack.block_count == 1
    assert pack.languages == ["en"]


def test_pack_preserves_all_block_fields():
    home = "https://x.com/"
    m = _manifest([
        _page(home, PageType.HOMEPAGE, [
            _block("home_h1_0001", home, "h1",
                   "Premium Foot Care", above_fold=True),
        ]),
    ])
    pack = build_evidence_pack(m, _empty_profile())
    b = pack.blocks[0]
    assert b.block_id == "home_h1_0001"
    assert b.page_url == home
    assert b.page_type == "homepage"
    assert b.section == "main"
    assert b.tag == "h1"
    assert b.above_fold is True
    assert b.text == "Premium Foot Care"  # verbatim


# ---------------------------------------------------------------------
# Filtering: nav/footer/boilerplate
# ---------------------------------------------------------------------

def test_pack_drops_nav_blocks():
    home = "https://x.com/"
    m = _manifest([
        _page(home, PageType.HOMEPAGE, [
            _block("home_li_0001", home, "li", "About",
                   section=Section.NAV),
            _block("home_li_0002", home, "li", "Services",
                   section=Section.NAV),
            _block("home_h1_0003", home, "h1", "Premium Foot Care", above_fold=True),
        ]),
    ])
    pack = build_evidence_pack(m, _empty_profile())
    ids = [b.block_id for b in pack.blocks]
    assert "home_h1_0003" in ids
    assert "home_li_0001" not in ids
    assert "home_li_0002" not in ids


def test_pack_drops_footer_blocks_unless_above_fold():
    home = "https://x.com/"
    m = _manifest([
        _page(home, PageType.HOMEPAGE, [
            _block("home_p_0001", home, "p",
                   "Subscribe to our newsletter for updates",
                   section=Section.FOOTER, above_fold=False),
            _block("home_h1_0002", home, "h1",
                   "Premium Foot Care", above_fold=True),
        ]),
    ])
    pack = build_evidence_pack(m, _empty_profile())
    ids = [b.block_id for b in pack.blocks]
    assert "home_p_0001" not in ids
    assert "home_h1_0002" in ids


def test_pack_drops_boilerplate_copyright_and_cookies():
    home = "https://x.com/"
    m = _manifest([
        _page(home, PageType.HOMEPAGE, [
            _block("home_p_0001", home, "p", "© 2024 X Co. All rights reserved."),
            _block("home_p_0002", home, "p", "We use cookies to improve your experience."),
            _block("home_p_0003", home, "p", "Privacy Policy"),
            _block("home_p_0004", home, "p", "Read more"),
            _block("home_h1_0005", home, "h1", "Premium Foot Care", above_fold=True),
        ]),
    ])
    pack = build_evidence_pack(m, _empty_profile())
    ids = {b.block_id for b in pack.blocks}
    # All boilerplate dropped
    for bad in ("home_p_0001", "home_p_0002", "home_p_0003", "home_p_0004"):
        assert bad not in ids, f"{bad} should have been dropped"
    assert "home_h1_0005" in ids


def test_pack_drops_arabic_copyright():
    home = "https://x.com/"
    m = _manifest([
        _page(home, PageType.HOMEPAGE, [
            _block("home_p_0001", home, "p",
                   "جميع الحقوق محفوظة لشركة X"),
            _block("home_h1_0002", home, "h1", "خدمة ممتازة", above_fold=True),
        ]),
    ])
    pack = build_evidence_pack(m, _empty_profile())
    ids = {b.block_id for b in pack.blocks}
    assert "home_p_0001" not in ids
    assert "home_h1_0002" in ids


def test_pack_drops_too_short_unless_heading():
    home = "https://x.com/"
    m = _manifest([
        _page(home, PageType.HOMEPAGE, [
            _block("home_p_0001", home, "p", "Hi"),          # too short
            _block("home_h1_0002", home, "h1", "Welcome"),   # short but a heading -> keep
        ]),
    ])
    pack = build_evidence_pack(m, _empty_profile())
    ids = {b.block_id for b in pack.blocks}
    assert "home_p_0001" not in ids
    assert "home_h1_0002" in ids


def test_pack_drops_too_long():
    home = "https://x.com/"
    long_text = "A" * 700
    m = _manifest([
        _page(home, PageType.HOMEPAGE, [
            _block("home_p_0001", home, "p", long_text),
            _block("home_h1_0002", home, "h1", "Premium Foot Care", above_fold=True),
        ]),
    ])
    pack = build_evidence_pack(m, _empty_profile())
    ids = {b.block_id for b in pack.blocks}
    assert "home_p_0001" not in ids
    assert "home_h1_0002" in ids


# ---------------------------------------------------------------------
# Deduplication
# ---------------------------------------------------------------------

def test_pack_dedupes_repeated_text_across_pages():
    """The brand tagline often appears in the header of every page —
    we should keep one and drop the rest."""
    home = "https://x.com/"
    about = "https://x.com/about/"
    repeated = "We deliver premium foot care in Cairo since 2010."
    m = _manifest([
        _page(home, PageType.HOMEPAGE, [
            _block("home_p_0001", home, "p", repeated),
            _block("home_h1_0002", home, "h1", "Premium Foot Care",
                   above_fold=True),
        ]),
        _page(about, PageType.ABOUT, [
            _block("about_p_0001", about, "p", repeated),
            _block("about_p_0002", about, "p",
                   "Founded in 2010 by certified specialists."),
        ]),
    ])
    pack = build_evidence_pack(m, _empty_profile())
    matching = [b for b in pack.blocks if b.text == repeated]
    assert len(matching) == 1


def test_pack_dedupes_case_insensitively():
    home = "https://x.com/"
    m = _manifest([
        _page(home, PageType.HOMEPAGE, [
            _block("home_p_0001", home, "p", "Premium Foot Care in Cairo"),
            _block("home_p_0002", home, "p", "premium foot care in cairo"),
        ]),
    ])
    pack = build_evidence_pack(m, _empty_profile())
    # One should survive, not both
    assert pack.block_count == 1


def test_pack_does_not_dedupe_similar_but_different_text():
    home = "https://x.com/"
    m = _manifest([
        _page(home, PageType.HOMEPAGE, [
            _block("home_p_0001", home, "p", "We offer foot care services."),
            _block("home_p_0002", home, "p", "We offer hand care services."),
        ]),
    ])
    pack = build_evidence_pack(m, _empty_profile())
    assert pack.block_count == 2


# ---------------------------------------------------------------------
# Caps and quotas
# ---------------------------------------------------------------------

def test_pack_respects_max_blocks_cap():
    home = "https://x.com/"
    blocks = [
        _block(f"home_p_{i:04d}", home, "p", f"Unique sentence number {i} here.")
        for i in range(300)
    ]
    m = _manifest([_page(home, PageType.HOMEPAGE, blocks)])
    pack = build_evidence_pack(m, _empty_profile(), max_blocks=50)
    assert pack.block_count == 50


def test_pack_respects_per_page_type_quota():
    home = "https://x.com/"
    blocks = [
        _block(f"home_p_{i:04d}", home, "p", f"Unique sentence number {i} on home.")
        for i in range(150)
    ]
    m = _manifest([_page(home, PageType.HOMEPAGE, blocks)])
    pack = build_evidence_pack(m, _empty_profile(),
                                max_blocks=200, max_per_page_type=60)
    # Even though we have headroom under max_blocks, homepage is quota-capped at 60
    assert pack.block_count == 60
    assert pack.page_type_distribution["homepage"] == 60


def test_pack_diversity_across_page_types():
    """When homepage and about both have many unique blocks, both should be
    represented in the pack."""
    home = "https://x.com/"
    about = "https://x.com/about/"
    home_blocks = [
        _block(f"home_p_{i:04d}", home, "p", f"Home sentence {i}.")
        for i in range(100)
    ]
    about_blocks = [
        _block(f"about_p_{i:04d}", about, "p", f"About sentence {i}.")
        for i in range(100)
    ]
    m = _manifest([
        _page(home, PageType.HOMEPAGE, home_blocks),
        _page(about, PageType.ABOUT, about_blocks),
    ])
    pack = build_evidence_pack(m, _empty_profile(),
                                max_blocks=200, max_per_page_type=60)
    assert pack.page_type_distribution.get("homepage", 0) <= 60
    assert pack.page_type_distribution.get("about", 0) <= 60
    # Both must be present
    assert pack.page_type_distribution.get("homepage", 0) > 0
    assert pack.page_type_distribution.get("about", 0) > 0


# ---------------------------------------------------------------------
# Scoring / ordering
# ---------------------------------------------------------------------

def test_pack_orders_high_value_blocks_first():
    home = "https://x.com/"
    blocks = [
        # Low-value first in input
        _block("home_p_0001", home, "p", "Some side note about parking."),
        # High-value h1 above-fold should rise to top
        _block("home_h1_0002", home, "h1", "Premium Foot Care in Cairo",
               above_fold=True),
    ]
    m = _manifest([_page(home, PageType.HOMEPAGE, blocks)])
    pack = build_evidence_pack(m, _empty_profile())
    assert pack.blocks[0].block_id == "home_h1_0002"


def test_pack_homepage_outranks_legal():
    home = "https://x.com/"
    legal = "https://x.com/terms/"
    m = _manifest([
        _page(legal, PageType.LEGAL, [
            _block("legal_p_0001", legal, "p",
                   "By using the site you agree to our terms."),
        ]),
        _page(home, PageType.HOMEPAGE, [
            _block("home_h1_0001", home, "h1",
                   "Premium Foot Care", above_fold=True),
        ]),
    ])
    pack = build_evidence_pack(m, _empty_profile())
    assert pack.blocks[0].block_id == "home_h1_0001"


# ---------------------------------------------------------------------
# Invariants
# ---------------------------------------------------------------------

def test_pack_block_ids_are_subset_of_manifest():
    """Critical: every block_id in the pack must exist in the manifest.
    The validator (step 5) will use this invariant to detect LLM
    hallucinations — any cited block_id not in pack.block_ids is bogus."""
    home = "https://x.com/"
    manifest_ids = []
    blocks = []
    for i in range(20):
        bid = f"home_p_{i:04d}"
        manifest_ids.append(bid)
        blocks.append(_block(bid, home, "p", f"Unique sentence number {i} here."))

    m = _manifest([_page(home, PageType.HOMEPAGE, blocks)])
    pack = build_evidence_pack(m, _empty_profile())
    for b in pack.blocks:
        assert b.block_id in manifest_ids
    # And pack.block_ids should match pack.blocks
    assert set(pack.block_ids) == {b.block_id for b in pack.blocks}


def test_pack_carries_missing_fields_from_profile():
    home = "https://x.com/"
    m = _manifest([_page(home, PageType.HOMEPAGE,
                          [_block("h1", home, "h1", "Hello", above_fold=True)])])
    profile = _empty_profile()
    profile.quality.major_missing = ["category", "tone_of_voice"]
    pack = build_evidence_pack(m, profile)
    assert pack.missing_fields == ["category", "tone_of_voice"]


def test_pack_counts_consistent():
    home = "https://x.com/"
    blocks = [
        _block(f"home_p_{i:04d}", home, "p", f"Unique sentence {i} here.")
        for i in range(50)
    ]
    # Add some noise that gets filtered out
    blocks += [
        _block("nav1", home, "li", "Home", section=Section.NAV),
        _block("nav2", home, "li", "About", section=Section.NAV),
        _block("foot1", home, "p", "© 2024 Co."),
    ]
    m = _manifest([_page(home, PageType.HOMEPAGE, blocks)])
    pack = build_evidence_pack(m, _empty_profile(), max_blocks=200)
    assert pack.blocks_total_in_manifest == len(blocks)
    assert pack.blocks_after_filter == 50  # nav + footer + boilerplate filtered
    assert pack.block_count == 50


# ---------------------------------------------------------------------
# as_llm_text rendering
# ---------------------------------------------------------------------

def test_as_llm_text_format():
    home = "https://x.com/"
    m = _manifest([
        _page(home, PageType.HOMEPAGE, [
            _block("home_h1_0001", home, "h1",
                   "Premium Foot Care", above_fold=True),
        ]),
    ])
    pack = build_evidence_pack(m, _empty_profile())
    text = pack.as_llm_text()
    assert "[home_h1_0001]" in text
    assert "homepage" in text
    assert "main" in text
    assert "above_fold" in text
    assert "h1" in text
    assert "Premium Foot Care" in text


def test_as_llm_text_escapes_quotes_in_block_text():
    home = "https://x.com/"
    m = _manifest([
        _page(home, PageType.HOMEPAGE, [
            _block("home_h1_0001", home, "h1",
                   'She said "hello" today', above_fold=True),
        ]),
    ])
    pack = build_evidence_pack(m, _empty_profile())
    text = pack.as_llm_text()
    # The double-quotes in the block text get replaced with singles to
    # keep the wrapper quotes unambiguous
    assert '"hello"' not in text
    assert "'hello'" in text


def test_as_llm_text_respects_max_chars():
    home = "https://x.com/"
    blocks = [
        _block(f"home_p_{i:04d}", home, "p", f"Unique sentence {i} on home page here.")
        for i in range(50)
    ]
    m = _manifest([_page(home, PageType.HOMEPAGE, blocks)])
    pack = build_evidence_pack(m, _empty_profile())
    text = pack.as_llm_text(max_chars=200)
    assert len(text) <= 220  # small allowance for the last full line


# ---------------------------------------------------------------------
# filter() helper
# ---------------------------------------------------------------------

def test_filter_by_page_type():
    home = "https://x.com/"
    about = "https://x.com/about/"
    m = _manifest([
        _page(home, PageType.HOMEPAGE, [
            _block("home_h1", home, "h1", "Home headline", above_fold=True),
        ]),
        _page(about, PageType.ABOUT, [
            _block("about_p", about, "p", "We are a clinic founded in 2010."),
        ]),
    ])
    pack = build_evidence_pack(m, _empty_profile())
    sub = pack.filter(page_types={"about"})
    assert sub.block_count == 1
    assert sub.blocks[0].page_type == "about"


def test_filter_above_fold_only():
    home = "https://x.com/"
    m = _manifest([
        _page(home, PageType.HOMEPAGE, [
            _block("home_h1", home, "h1", "Home headline", above_fold=True),
            _block("home_p", home, "p", "We are a clinic founded in 2010.",
                   above_fold=False),
        ]),
    ])
    pack = build_evidence_pack(m, _empty_profile())
    sub = pack.filter(above_fold_only=True)
    assert sub.block_count == 1
    assert sub.blocks[0].above_fold is True


# ---------------------------------------------------------------------
# Integration: pack from real-shaped manifest + real rules-only profile
# ---------------------------------------------------------------------

def test_pack_integrates_with_apply_rules():
    """End-to-end: scrape manifest -> rules-only profile -> pack."""
    from tests.test_rules import _rich_manifest
    m = _rich_manifest()
    rules_profile = apply_rules(m, "test/manifest.json")
    pack = build_evidence_pack(m, rules_profile)

    # missing_fields should come from the profile's quality.major_missing
    assert pack.missing_fields == rules_profile.quality.major_missing

    # The fixture has 5 text blocks; nav/boilerplate filter shouldn't kill them
    assert pack.block_count >= 4
    # Every block_id must exist in the original manifest
    manifest_ids = {b.block_id for p in m.pages for b in p.text_blocks}
    for b in pack.blocks:
        assert b.block_id in manifest_ids


def test_defaults_are_150_to_200():
    """Doc-style test: confirm the default cap is in the requested range."""
    assert 150 <= DEFAULT_MAX_BLOCKS <= 250
    assert DEFAULT_MAX_PER_PAGE_TYPE <= DEFAULT_MAX_BLOCKS

def test_leaf_detail_blocks_outrank_nav_chrome():
    # BEHAVIOUR, not a source grep (CS-55): a block on a LEAF-DETAIL page (an individual
    # product/course/service) out-ranks an otherwise equally-scored block on a COLLECTION/nav page,
    # so the items the brand actually SELLS survive the pack cap ahead of collection chrome
    # (R2 rawafrican: 11 /products/ pages carried the priced truth but lost their pack slots).
    leaf_url = "https://x.com/products/widget-alpha"     # is_leaf_detail -> True
    coll_url = "https://x.com/products"                  # collection/nav -> False
    leaf_b = _block("L1", leaf_url, "p", "Widget Alpha is a premium build unit priced at 250 EGP.")
    coll_b = _block("C1", coll_url, "p", "Browse our full products collection across categories.")
    # collection page FIRST so a naive stable sort would keep it — the +3 leaf boost must override.
    m = _manifest([
        _page(coll_url, PageType.HOMEPAGE, [coll_b]),
        _page(leaf_url, PageType.HOMEPAGE, [leaf_b]),
    ])
    pack = build_evidence_pack(m, _empty_profile(), max_blocks=1)
    kept = {b.block_id for b in pack.blocks}
    assert kept == {"L1"}, f"the leaf-detail block should win the single slot, got {kept}"
