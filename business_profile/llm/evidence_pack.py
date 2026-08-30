"""Evidence Pack Builder.

Takes a ScrapeManifest + a rules-only BusinessProfile and produces an
EvidencePack: a compact, scored, deduplicated subset of text blocks
that the LLM extractor (step 4) will read.

Design:
- Field-agnostic. One pack feeds every per-field LLM call.
- Hard caps: max 200 blocks total, max 60 per page type.
- Score = page_type + section + tag + above_fold + length-preference.
- Dedupe by normalized lowercase text (cross-page header noise vanishes).
- Boilerplate killed (copyright, cookies, "Read more", legal links).
- Original text preserved verbatim so validator can match LLM quotes.

The pack is the ONLY source of truth for downstream LLM citations.
Any block_id the LLM emits that is not in `pack.block_ids` is a
hallucination and gets dropped by the validator in step 5.
"""
from __future__ import annotations

import re
from typing import Optional

from pydantic import BaseModel, Field

from scraper.schemas import PageType, ScrapeManifest, Section, TextBlock

from ..schemas import BusinessProfile


# ---------------------------------------------------------------------
# Output schema
# ---------------------------------------------------------------------

class EvidenceBlock(BaseModel):
    """One block in the pack. Same fields the LLM will see."""
    block_id: str
    page_url: str
    page_type: str       # "homepage" / "about" / ...
    section: str         # "main" / "header" / ...
    tag: str             # "h1" / "p" / ...
    above_fold: bool
    text: str            # verbatim from manifest


class EvidencePack(BaseModel):
    """Compact corpus for the LLM extractor."""
    source_url: str
    languages: list[str] = Field(default_factory=list)
    blocks: list[EvidenceBlock] = Field(default_factory=list)

    # Diagnostics (useful for defense and tuning)
    blocks_total_in_manifest: int = 0
    blocks_after_filter: int = 0
    block_count: int = 0
    block_ids: list[str] = Field(default_factory=list)  # for fast validator lookup
    page_type_distribution: dict[str, int] = Field(default_factory=dict)
    section_distribution: dict[str, int] = Field(default_factory=dict)

    # PR-3: was vertical-aware re-ranking active for this pack? Useful
    # for debugging "why did the LLM see /menu before /about for this
    # restaurant." None means the category was unknown / OTHER and the
    # global priority alone was used.
    vertical_rerank_category: Optional[str] = None

    # Hint for the LLM extractor: which fields rules couldn't fill
    missing_fields: list[str] = Field(default_factory=list)

    def as_llm_text(self, max_chars: Optional[int] = None) -> str:
        """Render the pack as plain text for inclusion in LLM prompts.

        Format:
            [block_id] (page_type, section[, above_fold], tag) "text"

        This is the exact format the LLM will be told to cite from.
        """
        lines = []
        used = 0
        for b in self.blocks:
            flags = [b.page_type, b.section]
            if b.above_fold:
                flags.append("above_fold")
            flags.append(b.tag)
            # PROMPT-INJECTION HARDENING (P-72): the block text is UNTRUSTED scraped content. The
            # citation format is `[block_id] (...) "text"`, so a page could embed text mimicking a
            # `[block_id] (...) "..."` line to forge its own evidence anchor and get a fabricated
            # claim cited. Neutralize the three characters that let text escape its quoted slot or
            # counterfeit an anchor: newline -> space (stay on one line), " -> ' (can't close the
            # quote early), and [ ] -> ( ) (only the framework-generated anchor can carry brackets,
            # so a page's own "[block_9]" can never look like a real citation id).
            safe_text = (b.text.replace("\n", " ").replace('"', "'")
                         .replace("[", "(").replace("]", ")"))
            line = f'[{b.block_id}] ({", ".join(flags)}) "{safe_text}"'
            if max_chars and used + len(line) + 1 > max_chars:
                break
            lines.append(line)
            used += len(line) + 1
        return "\n".join(lines)

    def filter(
        self,
        page_types: Optional[set[str]] = None,
        sections: Optional[set[str]] = None,
        above_fold_only: bool = False,
    ) -> "EvidencePack":
        """Return a new pack with a subset of blocks.

        Useful for per-field LLM calls (e.g., when extracting offerings,
        you might restrict to services/products/menu/pricing pages).
        """
        filtered: list[EvidenceBlock] = []
        for b in self.blocks:
            if page_types and b.page_type not in page_types:
                continue
            if sections and b.section not in sections:
                continue
            if above_fold_only and not b.above_fold:
                continue
            filtered.append(b)
        return EvidencePack(
            source_url=self.source_url,
            languages=list(self.languages),
            blocks=filtered,
            blocks_total_in_manifest=self.blocks_total_in_manifest,
            blocks_after_filter=self.blocks_after_filter,
            block_count=len(filtered),
            block_ids=[b.block_id for b in filtered],
            page_type_distribution=_distribution(filtered, "page_type"),
            section_distribution=_distribution(filtered, "section"),
            missing_fields=list(self.missing_fields),
        )


def _distribution(blocks: list[EvidenceBlock], attr: str) -> dict[str, int]:
    out: dict[str, int] = {}
    for b in blocks:
        v = getattr(b, attr)
        out[v] = out.get(v, 0) + 1
    return out


# ---------------------------------------------------------------------
# Scoring weights
# ---------------------------------------------------------------------

# Higher = more likely to contain business-describing content
_PAGE_TYPE_SCORE: dict[str, int] = {
    PageType.HOMEPAGE.value: 5,
    PageType.SERVICES.value: 5,
    PageType.PRODUCTS.value: 5,
    PageType.MENU.value: 5,
    PageType.PRICING.value: 5,
    PageType.BOOKING.value: 5,
    # v0.2-a: high-value offerings pages for universal coverage
    PageType.DELIVERY_ORDER.value: 5,
    PageType.BRANCHES_LOCATIONS.value: 5,
    PageType.COURSES.value: 5,
    PageType.PROGRAMS.value: 5,
    PageType.ADMISSIONS.value: 5,
    PageType.CATERING.value: 5,
    PageType.ABOUT.value: 4,
    PageType.CONTACT.value: 4,
    # v0.2-a: social-proof and narrative pages
    PageType.PORTFOLIO.value: 4,
    PageType.TESTIMONIALS.value: 4,
    PageType.EVENTS.value: 3,
    PageType.OFFERS.value: 3,
    PageType.FAQ.value: 3,
    PageType.BLOG.value: 2,
    # v0.2-a: photo-heavy pages have weak text
    PageType.GALLERY.value: 1,
    PageType.CAREERS.value: 1,
    PageType.OTHER.value: 1,
    PageType.LEGAL.value: 0,
}

_SECTION_SCORE: dict[str, int] = {
    Section.MAIN.value: 3,
    Section.ASIDE.value: 1,
    Section.UNKNOWN.value: 1,
    Section.HEADER.value: 0,
    Section.NAV.value: -2,
    Section.FOOTER.value: -1,
}

_TAG_SCORE: dict[str, int] = {
    "h1": 3, "h2": 2, "h3": 2, "h4": 1, "h5": 0, "h6": 0,
    "p": 1, "li": 1, "blockquote": 2, "summary": 1, "figcaption": 1,
    "button": 1, "a": 0,
    "span": 0, "div": 0, "label": 0,
    "strong": 1, "em": 1, "td": 0, "th": 1, "caption": 1,
}

_ABOVE_FOLD_BONUS = 1
_IDEAL_LEN_MIN, _IDEAL_LEN_MAX = 30, 300


# ---------------------------------------------------------------------
# Filters
# ---------------------------------------------------------------------

_BOILERPLATE_PATTERNS = [
    # Copyright / legal footer text
    re.compile(r"©|\u00a9", re.UNICODE),
    re.compile(r"\bcopyright\b", re.IGNORECASE),
    re.compile(r"all rights reserved", re.IGNORECASE),
    re.compile(r"حقوق(?:\s+النشر)?\s+محفوظة", re.UNICODE),  # AR copyright
    # Privacy / terms
    re.compile(r"\bprivacy (policy|notice)\b", re.IGNORECASE),
    re.compile(r"\bterms (of (service|use)|&\s*conditions)\b", re.IGNORECASE),
    # Cookie CONSENT banners — but NOT the food "cookie" (a bare `\bcookie` deleted real
    # dessert menu items: buffaloburger "Cookie Dough"/"SPOON ME COOKIE", mcdonalds
    # "Chocolate Chip Cookie" — MEASURED 186 blocks over 8 sites). Match only banner phrasing.
    re.compile(r"\b(accept|reject|manage|allow|decline|enable|disable|we\s+use|uses?|using|"
               r"about|control)\s+(all\s+|the\s+|our\s+)?cookies?\b", re.IGNORECASE),
    re.compile(r"\bcookies?\s+(policy|consent|settings|preferences?|notice|banner|choices|"
               r"management|and\s+(personal\s+data|privacy)|to\s+(improve|enhance|personali|"
               r"give|provide|ensure|analy))", re.IGNORECASE),
    re.compile(r"\bcookie\s+use\b", re.IGNORECASE),
    re.compile(r"\bgdpr\b", re.IGNORECASE),
    # Standalone "Read more" / "Learn more" / "Click here"
    re.compile(r"^(read|learn|see|find out|discover) more\.?$", re.IGNORECASE),
    re.compile(r"^click here\.?$", re.IGNORECASE),
    # Accessibility nav
    re.compile(r"^skip to (main )?content$", re.IGNORECASE),
    # Pagination markers
    re.compile(r"^(page \d+|next|previous|prev|first|last)$", re.IGNORECASE),
]

_MIN_TEXT_LEN = 3
_MIN_TEXT_LEN_NON_HEADING = 8
_MAX_TEXT_LEN = 600


def _is_boilerplate(text: str) -> bool:
    for pat in _BOILERPLATE_PATTERNS:
        if pat.search(text):
            return True
    return False


def _normalize_for_dedup(text: str) -> str:
    """Lowercase + collapse whitespace. Used only for dedup keys, never stored."""
    return re.sub(r"\s+", " ", text.lower().strip())


def _score_block(
    block: TextBlock,
    page_type: PageType,
    *,
    vertical_bumps: Optional[dict[str, int]] = None,
) -> int:
    score = 0
    score += _PAGE_TYPE_SCORE.get(page_type.value, 0)
    score += _SECTION_SCORE.get(block.section.value, 0)
    score += _TAG_SCORE.get(block.tag.lower(), 0)
    if block.above_fold:
        score += _ABOVE_FOLD_BONUS
    n = len(block.text)
    if _IDEAL_LEN_MIN <= n <= _IDEAL_LEN_MAX:
        score += 1
    elif n < 10:
        score -= 1
    # PR-3: vertical-aware bump. When the rules profile already knows
    # the category, give a small extra score to page types that matter
    # most for that vertical. Bumps are deliberately +1 / +2 so they
    # only break ties — they don't override the global priority for
    # genuinely high-value pages like SERVICES or MENU.
    if vertical_bumps:
        score += vertical_bumps.get(page_type.value, 0)
    return score


# ---------------------------------------------------------------------
# PR-3: Vertical-aware page preferences (Option B — re-rank after
# category is known, not pre-classify before crawl).
#
# Each table is a small set of page types whose evidence value is
# disproportionately high for that vertical. The bump is +2 for the
# top picks and +1 for secondary picks. Verticals not in the table
# fall back to the global score with no bump.
# ---------------------------------------------------------------------

VERTICAL_PAGE_PREFERENCES: dict[str, dict[str, int]] = {
    # Restaurants/cafes: menu and food-ordering pages carry the offerings.
    # Branches matter for multi-location chains; catering opens up B2B.
    "restaurant": {
        PageType.MENU.value: 2,
        PageType.DELIVERY_ORDER.value: 2,
        PageType.BRANCHES_LOCATIONS.value: 1,
        PageType.CATERING.value: 1,
        PageType.ABOUT.value: 1,
    },
    "cafe": {
        PageType.MENU.value: 2,
        PageType.DELIVERY_ORDER.value: 2,
        PageType.BRANCHES_LOCATIONS.value: 1,
        PageType.ABOUT.value: 1,
    },
    # Clinics/healthcare: services + (the existing crawler tags doctor
    # pages as ABOUT-or-services, so we bump both) + booking for trust.
    "clinic": {
        PageType.SERVICES.value: 2,
        PageType.BOOKING.value: 1,
        PageType.ABOUT.value: 1,
        PageType.BRANCHES_LOCATIONS.value: 1,
    },
    "hospital": {
        PageType.SERVICES.value: 2,
        PageType.BOOKING.value: 1,
        PageType.ABOUT.value: 1,
        PageType.BRANCHES_LOCATIONS.value: 1,
    },
    # Beauty / skincare: PRODUCTS dominate; FAQ often carries ingredient
    # education we want to mine.
    "beauty": {
        PageType.PRODUCTS.value: 2,
        PageType.ABOUT.value: 1,
        PageType.FAQ.value: 1,
        PageType.GALLERY.value: 1,
    },
    # Education / training: courses + programs + admissions, with about
    # for institutional positioning.
    "education": {
        PageType.COURSES.value: 2,
        PageType.PROGRAMS.value: 2,
        PageType.ADMISSIONS.value: 2,
        PageType.ABOUT.value: 1,
        PageType.CONTACT.value: 1,
    },
    # E-commerce: products page is the whole show; faq/shipping (mapped
    # to FAQ) for trust signals.
    "ecommerce": {
        PageType.PRODUCTS.value: 2,
        PageType.ABOUT.value: 1,
        PageType.FAQ.value: 1,
        PageType.CONTACT.value: 1,
    },
    "retail": {
        PageType.PRODUCTS.value: 2,
        PageType.ABOUT.value: 1,
        PageType.BRANCHES_LOCATIONS.value: 1,
    },
    # B2B services: PORTFOLIO + TESTIMONIALS are the SWOT-relevant pages;
    # services itself is the value-prop carrier.
    "services_b2b": {
        PageType.SERVICES.value: 2,
        PageType.PORTFOLIO.value: 2,
        PageType.TESTIMONIALS.value: 1,
        PageType.ABOUT.value: 1,
    },
    "services_b2c": {
        PageType.SERVICES.value: 2,
        PageType.TESTIMONIALS.value: 1,
        PageType.ABOUT.value: 1,
        PageType.BOOKING.value: 1,
    },
    "professional_services": {
        PageType.SERVICES.value: 2,
        PageType.ABOUT.value: 2,
        PageType.PORTFOLIO.value: 1,
        PageType.TESTIMONIALS.value: 1,
    },
}


def _vertical_bumps_for(rules_profile: BusinessProfile) -> Optional[dict[str, int]]:
    """Resolve the bump table for the rules profile's detected category.

    Returns None when:
      - category was never resolved (value is None)
      - category resolved to OTHER (we don't know the vertical)
      - category isn't in the preferences table (e.g. NONPROFIT)
    None signals "no re-rank, behave exactly as pre-PR-3".
    """
    cat_field = getattr(rules_profile, "category", None)
    if cat_field is None:
        return None
    val = getattr(cat_field, "value", None)
    if val is None:
        return None
    # `val` is a BusinessCategory enum; we key on .value
    val_str = val.value if hasattr(val, "value") else str(val)
    if val_str == "other":
        return None
    return VERTICAL_PAGE_PREFERENCES.get(val_str)


# ---------------------------------------------------------------------
# Builder
# ---------------------------------------------------------------------

DEFAULT_MAX_BLOCKS = 200
DEFAULT_MAX_PER_PAGE_TYPE = 60


def build_evidence_pack(
    manifest: ScrapeManifest,
    rules_profile: BusinessProfile,
    max_blocks: int = DEFAULT_MAX_BLOCKS,
    max_per_page_type: int = DEFAULT_MAX_PER_PAGE_TYPE,
) -> EvidencePack:
    """Build an EvidencePack from a manifest + rules-only profile.

    Algorithm:
    1. Collect every TextBlock with its page's page_type.
    2. Drop nav/footer (unless above_fold), boilerplate, too short, too long.
    3. Dedupe by normalized text (case-insensitive, whitespace-collapsed).
    4. Score each remaining block.
    5. Sort by score desc, then take blocks honoring per-page-type quotas.
    6. Stop at max_blocks.
    """
    # 1. Collect
    candidates: list[tuple[TextBlock, PageType]] = []
    total = 0
    for page in manifest.pages:
        for block in page.text_blocks:
            total += 1
            candidates.append((block, page.page_type))

    # 2. Filter
    filtered: list[tuple[TextBlock, PageType]] = []
    seen_keys: set[str] = set()
    for block, page_type in candidates:
        text = block.text or ""
        n = len(text)

        # Length bounds
        if n < _MIN_TEXT_LEN:
            continue
        is_heading = block.tag.lower() in ("h1", "h2", "h3", "h4")
        if n < _MIN_TEXT_LEN_NON_HEADING and not is_heading:
            continue
        if n > _MAX_TEXT_LEN:
            continue

        # Section guard: keep header above_fold (often contains the brand
        # hero); drop pure nav/footer noise.
        sec = block.section.value
        if sec == Section.NAV.value and not block.above_fold:
            continue
        if sec == Section.FOOTER.value and not block.above_fold:
            continue

        # Boilerplate
        if _is_boilerplate(text):
            continue

        # Dedupe by normalized text
        key = _normalize_for_dedup(text)
        if key in seen_keys:
            continue
        seen_keys.add(key)

        filtered.append((block, page_type))

    # 3. Score (PR-3: with optional vertical bump when category is known)
    vertical_bumps = _vertical_bumps_for(rules_profile)
    # R2 (measured on rawafrican: 11 /products/ pages carried 1715 blocks / 310 priced, yet
    # only 29 reached the 180-block pack -> offerings extracted CATEGORY-level, unpriced,
    # and U1 lost the store signal): blocks from LEAF-DETAIL pages (individual products /
    # courses / services — the same universal notion the crawler reserves budget for) get
    # a score boost so the items the brand actually SELLS out-rank nav/collection chrome.
    try:
        from scraper.classify.page_type import is_leaf_detail
        _leaf_urls = {b.page_url for b, _pt in filtered if is_leaf_detail(b.page_url or "")}
    except Exception:  # noqa: BLE001
        _leaf_urls = set()
    scored = [
        (_score_block(b, pt, vertical_bumps=vertical_bumps)
         + (3 if b.page_url in _leaf_urls else 0), b, pt)
        for b, pt in filtered
    ]
    # Stable sort by score desc; preserve insertion order for ties so we get
    # earlier pages first when scores match (homepage > later pages).
    scored.sort(key=lambda t: t[0], reverse=True)

    # 4. Select with per-page-type quotas
    selected: list[tuple[TextBlock, PageType]] = []
    per_type_count: dict[str, int] = {}
    for _score, block, page_type in scored:
        if len(selected) >= max_blocks:
            break
        ptv = page_type.value
        if per_type_count.get(ptv, 0) >= max_per_page_type:
            continue
        per_type_count[ptv] = per_type_count.get(ptv, 0) + 1
        selected.append((block, page_type))

    # 5. Build output blocks
    out_blocks = [
        EvidenceBlock(
            block_id=b.block_id,
            page_url=b.page_url,
            page_type=pt.value,
            section=b.section.value,
            tag=b.tag,
            above_fold=b.above_fold,
            text=b.text,  # verbatim, unmodified
        )
        for b, pt in selected
    ]

    # Resolve the category string for the diagnostic; None when no bump applied.
    rerank_category: Optional[str] = None
    if vertical_bumps is not None:
        cat_field = getattr(rules_profile, "category", None)
        cat_val = getattr(cat_field, "value", None) if cat_field else None
        if cat_val is not None:
            rerank_category = cat_val.value if hasattr(cat_val, "value") else str(cat_val)

    return EvidencePack(
        source_url=manifest.scrape_meta.final_url or manifest.scrape_meta.normalized_url,
        languages=[e.code for e in manifest.languages],
        blocks=out_blocks,
        blocks_total_in_manifest=total,
        blocks_after_filter=len(filtered),
        block_count=len(out_blocks),
        block_ids=[b.block_id for b in out_blocks],
        page_type_distribution=_distribution(out_blocks, "page_type"),
        section_distribution=_distribution(out_blocks, "section"),
        vertical_rerank_category=rerank_category,
        missing_fields=list(rules_profile.quality.major_missing),
    )