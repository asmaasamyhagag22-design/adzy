"""Pydantic schemas for the scrape manifest.

The manifest is the contract between the scraper and every downstream
stage. If a field is not in the manifest, downstream stages MUST
assume it does not exist — they should never re-fetch the website.

Design rules:
- Every evidence-bearing item has a `block_id` reference.
- Per-page failures live on PageRecord; site-level failures on the root.
- Every Optional field is explicitly Optional — no implicit nullability.
"""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Literal, Optional

from pydantic import BaseModel, Field

from .errors import ErrorCode
from .time_utils import utc_now


# ---------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------

class PageType(str, Enum):
    HOMEPAGE = "homepage"
    ABOUT = "about"
    SERVICES = "services"
    PRODUCTS = "products"
    PRICING = "pricing"
    CONTACT = "contact"
    BOOKING = "booking"
    MENU = "menu"  # also used for restaurant food menus
    FAQ = "faq"
    BLOG = "blog"
    LEGAL = "legal"  # privacy, terms
    CAREERS = "careers"
    # v0.2-a additions — universal page discovery:
    DELIVERY_ORDER = "delivery_order"
    BRANCHES_LOCATIONS = "branches_locations"
    COURSES = "courses"
    PROGRAMS = "programs"
    ADMISSIONS = "admissions"
    EVENTS = "events"
    PORTFOLIO = "portfolio"
    TESTIMONIALS = "testimonials"
    GALLERY = "gallery"
    CATERING = "catering"
    OFFERS = "offers"
    OTHER = "other"


class PageTier(str, Enum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"
    SKIP = "SKIP"


class Section(str, Enum):
    HEADER = "header"
    NAV = "nav"
    MAIN = "main"
    FOOTER = "footer"
    ASIDE = "aside"
    UNKNOWN = "unknown"


class LinkCategory(str, Enum):
    INTERNAL = "internal"
    SOCIAL = "social"
    CONTACT_PROTOCOL = "contact_protocol"  # tel:, mailto:, wa.me
    EXTERNAL = "external"
    CTA = "cta"  # action-verb internal link or button-styled


class ImageRole(str, Enum):
    LOGO = "logo"
    HERO = "hero"
    OG_IMAGE = "og_image"
    CONTENT = "content"   # real on-page photo (food/interior/product/team), logo-excluded


# ---------------------------------------------------------------------
# Primitives
# ---------------------------------------------------------------------

class BoundingBox(BaseModel):
    x: float
    y: float
    width: float
    height: float


class FailureRecord(BaseModel):
    code: ErrorCode
    message: str
    page_url: Optional[str] = None
    occurred_at: datetime = Field(default_factory=utc_now)


# ---------------------------------------------------------------------
# Text blocks (the foundation for evidence)
# ---------------------------------------------------------------------

class TextBlock(BaseModel):
    """A single text-bearing DOM node, captured with enough context
    for downstream stages to use it as evidence without re-fetching.
    """
    block_id: str  # unique within scrape, e.g. "homepage_h1_001"
    page_url: str
    tag: str  # 'h1', 'p', 'button', 'a', etc.
    text: str
    section: Section
    selector: str  # CSS selector to locate this block in the DOM
    bbox: Optional[BoundingBox] = None
    above_fold: bool = False
    is_link: bool = False
    href: Optional[str] = None


# ---------------------------------------------------------------------
# Links
# ---------------------------------------------------------------------

class LinkRecord(BaseModel):
    href: str
    anchor_text: str
    page_url: str  # which page this link was found on
    block_id: Optional[str] = None  # evidence pointer
    category: LinkCategory
    # For social links, the platform name (facebook, instagram, ...)
    platform: Optional[str] = None
    # CS-56: a SYNTHESISED URL (inferred from a next_data slug template), NOT a real discovered
    # anchor. The contract is "recorded but NOT fetched" — it stays in the link INVENTORY but the
    # crawl frontier must SKIP it (the URL may not exist; the template can be brand-specific).
    inferred: bool = False


# ---------------------------------------------------------------------
# Forms
# ---------------------------------------------------------------------

class FormField(BaseModel):
    name: Optional[str] = None
    field_type: str  # text, email, tel, submit, ...
    placeholder: Optional[str] = None
    required: bool = False


class FormRecord(BaseModel):
    page_url: str
    action: Optional[str] = None
    method: str = "get"
    fields: list[FormField] = Field(default_factory=list)
    surrounding_text: Optional[str] = None


# ---------------------------------------------------------------------
# Site-wide aggregations
# ---------------------------------------------------------------------

class PhoneRecord(BaseModel):
    e164: Optional[str] = None  # +20... normalized; None for short codes / hotlines
    raw: str
    evidence_block_id: Optional[str] = None
    page_url: str
    # 2026-05: hotline / short-code support (e.g. tel:19914 for restaurants).
    # When True, e164 is None and `raw` carries the original value as
    # the customer would dial it. Set by the extractor when the value
    # is numeric but fails E.164 validation (too short for a real phone).
    is_short_code: bool = False


class EmailRecord(BaseModel):
    address: str
    evidence_block_id: Optional[str] = None
    page_url: str


class WhatsAppRecord(BaseModel):
    number: Optional[str] = None  # extracted from wa.me/<num>
    raw_url: str
    page_url: str


class AddressRecord(BaseModel):
    text: str
    evidence_block_id: Optional[str] = None
    page_url: str


class MapEmbed(BaseModel):
    provider: str  # "google_maps", "openstreetmap", "other"
    src: str
    page_url: str


class ContactInfo(BaseModel):
    phones: list[PhoneRecord] = Field(default_factory=list)
    emails: list[EmailRecord] = Field(default_factory=list)
    whatsapp: list[WhatsAppRecord] = Field(default_factory=list)
    addresses_raw: list[AddressRecord] = Field(default_factory=list)
    map_embeds: list[MapEmbed] = Field(default_factory=list)


class ColorEntry(BaseModel):
    hex: str
    dominance: float  # 0.0 - 1.0


class LogoRecord(BaseModel):
    block_id: Optional[str] = None
    src: str
    alt: Optional[str] = None
    page_url: str
    local_path: Optional[str] = None  # if we downloaded it


class LogoCandidate(BaseModel):
    """A possible logo discovered from visual evidence.

    v0.2 keeps the old single ``logo`` field for compatibility, but the
    scraper now preserves the full candidate set so downstream stages can
    avoid silently picking a government/partner/sponsor logo as the brand.
    """
    block_id: Optional[str] = None
    src: str
    alt: Optional[str] = None
    text_wordmark: Optional[str] = None
    page_url: str
    local_path: Optional[str] = None
    source_type: str = "unknown"  # img, svg_file, inline_svg, css_background, favicon, og_image...
    classification: str = "unknown_candidate"
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    score: float = 0.0
    reasons: list[str] = Field(default_factory=list)
    width: Optional[int] = None
    height: Optional[int] = None
    aspect_ratio: Optional[float] = None
    in_header: bool = False
    near_nav: bool = False
    links_to_home: bool = False
    repeated_count: int = 1
    is_fallback: bool = False

class VisualIdentity(BaseModel):
    # Legacy v0.1 fields kept stable for old downstream consumers.
    color_palette: list[ColorEntry] = Field(default_factory=list)
    body_bg: Optional[str] = None
    header_bg: Optional[str] = None
    primary_button_color: Optional[str] = None
    fonts: dict[str, Optional[str]] = Field(
        default_factory=lambda: {"body": None, "headings": None, "buttons": None}
    )
    logo: Optional[LogoRecord] = None

    # v0.2 structured visual identity fields.
    primary_logo: Optional[LogoCandidate] = None
    logo_candidates: list[LogoCandidate] = Field(default_factory=list)
    partner_logos: list[LogoCandidate] = Field(default_factory=list)
    authority_logos: list[LogoCandidate] = Field(default_factory=list)
    co_branding_detected: bool = False
    visual_extraction_note: Optional[str] = None

    raw_palette: list[ColorEntry] = Field(default_factory=list)
    brand_palette: list[ColorEntry] = Field(default_factory=list)
    primary_brand_color: Optional[str] = None
    accent_colors: list[str] = Field(default_factory=list)
    neutral_colors: list[str] = Field(default_factory=list)
    background_colors: list[str] = Field(default_factory=list)
    text_colors: list[str] = Field(default_factory=list)
    palette_extraction_note: Optional[str] = None
    visual_warnings: list[str] = Field(default_factory=list)


class SchemaOrgBlock(BaseModel):
    type: Optional[str] = None  # @type
    data: dict  # raw JSON-LD object (or normalized Microdata/RDFa)
    # PR-2: provenance. Older manifests load with the JSON-LD default so
    # the field is backward-compatible. Possible values:
    #   "json_ld"   — extracted from <script type="application/ld+json">
    #   "microdata" — extracted via extruct from itemtype/itemprop attrs
    #   "rdfa"      — extracted via extruct from typeof/property attrs
    source: Literal["json_ld", "microdata", "rdfa", "next_data"] = "json_ld"


class LanguageEvidence(BaseModel):
    """Provenance for the language a crawl measured.

    `render_locale_source` is the load-bearing field. `evidence` means the SERVER's own
    preference-free representation named its language and the browser was told to render that.
    `fallback` means nothing named a language and the browser had to be given a deterministic
    locale anyway — a technical necessity that is NOT a finding about the brand.
    """

    bootstrap_url: Optional[str] = None
    bootstrap_final_url: Optional[str] = None
    # The language the server's own default representation stated. "" when it stated none.
    representation_language: str = ""
    render_locale: Optional[str] = None
    render_locale_source: str = "unknown"        # evidence | fallback | unknown
    is_language_selector: bool = False
    negotiates_language: bool = False
    # hreflang tag -> absolute URL. A MAPPING: order is not preference.
    hreflang: dict[str, str] = Field(default_factory=dict)
    # `x-default` is a routing relation to a URL, never a language.
    x_default_url: Optional[str] = None
    browser_final_url: Optional[str] = None
    # True when the browser ended up somewhere other than the representation the server chose.
    representation_changed: bool = False

    @property
    def measurement_is_trusted(self) -> bool:
        """May the measured language be read as evidence about the brand?

        No when the render locale was a FALLBACK and the browser then moved into a different
        representation: that move may have been caused by our own locale, so the language we
        measured afterwards could be one we selected. Everything else is trustworthy.
        """
        if self.render_locale_source == "evidence":
            return True
        if self.render_locale_source == "fallback":
            return not self.representation_changed
        return False


class SiteMetadata(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    keywords: Optional[str] = None
    canonical: Optional[str] = None
    favicon: Optional[str] = None
    og_title: Optional[str] = None
    og_description: Optional[str] = None
    og_image: Optional[str] = None
    og_site_name: Optional[str] = None
    twitter_card: Optional[str] = None
    html_lang: Optional[str] = None
    schema_org: list[SchemaOrgBlock] = Field(default_factory=list)


class ImageOfInterest(BaseModel):
    role: ImageRole
    src: str
    alt: Optional[str] = None
    page_url: str
    width: Optional[int] = None
    height: Optional[int] = None
    above_fold: bool = False


# ---------------------------------------------------------------------
# Robots / readiness / pages / meta
# ---------------------------------------------------------------------

class RobotsRecord(BaseModel):
    checked: bool
    allowed: bool
    robots_url: Optional[str] = None
    crawl_delay_seconds: float = 0.0
    note: Optional[str] = None
    # Sitemap: directives discovered in robots.txt (PR-1).
    # Empty list when robots.txt was unreachable or carried no Sitemap lines.
    sitemap_urls: list[str] = Field(default_factory=list)


class ReadinessReport(BaseModel):
    has_homepage: bool
    has_internal_pages: bool
    has_contact_signals: bool
    has_visual_signals: bool
    has_cta_candidates: bool
    has_metadata: bool
    major_missing: list[str] = Field(default_factory=list)
    ready_for_extraction: bool


class LanguageEntry(BaseModel):
    code: str
    proportion: float


class PageRecord(BaseModel):
    url: str  # the URL we asked for
    final_url: str  # after redirects
    http_status: Optional[int] = None
    fetched_at: datetime
    fetch_duration_ms: int

    page_type: PageType
    tier: PageTier

    content_hash: Optional[str] = None  # sha256 of rendered HTML
    screenshot_hash: Optional[str] = None  # sha256 of full-page PNG

    html_path: Optional[str] = None
    clean_text_path: Optional[str] = None
    screenshot_full_path: Optional[str] = None
    screenshot_viewport_path: Optional[str] = None

    viewport_width: int
    viewport_height: int

    text_blocks: list[TextBlock] = Field(default_factory=list)
    forms: list[FormRecord] = Field(default_factory=list)
    failures: list[FailureRecord] = Field(default_factory=list)

    # PR-3: trafilatura-derived signals.
    # `cleaned_main_text` is the article body with nav/footer/cookie-banner
    # noise stripped — useful for LLM evidence-pack summaries. Stored in
    # the manifest so the build is reproducible without re-importing
    # trafilatura at extraction time.
    # `has_meaningful_content` is True when the cleaned text passes a
    # density threshold (length + word count). False means the page
    # returned 200 but only carried scaffolding — useful for the readiness
    # `pages_with_text_blocks` count to be honest.
    cleaned_main_text: Optional[str] = None
    has_meaningful_content: bool = False

    # 2026-05 PR-next-data: per-page Next.js __NEXT_DATA__ diagnostics.
    # `next_data_found` is True when the script tag was present and the
    # payload was non-empty. `next_data_payload_size_bytes` is the raw
    # JSON byte count. `next_data_likely_dominant` is True when the
    # JSON payload dwarfs the DOM text (>10x), an honest signal that
    # "the DOM isn't where the real content is."
    next_data_found: bool = False
    next_data_payload_size_bytes: int = 0
    next_data_likely_dominant: bool = False


class ScrapeMeta(BaseModel):
    input_url: str
    normalized_url: str
    final_url: Optional[str] = None
    scraped_at: datetime
    duration_ms: int = 0
    bytes_downloaded: int = 0
    pages_attempted: int = 0
    pages_succeeded: int = 0
    scraper_version: str = "0.1.0"
    budget_exceeded: bool = False


class LinkInventory(BaseModel):
    internal: list[LinkRecord] = Field(default_factory=list)
    social: list[LinkRecord] = Field(default_factory=list)
    contact_protocol: list[LinkRecord] = Field(default_factory=list)
    external: list[LinkRecord] = Field(default_factory=list)
    cta_candidates: list[LinkRecord] = Field(default_factory=list)
    # 2026-05 PR-next-data: URLs synthesized from structured data sources
    # (e.g. Next.js __NEXT_DATA__ slugs). These are recorded for downstream
    # visibility but NOT auto-fetched by the crawler in this PR. A future
    # PR may promote them to the regular `internal` list with a low tier
    # after measuring the false-positive rate.
    inferred_internal: list[LinkRecord] = Field(default_factory=list)


# ---------------------------------------------------------------------
# Scrape diagnostics (PR-1)
#
# Counts that explain "how comprehensive was the crawl". The
# business_profile.ReadinessReport reads these to decide whether
# `single_page_evidence` is a hard block or a soft warning. Separate
# concept from `ReadinessReport` above (which is about manifest
# extractability) and from `ValidationDiagnostics` in the LLM layer
# (which is about LLM-side hallucination defense).
# ---------------------------------------------------------------------

class ScrapeDiagnostics(BaseModel):
    pages_discovered: int = 0        # URLs found via sitemap + homepage links (post-classify, pre-budget)
    pages_scraped: int = 0           # URLs the crawler actually fetched
    pages_with_text_blocks: int = 0  # URLs that produced at least one text block
    pages_failed: int = 0            # fetch errors / robots disallow / timeout
    sitemap_used: bool = False       # at least one URL came from sitemap.xml
    sitemap_urls_found: int = 0      # total URLs the sitemap contributed (pre-cap, pre-classify)
    js_rendering_used: bool = True   # always True today; future-proofs for static-fetch fallback


# ---------------------------------------------------------------------
# Top-level manifest
# ---------------------------------------------------------------------

class DeepSearchSource(BaseModel):
    """One web-search result used as secondary evidence."""
    title: str
    url: str
    snippet: str = ""
    rank: int = 0


class DeepSearchResult(BaseModel):
    """Tier-3 fallback evidence gathered from web search when the direct scrape
    was blocked/empty. SECONDARY by construction — every field is what the public
    web (Google's index) reports, NOT first-party site content. Never fabricated:
    empty search -> used=True with empty fields; used=False if no provider."""
    used: bool = False
    provider: Optional[str] = None
    query: Optional[str] = None
    business_name: Optional[str] = None     # from the site's own listing title
    description: Optional[str] = None        # from the site's indexed meta-description
    social_links: list[str] = Field(default_factory=list)
    sources: list[DeepSearchSource] = Field(default_factory=list)
    note: Optional[str] = None


class ScrapeManifest(BaseModel):
    scrape_meta: ScrapeMeta
    robots: RobotsRecord
    readiness: ReadinessReport
    languages: list[LanguageEntry] = Field(default_factory=list)
    # What the SITE declares about itself via <link rel="alternate" hreflang>. Distinct
    # from `languages`, which is what the crawl happened to READ: alfouadpharmacies.com
    # declares ar+en on all 41 pages, the crawler opened only /en/, and the analysis then
    # told the client their site was single-language.
    declared_languages: list[str] = Field(default_factory=list)
    # HOW the crawl came to observe a language — never WHICH language it is. Free-text notes
    # cannot be reasoned about downstream, and the whole F-06 defect is a measurement being read
    # as evidence about the brand. Optional with a default so older manifests still load.
    language_evidence: Optional[LanguageEvidence] = None

    # Reviews printed on the brand's OWN site, with the name recovered from the portrait's
    # alt text. Text extraction drops alt, so an attributed review reached the LLM stage
    # looking anonymous and its "attribution required" rule correctly refused it — nine real
    # customer reviews sat unread in a page we had already downloaded.
    testimonials: list[dict] = Field(default_factory=list)
    pages: list[PageRecord] = Field(default_factory=list)
    site_metadata: SiteMetadata = Field(default_factory=SiteMetadata)
    visual: VisualIdentity = Field(default_factory=VisualIdentity)
    contact: ContactInfo = Field(default_factory=ContactInfo)
    links: LinkInventory = Field(default_factory=LinkInventory)
    images_of_interest: list[ImageOfInterest] = Field(default_factory=list)
    failures: list[FailureRecord] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    # PR-1: optional diagnostics counts. Older manifests load with this as None.
    scrape_diagnostics: Optional[ScrapeDiagnostics] = None
    # Tier-3 Deep Search fallback (secondary evidence). None on a normal scrape;
    # populated only when the direct scrape was blocked/empty. A deep_search recovery IS an
    # independent readiness path (readiness.compute_readiness) — business_profile then builds a
    # SECONDARY, SERP-grounded profile from it (rules/from_deep_search), cited to the source URLs.
    deep_search: Optional[DeepSearchResult] = None