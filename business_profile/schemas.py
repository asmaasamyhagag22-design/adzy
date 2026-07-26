"""Business profile schema — the contract for the second pipeline stage.

The profile is built from the scrape manifest. Every leaf field uses
the `EvidencedField` wrapper, which forces every claim to carry:
- value (or null if missing)
- evidence: list of (block_id, page_url, quote, extractor)
- confidence: high / medium / low / none
- source_type: extracted / inferred / missing

Rule: a field with value != null but evidence == [] is INVALID. The
validator drops it. This is how we enforce "no evidence = no fact"
at the type-system level.

State machine:
- value=None  -> source_type=missing, confidence=none, evidence=[]
- value set, direct quote     -> source_type=extracted, confidence=high|medium
- value set, no direct quote  -> source_type=inferred,  confidence=low|medium
"""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Generic, Literal, Optional, TypeVar

from pydantic import BaseModel, Field, model_validator

T = TypeVar("T")


# ---------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------

class BusinessModel(str, Enum):
    """HOW a business sells — the axis `BusinessCategory` kept conflating with WHAT it sells.

    MEASURED 2026-07-21: one `category` field had to answer both questions, so it flipped between
    them. `ecommerce` covered an online pharmacy, a jewellery house, a supplements brand and a
    beauty brand — four unrelated markets — while Coca-Cola came out `services_b2c`. Consumers that
    need the model (Places types, local/reach routing, search radius) and consumers that need the
    market (trend seeds, query seeds, peer relevance) were reading the same field and one of them
    was always wrong.

    This axis stays a CLOSED enum because it genuinely is finite. The market axis is free text
    (`market_category`) — no closed list survives contact with real markets (D-8).
    """

    ECOMMERCE = "ecommerce"          # sells online, ships; competes nationally
    LOCAL = "local"                  # walk-in premises; competes by proximity
    SERVICE = "service"              # delivers a service, on-site or remote
    HYBRID = "hybrid"                # a real storefront AND a real online store
    UNKNOWN = "unknown"              # not enough evidence — never guessed


class BusinessCategory(str, Enum):
    RESTAURANT = "restaurant"
    CAFE = "cafe"
    CLINIC = "clinic"
    HOSPITAL = "hospital"
    RETAIL = "retail"
    ECOMMERCE = "ecommerce"
    SERVICES_B2B = "services_b2b"
    SERVICES_B2C = "services_b2c"
    EDUCATION = "education"
    GOVERNMENT = "government"
    AGENCY = "agency"
    HOSPITALITY = "hospitality"
    REAL_ESTATE = "real_estate"
    PROFESSIONAL_SERVICES = "professional_services"  # law, accounting, consulting
    FITNESS = "fitness"
    BEAUTY = "beauty"
    AUTOMOTIVE = "automotive"
    NONPROFIT = "nonprofit"
    OTHER = "other"


class AudienceType(str, Enum):
    B2C = "B2C"
    B2B = "B2B"
    B2G = "B2G"
    MIXED = "mixed"
    UNKNOWN = "unknown"


class ToneOfVoice(str, Enum):
    FORMAL = "formal"
    PROFESSIONAL = "professional"
    FRIENDLY = "friendly"
    CASUAL = "casual"
    PLAYFUL = "playful"
    LUXURY = "luxury"
    CLINICAL = "clinical"
    INSPIRATIONAL = "inspirational"
    UNKNOWN = "unknown"


class PricingPosture(str, Enum):
    BUDGET = "budget"
    MID = "mid"
    PREMIUM = "premium"
    UNKNOWN = "unknown"


class Confidence(str, Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    NONE = "none"


class SourceType(str, Enum):
    EXTRACTED = "extracted"  # direct evidence in scraped text
    INFERRED = "inferred"    # LLM judgment from evidence, not a direct quote
    MISSING = "missing"      # no evidence found, value is null


# ---------------------------------------------------------------------
# Evidence primitives
# ---------------------------------------------------------------------

class EvidenceItem(BaseModel):
    """One unit of evidence backing a claim.

    block_id: stable ID from manifest.pages[].text_blocks. None when
              evidence is structural (schema.org, og tag, computed CSS).
    page_url: which page this evidence is on.
    quote:    short verbatim snippet (<=200 chars). None for structural.
    extractor: tag identifying who produced this evidence:
               'title', 'og:site_name', 'schema_org:Restaurant.name',
               'rule:tel_link', 'rule:cta_anchor', 'llm', etc.
    """
    block_id: Optional[str] = None
    page_url: str
    quote: Optional[str] = None
    extractor: str


class EvidencedField(BaseModel, Generic[T]):
    """A value plus its provenance. Use for every leaf claim."""
    value: Optional[T] = None
    evidence: list[EvidenceItem] = Field(default_factory=list)
    confidence: Confidence = Confidence.NONE
    source_type: SourceType = SourceType.MISSING

    @classmethod
    def missing(cls) -> "EvidencedField[T]":
        return cls(value=None, evidence=[], confidence=Confidence.NONE,
                   source_type=SourceType.MISSING)


# ---------------------------------------------------------------------
# Sub-models
# ---------------------------------------------------------------------

class Offering(BaseModel):
    """One product or service the business sells."""
    name: str
    short_description: Optional[str] = None
    price_text: Optional[str] = None  # raw, e.g. "from EGP 250" — not parsed
    page_url: Optional[str] = None
    evidence: list[EvidenceItem] = Field(default_factory=list)
    confidence: Confidence = Confidence.MEDIUM


class CatalogItem(BaseModel):
    """ONE real item the store sells, verbatim from its own product page.

    Distinct from `Offering`, which may be a department ("Skin Care") on a large store. Every
    field here is copied text or a URL the crawl visited — there is no model in this path, so
    there is nothing to ground-check: `price_text` is what the page shows, currency and all."""
    name: str
    price_text: Optional[str] = None   # verbatim, e.g. "EGP 850" / "ر.س 120" — never parsed
    url: Optional[str] = None          # the product's own page (canonical, query stripped)
    image_url: Optional[str] = None


class Location(BaseModel):
    """A physical location."""
    label: Optional[str] = None  # branch name if any
    address_text: Optional[str] = None
    city: Optional[str] = None
    region: Optional[str] = None
    country: Optional[str] = None
    postal_code: Optional[str] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    evidence: list[EvidenceItem] = Field(default_factory=list)


class OpeningHoursEntry(BaseModel):
    """Hours for one day or day range."""
    days: str  # e.g. "Mon-Fri", "Sat", "Sun"
    opens: Optional[str] = None  # "09:00"
    closes: Optional[str] = None  # "17:00"
    note: Optional[str] = None   # e.g. "by appointment"


class OpeningHours(BaseModel):
    entries: list[OpeningHoursEntry] = Field(default_factory=list)
    evidence: list[EvidenceItem] = Field(default_factory=list)


class PhoneChannel(BaseModel):
    """One phone channel — either a real E.164 number or a short code / hotline.

    2026-05: introduced to support restaurant hotlines (e.g. 19914) that
    fail standard E.164 validation but are still real conversion paths.
    For a normal number, `e164` is populated and `is_short_code=False`.
    For a hotline, `e164=None`, `raw` carries the dialable digits, and
    `is_short_code=True`.
    """
    e164: Optional[str] = None
    raw: str
    is_short_code: bool = False


class ContactChannels(BaseModel):
    """Pure remap from manifest.contact + manifest.links. High confidence."""
    # 2026-05: structured list. The dialable string is always available
    # via .raw; .e164 is populated only for full international numbers.
    phones: list[PhoneChannel] = Field(default_factory=list)
    emails: list[str] = Field(default_factory=list)
    whatsapp_numbers: list[str] = Field(default_factory=list)
    has_contact_form: bool = False
    physical_addresses: list[str] = Field(default_factory=list)
    map_embed_present: bool = False
    evidence: list[EvidenceItem] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def _migrate_phones_e164(cls, data):
        """Backward-compatibility for the pre-2026-05 `phones_e164` field.

        Legacy callers (tests, older API clients) construct ContactChannels
        with `phones_e164=[...]` as a list of E.164 strings. We accept that
        as input and convert it into the new structured `phones` list. If
        both `phones` and `phones_e164` are supplied, `phones` wins.
        """
        if isinstance(data, dict) and "phones_e164" in data:
            legacy = data.pop("phones_e164")
            if "phones" not in data or not data["phones"]:
                if isinstance(legacy, list):
                    data["phones"] = [
                        {"e164": v, "raw": v, "is_short_code": False}
                        for v in legacy if isinstance(v, str) and v
                    ]
        return data

    # Backward-compatible alias (deprecated, slated for removal next release).
    # Returns ONLY the E.164-validated numbers as plain strings, matching
    # the pre-2026-05 contract. Short-code hotlines are intentionally NOT
    # included here — readers that want every dialable number must
    # migrate to `phones`.
    #
    # Pydantic v2 does NOT serialize @property-decorated methods by
    # default. That means `model_dump_json()` produces JSON without the
    # `phones_e164` key, and callers reading the JSON via `.get("phones_e164")`
    # will get None instead of a list. This is intentional: it surfaces the
    # deprecation. Callers must migrate to `phones`. Internal code in this
    # package has been migrated; this property is preserved only for the
    # external API contract during the one-release deprecation window.
    @property
    def phones_e164(self) -> list[str]:
        return [p.e164 for p in self.phones if p.e164]


class SocialAccount(BaseModel):
    platform: str  # facebook, instagram, twitter, ...
    url: str
    evidence: list[EvidenceItem] = Field(default_factory=list)


class LogoCandidateSummary(BaseModel):
    src: str
    alt: Optional[str] = None
    text_wordmark: Optional[str] = None
    source_type: str = "unknown"
    classification: str = "unknown_candidate"
    confidence: float = 0.0
    score: float = 0.0
    reasons: list[str] = Field(default_factory=list)
    width: Optional[int] = None
    height: Optional[int] = None
    aspect_ratio: Optional[float] = None


class VisualIdentitySummary(BaseModel):
    """Passthrough of manifest.visual + a few derived flags.

    v0.2 adds structured visual identity fields while preserving v0.1
    ``palette_hex``, ``palette_dominance``, ``primary_color`` and
    ``logo_url`` for old API/frontend consumers.
    """
    # Legacy fields kept for backward compatibility.
    palette_hex: list[str] = Field(default_factory=list)
    palette_dominance: list[float] = Field(default_factory=list)
    primary_color: Optional[str] = None  # most dominant non-neutral
    body_font: Optional[str] = None
    heading_font: Optional[str] = None
    logo_url: Optional[str] = None

    # Best PHOTOGRAPHIC brand reference image (HERO above-fold > HERO > OG image),
    # surfaced from manifest.images_of_interest. Used to GROUND generation — Veo
    # image-to-video seeds the reel from the brand's real imagery (#4). None when
    # the scrape found no usable hero/og image. NOT a logo (logos live above).
    hero_image_url: Optional[str] = None

    # The brand's REAL on-page photos (food/interior/product/team), surfaced from
    # manifest.images_of_interest CONTENT role with logos excluded. This is what
    # lets the reel come FROM THE PLACE (Ken Burns over real photos) instead of an
    # invented scene. Empty when the scrape found only a logo. Verbatim URLs.
    content_images: list[str] = Field(default_factory=list)

    # LOCAL path of the homepage FULL-PAGE screenshot from the scrape. The page AS
    # DESIGNED is brand identity BY CONSTRUCTION (layout, colour application, type
    # in-situ, imagery treatment) — unlike content photos, which may be stock or a
    # supplier's product shots. Feeds the BrandCreativeDNA vision step as the
    # always-available identity evidence. None on old profiles / missing file.
    homepage_screenshot_path: Optional[str] = None

    # v0.2 structured logo fields.
    primary_logo: Optional[LogoCandidateSummary] = None
    logo_candidates: list[LogoCandidateSummary] = Field(default_factory=list)
    partner_logos: list[LogoCandidateSummary] = Field(default_factory=list)
    authority_logos: list[LogoCandidateSummary] = Field(default_factory=list)
    co_branding_detected: bool = False
    visual_extraction_note: Optional[str] = None

    # v0.2 structured palette fields.
    raw_palette: list[str] = Field(default_factory=list)
    raw_palette_dominance: list[float] = Field(default_factory=list)
    brand_palette: list[str] = Field(default_factory=list)
    brand_palette_confidence: list[float] = Field(default_factory=list)
    primary_brand_color: Optional[str] = None
    accent_colors: list[str] = Field(default_factory=list)
    neutral_colors: list[str] = Field(default_factory=list)
    background_colors: list[str] = Field(default_factory=list)
    text_colors: list[str] = Field(default_factory=list)
    palette_extraction_note: Optional[str] = None
    visual_warnings: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------
# Extraction meta + quality
# ---------------------------------------------------------------------

class ExtractionMeta(BaseModel):
    manifest_path: str
    manifest_hash: Optional[str] = None  # sha256 of the manifest file
    extracted_at: datetime
    duration_ms: int = 0
    rule_extractors_used: list[str] = Field(default_factory=list)
    llm_calls: int = 0
    llm_tokens_input: int = 0
    llm_tokens_output: int = 0
    llm_model: Optional[str] = None
    llm_cost_usd: Optional[float] = None
    extractor_version: str = "0.1.0"

    # 2026-05 PR-validator-diag-persist: structured summary of validator
    # activity, so post-hoc questions like "which fields did the LLM
    # produce that the validator rejected?" can be answered from the
    # saved profile without re-running. We store counts + grouped
    # claim summaries — NOT the full per-rejection RejectionRecord list,
    # which can be large. If you need per-rejection detail, re-run with
    # logging enabled.
    #
    # Shape when populated (None when no LLM extraction ran):
    #   {
    #     "total_rejections": int,
    #     "rejections_by_group": {"identity": int, "offerings": int, ...},
    #     "fields_dropped": [str, ...],
    #     "items_dropped": {"offerings": int, ...},
    #     "candidates_seen": {"offerings": int, ...},
    #     "rejected_claims": {"identity": [str, ...], ...},  # short claim strings
    #   }
    validation_diagnostics: Optional[dict] = None


class ProfileQuality(BaseModel):
    """Readiness signal for downstream stages (diagnosis, scorecard, copy)."""
    has_name: bool
    has_category: bool
    has_offerings: bool
    has_audience: bool
    has_value_propositions: bool
    has_tone: bool
    has_contact: bool
    has_visual: bool
    fields_extracted: int      # count of fields with source_type=extracted
    fields_inferred: int       # count with source_type=inferred
    fields_missing: int        # count with source_type=missing
    major_missing: list[str] = Field(default_factory=list)
    ready_for_strategy: bool   # threshold for next stage
    # --- v0.2-b1: poster-readiness gate ---
    ready_for_poster: bool = False
    poster_blockers: list[str] = Field(default_factory=list)
    poster_warnings: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------
# Stage F-readiness: SWOT / Strategy / Campaign gates
# ---------------------------------------------------------------------

# Allowed values are intentionally narrow so the frontend can rely on them.
ScrapeCompleteness = Literal["thin", "partial", "good", "comprehensive"]


class ScrapeDiagnostics(BaseModel):
    """Optional scraper-side diagnostics used to sharpen readiness gates.

    When the scraper provides these, readiness can detect failures like
    'the scraper discovered /menu and /about but only the homepage
    produced text blocks.' Without these, readiness degrades gracefully
    and uses only what's reachable from the profile itself.
    """
    pages_discovered: int = 0        # URLs found via sitemap/robots/nav
    pages_scraped: int = 0           # URLs the scraper actually fetched
    pages_with_text_blocks: int = 0  # URLs that produced extractable text
    pages_failed: int = 0            # fetch errors, JS timeouts, blocks
    sitemap_used: bool = False
    js_rendering_used: bool = False


class ReadinessReport(BaseModel):
    """Per-stage readiness gate for the downstream campaign pipeline.

    Attached to BusinessProfile under `.readiness` (optional for
    backward compatibility with older profile files).

    Two-state gates only — ready or not ready. No 'tentative' middle
    tier. If a gate is False, the downstream endpoint must either reject
    the request OR accept an explicit `force=True` override (in which
    case the downstream result carries its own `tentative=True` flag,
    not this report).
    """

    # ----- Gates -----
    ready_for_swot: bool
    ready_for_strategy: bool
    ready_for_campaign: bool

    # ----- Blockers per gate (machine-readable codes) -----
    swot_blockers: list[str] = Field(default_factory=list)
    strategy_blockers: list[str] = Field(default_factory=list)
    campaign_blockers: list[str] = Field(default_factory=list)

    # ----- Non-blocking quality warnings -----
    swot_warnings: list[str] = Field(default_factory=list)

    # ----- Scoring (separate from the gate) -----
    swot_richness_score: int = Field(ge=0, le=100, default=0)
    swot_quality_signals: dict[str, bool] = Field(default_factory=dict)

    # ----- Field-level honesty for the UI -----
    missing_critical: list[str] = Field(default_factory=list)
    missing_recommended: list[str] = Field(default_factory=list)
    weak_evidence_fields: list[str] = Field(default_factory=list)

    # ----- Counts (mirrored from ProfileQuality for UI convenience) -----
    fields_extracted: int = 0
    fields_inferred: int = 0
    fields_missing: int = 0

    # ----- Coverage diagnostics -----
    distinct_evidence_pages: int = 0
    pages_discovered: int = 0
    pages_scraped: int = 0
    scrape_completeness: ScrapeCompleteness = "thin"


# ---------------------------------------------------------------------
# Top-level profile
# ---------------------------------------------------------------------

class BusinessProfile(BaseModel):
    """The structured business profile derived from a scrape manifest.

    Every leaf claim uses EvidencedField. Lists of complex objects
    (offerings, locations) carry their evidence inline on each item.
    """
    # --- Schema versioning (v0.2-b1) ---
    # Bump when breaking changes are made. Frontends can use this to
    # warn on stale fixtures or trigger migrations.
    schema_version: str = "0.2.0"

    # --- Identity ---
    name: EvidencedField[str] = Field(default_factory=EvidencedField.missing)
    tagline: EvidencedField[str] = Field(default_factory=EvidencedField.missing)
    description: EvidencedField[str] = Field(default_factory=EvidencedField.missing)
    category: EvidencedField[BusinessCategory] = Field(default_factory=EvidencedField.missing)
    # The two axes `category` was conflating. Additive: `category` stays until every consumer has
    # been moved, so nothing breaks mid-migration.
    #   market_category — WHAT is sold, in the brand's own words ("مجوهرات يدوية", "liposomal
    #                     supplements"). FREE TEXT by ruling: a closed list is always too small.
    #   business_model  — HOW it sells. Closed enum; that axis really is finite.
    market_category: EvidencedField[str] = Field(default_factory=EvidencedField.missing)
    business_model: BusinessModel = BusinessModel.UNKNOWN

    # --- Offerings ---
    offerings: list[Offering] = Field(default_factory=list)
    # The individual items the store SELLS, verbatim from its own leaf pages (name, price as
    # shown, product URL, image). `offerings` summarizes a large store by its DEPARTMENTS —
    # correct, and useless to a creative surface that must show one real product with a real
    # price. Measured on Al Fouad (2026-07-26): 38 product pages crawled, 0 products in the
    # profile. Deterministic (`scraper.catalog.build_catalog`) — no LLM, nothing to hallucinate.
    catalog: list[CatalogItem] = Field(default_factory=list)
    pricing_visible: EvidencedField[bool] = Field(default_factory=EvidencedField.missing)
    pricing_posture: EvidencedField[PricingPosture] = Field(default_factory=EvidencedField.missing)
    # Short code explaining why pricing is unknown when a likely menu/pricing
    # page exists but visible price text was not extracted. Example:
    # "menu_page_found_but_prices_not_text_extracted".
    pricing_extraction_note: Optional[str] = None

    # --- v0.2-b1: honest failure mode for offerings ---
    # Short code (snake_case) explaining why offerings is empty, when it is.
    # None when offerings are populated. Values include:
    #   "no_offering_pages_scraped"
    #   "llm_returned_zero_offerings"
    #   "all_candidates_rejected_by_validator"
    #   "llm_group_failed"
    #   "llm_skipped"
    offerings_extraction_note: Optional[str] = None

    # --- v0.2-b1: surface LLM silent-failure to the consumer ---
    # True when run_llm_extraction returned 0 successful calls despite
    # being asked to run (skip_llm=False). Distinguishes "LLM was skipped"
    # from "LLM was requested but produced nothing."
    llm_silent_failure: bool = False

    # --- Audience & positioning (inferred-heavy) ---
    audience_type: EvidencedField[AudienceType] = Field(default_factory=EvidencedField.missing)
    audience_signals: list[EvidencedField[str]] = Field(default_factory=list)
    value_propositions: list[EvidencedField[str]] = Field(default_factory=list)
    # Catch-all: unique competitive edges / operational details that don't fit the fields
    # above (cited like everything else). Feeds SWOT strengths + poster/reel differentiation.
    other_unique_insights: list[EvidencedField[str]] = Field(default_factory=list)
    tone_of_voice: EvidencedField[ToneOfVoice] = Field(default_factory=EvidencedField.missing)

    # --- Geography & operations ---
    locations: list[Location] = Field(default_factory=list)
    service_areas: list[EvidencedField[str]] = Field(default_factory=list)
    languages: list[str] = Field(default_factory=list)  # iso codes, copied from manifest
    hours: Optional[OpeningHours] = None

    # --- Contact & acquisition ---
    contact_channels: ContactChannels = Field(default_factory=ContactChannels)
    existing_ctas: list[EvidencedField[str]] = Field(default_factory=list)
    social_presence: list[SocialAccount] = Field(default_factory=list)

    # --- Trust signals ---
    trust_signals: list[EvidencedField[str]] = Field(default_factory=list)

    # --- Visual ---
    visual: VisualIdentitySummary = Field(default_factory=VisualIdentitySummary)

    # --- Source URL & language(s) for fast access ---
    source_url: str
    final_url: Optional[str] = None

    # --- Meta ---
    extraction_meta: ExtractionMeta
    quality: ProfileQuality

    # --- Stage F-readiness: optional gate report (None on older profiles) ---
    readiness: Optional[ReadinessReport] = None


# ---------------------------------------------------------------------
# LLM response schemas (used by llm/* modules; concrete, no generics)
# These are what we ASK gpt-4o-mini to return. Enrichment to
# EvidencedField happens in Python after parsing.
# ---------------------------------------------------------------------

class LLMEvidenceRef(BaseModel):
    """LLM-emitted evidence reference. We validate block_id exists
    in the manifest before accepting."""
    block_id: str
    quote: str  # <= 200 chars, must be substring of the block's text


class LLMStringField(BaseModel):
    """Generic LLM response for a single-string field (tagline, etc.)."""
    value: Optional[str] = None
    evidence: list[LLMEvidenceRef] = Field(default_factory=list)
    confidence: Confidence = Confidence.NONE
    reasoning: Optional[str] = None  # short internal note, for debugging


class LLMEnumField(BaseModel):
    """LLM response for an enum field. The caller validates the value
    against the target enum after parsing."""
    value: Optional[str] = None
    evidence: list[LLMEvidenceRef] = Field(default_factory=list)
    confidence: Confidence = Confidence.NONE
    reasoning: Optional[str] = None


class LLMStringListItem(BaseModel):
    value: str
    evidence: list[LLMEvidenceRef] = Field(default_factory=list)
    confidence: Confidence = Confidence.MEDIUM


class LLMStringListField(BaseModel):
    items: list[LLMStringListItem] = Field(default_factory=list)


class LLMOfferingItem(BaseModel):
    name: str
    short_description: Optional[str] = None
    price_text: Optional[str] = None
    evidence: list[LLMEvidenceRef] = Field(default_factory=list)
    confidence: Confidence = Confidence.MEDIUM


class LLMOfferingsField(BaseModel):
    items: list[LLMOfferingItem] = Field(default_factory=list)