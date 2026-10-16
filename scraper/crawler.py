"""Crawler orchestrator.

Drives the full scrape:
1. Normalize URL and load robots.txt.
2. Fetch homepage. If blocked or empty -> record failure and stop.
3. Extract everything from homepage (text blocks, metadata, visual,
   links, forms, contact, images).
4. From discovered internal links, classify and tier them.
5. Fetch top-N high/medium-tier pages within budget, extracting from
   each.
6. Aggregate site-wide structures (links, contact, languages).
7. Compute readiness.
8. Return ScrapeManifest.

Writes intermediate artifacts (HTML, screenshots, clean text) to disk
under <output_root>/<domain>_<timestamp>/.
"""
from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Optional

from playwright.sync_api import sync_playwright

from .config import (
    BFS_MAX_NEW_CANDIDATES,
    ECOMMERCE_BUDGET_SECONDS,
    ECOMMERCE_MAX_INTERNAL_PAGES,
    ECOMMERCE_PRODUCT_URL_MIN,
    HOMEPAGE_RATE_LIMIT_RETRIES,
    INTER_PAGE_DELAY_SECONDS,
    MAX_CRAWL_DEPTH,
    MAX_INTERNAL_PAGES,
    MIN_SUBPAGE_ATTEMPTS,
    TOTAL_BUDGET_SECONDS,
    TWO_PHASE_IMAGE_FETCH,
    VIEWPORT_HEIGHT,
    VIEWPORT_WIDTH,
    scrapes_root,
)
from .classify.page_type import classify_homepage, classify_url, is_leaf_detail, is_product_detail  # noqa: F401
from .errors import ErrorCode
from .extractors.contact import extract_contact, fill_addresses_from_schema_org
from .extractors.forms import extract_forms
from .extractors.images import extract_images_of_interest
from .extractors.links import build_inventory, extract_links_from_html
from .extractors.metadata import extract_metadata, merge_metadata
from .extractors.text_blocks import extract_text_blocks
from .extractors.visual import build_visual_identity, extract_from_page
from .fetcher import fetch_page, make_browser_context


from .browser_launch import launch_chromium as _launch_chromium
from .browser_launch import looks_like_http2_failure as _looks_like_http2_failure
from .language import detect_languages
from .readiness import compute_readiness
from .representation import probe_neutral_representation, render_locale_for
from .robots import can_fetch, load_robots
from .time_utils import utc_now
from .schemas import (
    FailureRecord,
    LinkCategory,
    PageRecord,
    PageTier,
    PageType,
    ScrapeManifest,
    LanguageEvidence,
    ScrapeMeta,
    SiteMetadata,
    Section,
    TextBlock,
)
from .url_utils import (
    dedup_key,
    get_domain_slug,
    normalize_url,
    resolve,
    same_registrable_host,
    site_root_if_deep,
    validate_input_url,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------
# Output dirs
# ---------------------------------------------------------------------

def _make_output_dir(input_url: str, output_root: str) -> Path:
    slug = get_domain_slug(input_url)
    ts = utc_now().strftime("%Y%m%d_%H%M%S")
    out = Path(output_root) / f"{slug}_{ts}"
    (out / "raw").mkdir(parents=True, exist_ok=True)
    (out / "clean").mkdir(parents=True, exist_ok=True)
    return out


def _page_slug(url: str, page_type: PageType, index: int) -> str:
    """A short, filesystem-safe slug for per-page artifact filenames."""
    return f"{index:02d}_{page_type.value}"


def _write_page_artifacts(
    out_dir: Path,
    slug: str,
    html: str,
    clean_text: str,
    full_png: bytes,
    viewport_png: bytes,
) -> dict[str, str]:
    paths = {}
    if html:
        p = out_dir / "raw" / f"{slug}.html"
        p.write_text(html, encoding="utf-8", errors="replace")
        paths["html_path"] = str(p)
    if clean_text:
        p = out_dir / "clean" / f"{slug}.txt"
        p.write_text(clean_text, encoding="utf-8", errors="replace")
        paths["clean_text_path"] = str(p)
    if full_png:
        p = out_dir / "raw" / f"{slug}_full.png"
        p.write_bytes(full_png)
        paths["screenshot_full_path"] = str(p)
    if viewport_png:
        p = out_dir / "raw" / f"{slug}_viewport.png"
        p.write_bytes(viewport_png)
        paths["screenshot_viewport_path"] = str(p)
    return paths


# ---------------------------------------------------------------------
# Sub-page selection
# ---------------------------------------------------------------------

def _select_subpages_to_fetch(
    homepage_links,
    site_url: str,
    homepage_url: str,
    cap: int = MAX_INTERNAL_PAGES,
    *,
    extra_urls: Optional[list[str]] = None,
) -> list[tuple[str, PageType, PageTier, str]]:
    """From homepage links (and optional sitemap-discovered URLs), pick
    the top-N internal pages to fetch.

    Returns a list of (url, page_type, tier, anchor_text), deduped by
    normalized URL, ordered by tier (HIGH > MEDIUM > LOW). Skips
    homepage itself and SKIP-tier pages.

    `extra_urls` is an optional flat list of URLs (typically from
    sitemap.xml). Each is classified by URL only (no anchor text),
    runs through the same tier/priority pipeline, and is deduped
    against homepage-discovered links by normalized URL. Homepage
    links are processed first so that any URL found in both sources
    keeps its homepage anchor text (which carries more signal than
    a sitemap entry).
    """
    # Dedup by the scheme/slash-insensitive frontier key (H3) so http/https and
    # trailing-slash variants of one page don't each consume a budget slot; the stored
    # tuple keeps the faithful normalize_url form as the FETCH target.
    homepage_key = dedup_key(homepage_url)
    by_norm: dict[str, tuple[str, PageType, PageTier, str]] = {}

    tier_rank = {PageTier.HIGH: 0, PageTier.MEDIUM: 1, PageTier.LOW: 2, PageTier.SKIP: 99}

    for link in homepage_links:
        if getattr(link, "inferred", False):
            continue                        # CS-56: synthesised next_data URL — inventory only, never fetched
        if link.category not in (LinkCategory.INTERNAL, LinkCategory.CTA):
            continue
        if not same_registrable_host(link.href, site_url):
            continue
        norm = normalize_url(link.href)
        key = dedup_key(link.href)
        if key == homepage_key:
            continue
        pt, tier = classify_url(link.href, link.anchor_text)
        if tier == PageTier.SKIP:
            continue
        # Prefer the higher-tier classification if we've seen this URL before
        existing = by_norm.get(key)
        if existing is None or tier_rank[tier] < tier_rank[existing[2]]:
            by_norm[key] = (norm, pt, tier, link.anchor_text)

    # PR-1: merge in extra URLs (typically from sitemap.xml). Classify by
    # URL only (no anchor text). Homepage anchor text wins on collisions
    # because it carries more signal — sitemap entries have no anchor.
    for raw_url in (extra_urls or []):
        if not same_registrable_host(raw_url, site_url):
            continue
        norm = normalize_url(raw_url)
        key = dedup_key(raw_url)
        if key == homepage_key:
            continue
        if key in by_norm:
            # Already discovered via homepage links; keep that entry's anchor text.
            continue
        pt, tier = classify_url(raw_url, anchor_text="")
        if tier == PageTier.SKIP:
            continue
        by_norm[key] = (norm, pt, tier, "")

    # Sort by tier rank, then by page-type priority within tier.
    # Lower number = higher priority. Ordering reflects what's most useful
    # for the LLM evidence pack: contact + offerings pages first, then
    # location/admissions, then narrative pages, then peripheral content.
    type_priority = {
        PageType.HOMEPAGE: -1,
        # HIGH tier — offerings and contact
        PageType.CONTACT: 0,
        PageType.SERVICES: 1,
        PageType.PRODUCTS: 2,
        PageType.MENU: 3,
        PageType.DELIVERY_ORDER: 4,
        PageType.PRICING: 5,
        PageType.BOOKING: 6,
        PageType.BRANCHES_LOCATIONS: 7,
        PageType.COURSES: 8,
        PageType.PROGRAMS: 9,
        PageType.ADMISSIONS: 10,
        PageType.CATERING: 11,
        # MEDIUM tier — narrative and social proof
        PageType.ABOUT: 20,
        PageType.PORTFOLIO: 21,
        PageType.TESTIMONIALS: 22,
        PageType.EVENTS: 23,
        PageType.OFFERS: 24,
        PageType.FAQ: 25,
        # LOW tier — peripheral
        PageType.GALLERY: 30,
        PageType.BLOG: 31,
        PageType.CAREERS: 32,
        # Catch-all + skip
        PageType.OTHER: 50,
        PageType.LEGAL: 99,
    }
    candidates = list(by_norm.values())
    candidates.sort(key=lambda c: (tier_rank[c[2]], type_priority.get(c[1], 50)))
    return _reserve_product_quota(candidates, cap)


def _bfs_new_candidates(
    sub_links,
    site_url: str,
    frontier_norms: set,
    *,
    max_new: int,
) -> list[tuple[str, PageType, PageTier, str]]:
    """FIX-A: classify the links found on ONE fetched page and return the NEW frontier
    candidates they reveal — this is what makes the crawl multi-depth instead of
    structurally depth-1 (MEASURED: nti's course layer was discovered on category pages
    and never fetched, while 370s/13 slots of budget went unused).

    Pure + hermetically testable: no fetching here. Internal/CTA same-host links only,
    SKIP tier excluded, LOW tier excluded (peripheral galleries/blogs must not eat leaf
    budget), deduped against everything already planned. Ordered: leaf-detail first
    (the individual course/product/service pages the owner needs), then HIGH, then
    MEDIUM. The caller appends these at the frontier TAIL, so the original plan keeps
    its priority and the page cap + time budget still bound the crawl."""
    tier_rank = {PageTier.HIGH: 0, PageTier.MEDIUM: 1}
    out: list[tuple[str, PageType, PageTier, str]] = []
    for link in (sub_links or []):
        if len(out) >= max_new:
            break
        if getattr(link, "inferred", False):
            continue                        # CS-56: synthesised next_data URL — inventory only, never fetched
        if getattr(link, "category", None) not in (LinkCategory.INTERNAL, LinkCategory.CTA):
            continue
        href = getattr(link, "href", "") or ""
        if not href or not same_registrable_host(href, site_url):
            continue
        try:
            norm = normalize_url(href)
            key = dedup_key(href)          # CS-94: dedup on the SAME scheme/slash-insensitive key
        except Exception:                  # `_select_subpages_to_fetch` uses (was normalize_url here,
            continue                       # so http/https + trailing-slash twins slipped back in)
        if key in frontier_norms:
            continue
        try:
            pt, tier = classify_url(href, getattr(link, "anchor_text", "") or "")
        except Exception:
            continue
        if tier not in tier_rank:
            continue
        frontier_norms.add(key)
        out.append((norm, pt, tier, getattr(link, "anchor_text", "") or ""))
    out.sort(key=lambda c: (0 if is_leaf_detail(c[0]) else 1, tier_rank[c[2]]))
    return out


def _diversify_by_parent(products: list, n: int) -> list:
    """Round-robin across the products' PARENT paths so we pick a SPREAD of the catalogue (a few from
    each collection), not 30 variants of one family."""
    groups: dict = {}
    order: list = []
    for c in products:
        parent = "/".join(c[0].split("?")[0].rstrip("/").split("/")[:-1])   # drop the product slug
        if parent not in groups:
            groups[parent] = []
            order.append(parent)
        groups[parent].append(c)
    out: list = []
    while len(out) < n:
        took = False
        for parent in order:
            g = groups[parent]
            if g and len(out) < n:
                out.append(g.pop(0))
                took = True
        if not took:
            break
    return out


def _reserve_product_quota(candidates: list, cap: int) -> list:
    """Give a large, DIVERSIFIED share of the frontier to INDIVIDUAL product pages so a store's
    catalogue is captured item-by-item, not just at the department level. Without this, all product
    URLs tie on (tier, type) and the stable sort lets collection/nav pages (discovered first) fill
    every slot while /products/<slug> pages sit in the discarded tail (MEASURED: azza fetched 26
    collections, 0 individual products). No product-detail URLs -> unchanged (non-store safe)."""
    if cap <= 0:
        return []
    # FIX-A: leaves are UNIVERSAL now — individual courses/services/menu items get the same
    # diversified budget share that Shopify-style /products/<slug> pages always got.
    products = [c for c in candidates if is_leaf_detail(c[0])]
    if not products:
        return candidates[:cap]
    others = [c for c in candidates if not is_leaf_detail(c[0])]
    # keep up to ~35% for the top-ranked landing/collection/contact pages (offerings tree + key pages);
    # give the REST of the budget to a diverse spread of individual products.
    chosen_others = others[: round(cap * 0.35)]
    prod_quota = min(len(products), cap - len(chosen_others))
    chosen_products = _diversify_by_parent(products, prod_quota)
    # Interleave ~1 landing/collection page per 2 products so BOTH the offerings tree and a spread of
    # individual products are reached before the time budget binds.
    out: list = []
    oi = pi = 0
    while len(out) < cap and (oi < len(chosen_others) or pi < len(chosen_products)):
        if oi < len(chosen_others):
            out.append(chosen_others[oi]); oi += 1
        for _ in range(2):
            if pi < len(chosen_products) and len(out) < cap:
                out.append(chosen_products[pi]); pi += 1
    return out[:cap]


def _looks_like_ecommerce(home_links: list, sitemap_urls: list[str]) -> tuple[bool, int]:
    """UNIVERSAL e-commerce detection for the adaptive crawl budget (Pillar 1): a store exposes
    MANY product/collection pages. Counts DISTINCT URLs (homepage links + sitemap) that the
    existing page-type classifier labels PRODUCTS — a SIGNAL, never a vertical or a hardcoded
    name. Returns (is_store, product_url_count); early-exits at the threshold so a huge sitemap
    is not fully classified."""
    seen: set[str] = set()
    n_products = 0
    candidates = [getattr(l, "href", "") for l in (home_links or [])] + list(sitemap_urls or [])
    for u in candidates:
        if not u:
            continue
        try:
            key = normalize_url(u)
        except Exception:
            key = u
        if key in seen:
            continue
        seen.add(key)
        try:
            pt, _tier = classify_url(u)
        except Exception:
            continue
        if pt == PageType.PRODUCTS:
            n_products += 1
            if n_products >= ECOMMERCE_PRODUCT_URL_MIN:
                return True, n_products
    return False, n_products


def _register_fetched_or_skip(final_url: str, fetched_final_norms: set[str]) -> bool:
    """H2 redirect-dedup decision for ONE fetched page.

    Returns True if this page's FINAL (post-redirect) url normalizes to one we already
    fetched — the caller then skips it WITHOUT counting it as a kept page (it is
    duplicate content that would waste a budget slot and double-count in the evidence
    pack). Otherwise records it and returns False. CS-125: `normalize_url` delivers ONE
    canonical form (host lower-cased, default port + fragment + trailing slash dropped);
    the REDIRECT collapse comes from keying on the FINAL url, not from the normalizer. It
    does NOT fold http↔https — that is `dedup_key`'s job (used by the fetch frontier). Pure,
    so this guard is hermetically testable (MEASURED: daturial fetched its homepage 5x)."""
    key = normalize_url(final_url or "")
    if key in fetched_final_norms:
        return True
    fetched_final_norms.add(key)
    return False


# ---------------------------------------------------------------------
# Per-page extraction
# ---------------------------------------------------------------------

def _process_fetched_page(
    fetch_result,
    page_obj,
    page_type: PageType,
    tier: PageTier,
    out_dir: Path,
    page_index: int,
) -> tuple[PageRecord, list, SiteMetadata, list]:
    """Extract everything from a fetched page."""

    slug = _page_slug(fetch_result.final_url, page_type, page_index)

    # Text blocks
    text_blocks: list[TextBlock] = []
    if page_obj is not None:
        text_blocks = extract_text_blocks(page_obj, fetch_result.final_url)

    # API-driven SPA content (captured from same-site JSON during the fetch, e.g. ITI's programme
    # catalogue) becomes CITABLE text_blocks — so offerings/contacts/partners extracted from it
    # carry a real block_id and survive the evidence validator, instead of being invisible.
    api_text = getattr(fetch_result, "api_text", "") or ""
    if api_text:
        from .extractors.text_blocks import _slug_for_url
        slug = _slug_for_url(fetch_result.final_url)
        base = len(text_blocks)
        for j, piece in enumerate(s.strip() for s in api_text.split(" · ")):
            if len(piece) < 2 or j >= 150:                     # bounded
                continue
            text_blocks.append(TextBlock(
                block_id=f"{slug}_api_{base + j:04d}",
                page_url=fetch_result.final_url, tag="api",
                text=piece[:1000], section=Section.MAIN, selector="[api-json]",
            ))

    # Forms
    forms = extract_forms(fetch_result.html, fetch_result.final_url)

    # Metadata
    metadata = extract_metadata(fetch_result.html, fetch_result.final_url)

    # Content quality
    try:
        from .extractors.content_quality import extract_content_quality

        cleaned_main_text, has_meaningful_content = (
            extract_content_quality(
                fetch_result.html,
                fetch_result.final_url,
            )
        )

    except Exception:
        cleaned_main_text, has_meaningful_content = (None, False)

    # Next.js __NEXT_DATA__
    try:
        from .extractors.next_data import extract_next_data

        next_data_result = extract_next_data(
            fetch_result.html,
            fetch_result.final_url,
        )

    except Exception:
        from .extractors.next_data import NextDataResult

        next_data_result = NextDataResult()

    # Links — R1b: internality anchors to the FINAL (post-redirect) host, not the requested
    # one. assih.com now redirects to alameda-hc.com (the group portal); anchoring to the
    # requested host classified EVERY link on the landed page as external -> a 1-page scrape
    # with NO_INTERNAL_LINKS_FOUND. The landed page itself is the canonical site anchor.
    links = extract_links_from_html(
        fetch_result.html,
        fetch_result.final_url,
        fetch_result.final_url,
        text_blocks,
    )

    # Images
    images = extract_images_of_interest(
        fetch_result.html,
        fetch_result.final_url,
        metadata.og_image,
    )

    # Artifacts
    artifacts = _write_page_artifacts(
        out_dir,
        slug,
        fetch_result.html,
        fetch_result.rendered_text,
        fetch_result.full_screenshot_bytes,
        fetch_result.viewport_screenshot_bytes,
    )

    # Failures
    page_failures = []

    if getattr(fetch_result, "partial_content", False):
        page_failures.append(
            FailureRecord(
                code=ErrorCode.TIMEOUT,
                message=(
                    getattr(fetch_result, "partial_reason", None)
                    or "timeout_partial_content_salvaged"
                ),
                page_url=fetch_result.final_url,
            )
        )

    # -----------------------------------------------------------------
    # Merge next_data results
    # -----------------------------------------------------------------

    if next_data_result.found:

        # Schema blocks
        if next_data_result.schema_blocks:

            try:
                from .extractors.structured_data import (
                    dedupe_schema_blocks,
                )

                combined = (
                    list(metadata.schema_org)
                    + list(next_data_result.schema_blocks)
                )

                metadata = metadata.model_copy(
                    update={
                        "schema_org": dedupe_schema_blocks(combined)
                    }
                )

            except Exception:

                metadata = metadata.model_copy(
                    update={
                        "schema_org": (
                            list(metadata.schema_org)
                            + list(next_data_result.schema_blocks)
                        )
                    }
                )

        # CTA candidates
        if next_data_result.cta_candidates:

            existing = {
                (
                    (getattr(l, "anchor_text", "") or "")
                    .strip()
                    .lower(),

                    normalize_url(
                        getattr(l, "href", "")
                    ),
                )
                for l in links
            }

            for c in next_data_result.cta_candidates:

                key = (
                    (c.anchor_text or "")
                    .strip()
                    .lower(),

                    normalize_url(c.href),
                )

                if key not in existing:
                    existing.add(key)
                    links.append(c)

        # inferred internal urls
        if next_data_result.inferred_internal:

            existing_urls = {
                normalize_url(
                    getattr(l, "href", "")
                )
                for l in links
            }

            for inferred_link in next_data_result.inferred_internal:

                inferred_norm = normalize_url(
                    inferred_link.href
                )

                if inferred_norm not in existing_urls:
                    existing_urls.add(inferred_norm)
                    links.append(inferred_link)

    # -----------------------------------------------------------------
    # Dominance heuristic
    # -----------------------------------------------------------------

    dom_text_chars = sum(
        len(b.text)
        for b in text_blocks
        if b.text
    )

    next_data_likely_dominant = (
        next_data_result.found
        and (
            next_data_result.payload_size_bytes
            > 10 * max(dom_text_chars, 1)
        )
    )

    # -----------------------------------------------------------------
    # Final PageRecord
    # -----------------------------------------------------------------

    record = PageRecord(
        url=fetch_result.url,
        final_url=fetch_result.final_url,
        http_status=fetch_result.http_status,
        fetched_at=fetch_result.fetched_at,
        fetch_duration_ms=fetch_result.duration_ms,
        page_type=page_type,
        tier=tier,
        content_hash=fetch_result.content_hash,
        screenshot_hash=fetch_result.screenshot_hash,
        html_path=artifacts.get("html_path"),
        clean_text_path=artifacts.get("clean_text_path"),
        screenshot_full_path=artifacts.get(
            "screenshot_full_path"
        ),
        screenshot_viewport_path=artifacts.get(
            "screenshot_viewport_path"
        ),
        viewport_width=VIEWPORT_WIDTH,
        viewport_height=VIEWPORT_HEIGHT,
        text_blocks=text_blocks,
        forms=forms,
        failures=page_failures,
        cleaned_main_text=cleaned_main_text,
        has_meaningful_content=has_meaningful_content,
        next_data_found=next_data_result.found,
        next_data_payload_size_bytes=(
            next_data_result.payload_size_bytes
        ),
        next_data_likely_dominant=(
            next_data_likely_dominant
        ),
    )

    return (
        record,
        links,
        metadata,
        images,
        next_data_result.phones,
    )
# ---------------------------------------------------------------------
# Top-level scrape entry point
# ---------------------------------------------------------------------

def scrape(input_url: str, output_root: str = "", *, light: bool = False,
           thorough: bool = False, max_pages: Optional[int] = None,
           max_depth: Optional[int] = None,
           delay: Optional[float] = None) -> tuple[ScrapeManifest, Path]:
    """Scrape one business URL and return (manifest, output_dir).

    Always returns a manifest, even on failure — failures are recorded inside.

    light=True: a SHALLOW crawl (homepage + a few pages, no e-commerce budget upgrade) for
    COMPETITOR benchmarking — the comparison matrix needs only surface signals (whatsapp / cta /
    social / offerings COUNTS), and a full 30-page store crawl PER competitor made a competitive
    analysis of an e-commerce brand run >25 min and time out (MEASURED: rawafrican.net).

    thorough=True: restore the OLD load-every-homepage-image behaviour (disables the two-phase
    below-fold image-byte block). The default is the lighter two-phase footprint (owner ruling).
    """
    # ONE RESOLVER FOR THE CORPUS ROOT. `""` (the default) means "wherever the config says",
    # which is `scrapes` unless BASEERA_SCRAPES_DIR moves it off a slow synced drive. Repeating
    # the literal here is how a caller ends up writing to a different corpus than the one the
    # CLI reads — see `scraper/config.py:DEFAULT_OUTPUT_ROOT`.
    output_root = str(output_root or scrapes_root())
    two_phase = TWO_PHASE_IMAGE_FETCH and not thorough
    # None is how the direct scraper/CLI selects the longstanding config defaults.  API jobs pass
    # a concrete immutable request snapshot, including zero where zero has useful semantics.
    crawl_depth_limit = MAX_CRAWL_DEPTH if max_depth is None else max_depth
    inter_page_delay = INTER_PAGE_DELAY_SECONDS if delay is None else delay
    started = time.monotonic()
    # Validate the USER INPUT once, here at the boundary (ensure_scheme no longer validates,
    # so a malformed scraped LINK can't crash the crawl — but a concatenated/malformed input
    # URL is still rejected). The API rejects it earlier in its request schema.
    validate_input_url(input_url)
    normalized = normalize_url(input_url)
    # If the seed is a DEEP content page (e.g. ITI's /tracks/... page), anchor brand
    # IDENTITY to the site ROOT (homepage) instead — the deep page becomes a high-priority
    # subpage below, so its content (offerings/menu) is still captured. None for a root or
    # locale homepage (those are left exactly as-is). See url_utils.site_root_if_deep.
    deep_root = site_root_if_deep(normalized)
    home_fetch_url = deep_root or normalized
    out_dir = _make_output_dir(normalized, output_root)

    # ---- robots.txt -----------------------------------------------
    # ONE logical load per scrape, and its answer IS the policy for the whole run.
    #
    # `ScrapeManifest.robots` is a required field, so the manifest below used to be seeded with a
    # SECOND, throwaway `load_robots()` call whose parser was discarded — and the real load then
    # replaced its record. Two observations of the same endpoint, and the later one won: a site
    # that answered `Disallow: /` on the first and 429 on the second became "robots unavailable
    # -> assume allowed", and the crawl proceeded into the browser. Loading before the manifest
    # is built removes the second acquisition entirely; nothing is cached beyond this call frame,
    # so a separate scrape still observes robots for itself.
    rp, robots_record = load_robots(normalized)

    manifest = ScrapeManifest(
        scrape_meta=ScrapeMeta(
            input_url=input_url,
            normalized_url=normalized,
            scraped_at=utc_now(),
        ),
        robots=robots_record,
        readiness=_empty_readiness(),
    )
    if not robots_record.allowed:
        manifest.failures.append(FailureRecord(
            code=ErrorCode.ROBOTS_DISALLOWED,
            message="Homepage disallowed by robots.txt",
            page_url=normalized,
        ))
        manifest.scrape_meta.duration_ms = int((time.monotonic() - started) * 1000)
        manifest.readiness = compute_readiness(manifest)
        _write_manifest(manifest, out_dir)
        return manifest, out_dir

    # ---- Which representation does this site serve by default? ----
    # ONE preference-free HTTP GET, before any browser exists. Its only job is to let the SERVER
    # choose, so the browser locale below can follow that choice instead of making it. Without
    # this the crawl still selected a language: `navigator.language` is readable by the page, and
    # a locale-adaptive site routes on it even with Accept-Language absent.
    #
    # Cost is one request per scrape, not per page, and it is never retried: a failed probe simply
    # leaves the render locale on its deterministic fallback.
    # The authoritative robots decision governs THIS request too. `robots_record.allowed` was
    # computed for the input URL; when the seed was deep the probe targets the site root instead,
    # so ask the same parser about the URL actually being fetched.
    representation = (probe_neutral_representation(home_fetch_url)
                      if can_fetch(rp, home_fetch_url) else None)
    render_locale, locale_provenance = render_locale_for(representation)
    _served = getattr(representation, "observed_language", "") or "unknown"
    _note = f"representation: served={_served} render_locale={render_locale} ({locale_provenance})"
    if getattr(representation, "is_language_selector", False):
        # Recorded because it is an ANSWER — the site declined to name one language — and the
        # render locale below is therefore a fallback, not a finding about the brand.
        _note += f" selector_page=yes variants={sorted(representation.hreflang)}"
    if getattr(representation, "vary_accept_language", False):
        _note += " negotiates=yes"
    manifest.notes.append(_note)
    # STRUCTURED, because downstream has to reason about it. `browser_final_url` /
    # `representation_changed` are filled in after the homepage render below.
    manifest.language_evidence = LanguageEvidence(
        bootstrap_url=home_fetch_url,
        bootstrap_final_url=getattr(representation, "final_url", None),
        representation_language=getattr(representation, "observed_language", "") or "",
        render_locale=render_locale,
        render_locale_source=locale_provenance,
        is_language_selector=bool(getattr(representation, "is_language_selector", False)),
        negotiates_language=bool(getattr(representation, "vary_accept_language", False)),
        hreflang=dict(getattr(representation, "hreflang", {}) or {}),
        x_default_url=getattr(representation, "x_default_url", None),
    )

    # ---- Sitemap discovery (PR-1) ---------------------------------
    # Pull URLs from Sitemap: directives in robots.txt and, if those
    # weren't present, try the default /sitemap.xml location. URLs are
    # same-host filtered and capped (see scraper.sitemap). Failures
    # are recorded as notes but never block the crawl.
    try:
        from .sitemap import discover_sitemap_urls
        sitemap_result = discover_sitemap_urls(normalized, robots_record)
        if sitemap_result.notes:
            manifest.notes.extend(f"sitemap:{n}" for n in sitemap_result.notes)
    except Exception as exc:  # never fail the scrape because of sitemap issues
        logger.warning("sitemap discovery raised: %s", exc)
        from .sitemap import SitemapDiscoveryResult
        sitemap_result = SitemapDiscoveryResult()
        manifest.notes.append("sitemap:discovery_raised_exception")

    # ---- Browser session ------------------------------------------
    all_links = []
    all_html_by_page: dict[str, str] = {}
    all_text_blocks_by_page: dict[str, list[TextBlock]] = {}
    all_page_texts: list[str] = []
    html_lang_hints: list[str] = []
    site_metadata = SiteMetadata()
    visual_built = False
    # 2026-05 PR-next-data: phones extracted from Next.js __NEXT_DATA__
    # i18n keys flow up to manifest.contact alongside DOM/href-derived
    # phones at the manifest-finalization step below.
    all_next_data_phones: list = []

    with sync_playwright() as pw:
        # HTTP/2 STAYS ON UNTIL A SITE ACTUALLY BREAKS ON IT.
        #
        # `--disable-http2` was added for one measured case — lcwaikiki.eg reset HTTP/2 from
        # headless Chromium (net::ERR_HTTP2_PROTOCOL_ERROR, 1 page attempted, 0 succeeded) — and
        # was then applied to every site on the grounds that HTTP/1.1 is "universally safe". It is
        # not. Forcing HTTP/1.1 changes our connection fingerprint, and MEASURED on
        # alfouadpharmacies.com (2026-07-25, same URL, seconds apart, the flag the only difference):
        #
        #     args = []                   ->  HTTP 200 · 1,328,616 bytes · 5,570 chars rendered
        #     args = ['--disable-http2']  ->  HTTP 429 ·       169 bytes ·     18 chars rendered
        #
        # and that 429 was then reported to the owner as "this site blocks crawlers, retrying will
        # not help" — about a site that serves us 1.3 MB happily. A workaround for one site had
        # become a handicap on the rest.
        #
        # So: try HTTP/2 first, and fall back to HTTP/1.1 only when the homepage fails with the
        # protocol error that motivated the flag in the first place. lcwaikiki still works; nobody
        # else pays for it.
        browser = _launch_chromium(pw)
        _http2_disabled = False
        try:
            context = make_browser_context(browser, locale=render_locale)
            try:
                # ---- Fetch homepage -------------------------------
                # Seed was deep -> fetch the site ROOT for identity. If the root is
                # unreachable, fall back to the original seed so we never lose a scrape
                # that the deep page itself would have served.
                home_result = fetch_page(context, home_fetch_url, keep_page=True,
                                         rate_limit_retries=HOMEPAGE_RATE_LIMIT_RETRIES,
                                         two_phase=two_phase)
                manifest.scrape_meta.pages_attempted += 1

                # THE HTTP/2 FALLBACK, now fired by the error that motivated it rather than by
                # default. lcwaikiki.eg resets HTTP/2 from headless Chromium and cannot load a
                # single page; when we see that specific transport error, relaunch on HTTP/1.1 and
                # try once more. Every other site keeps HTTP/2 — and keeps the 200 that forcing
                # HTTP/1.1 was costing it.
                if not home_result.ok and _looks_like_http2_failure(home_result) \
                        and not _http2_disabled:
                    manifest.notes.append("http2_protocol_error_retried_over_http1")
                    if home_result._page is not None:
                        try:
                            home_result._page.close()
                        except Exception:
                            pass
                    try:
                        context.close()
                        browser.close()
                    except Exception:
                        pass
                    browser = _launch_chromium(pw, http2=False)
                    _http2_disabled = True
                    context = make_browser_context(browser, locale=render_locale)
                    home_result = fetch_page(context, home_fetch_url, keep_page=True,
                                             rate_limit_retries=HOMEPAGE_RATE_LIMIT_RETRIES,
                                             two_phase=two_phase)
                    manifest.scrape_meta.pages_attempted += 1
                if deep_root and not home_result.ok and home_fetch_url != normalized:
                    manifest.notes.append(
                        f"root_anchor_unreachable_fell_back_to_seed:{home_result.error_code}")
                    if home_result._page is not None:
                        try:
                            home_result._page.close()
                        except Exception:
                            pass
                    deep_root = None
                    home_fetch_url = normalized
                    home_result = fetch_page(context, normalized, keep_page=True,
                                             rate_limit_retries=HOMEPAGE_RATE_LIMIT_RETRIES,
                                             two_phase=two_phase)
                    manifest.scrape_meta.pages_attempted += 1
                manifest.scrape_meta.bytes_downloaded += home_result.bytes_downloaded

                if not home_result.ok:
                    manifest.failures.append(FailureRecord(
                        code=home_result.error_code or ErrorCode.UNKNOWN_ERROR,
                        message=home_result.error_message or "homepage fetch failed",
                        page_url=normalized,
                    ))
                    if home_result._page is not None:
                        try:
                            home_result._page.close()
                        except Exception:
                            pass
                    # Tier-3 Deep Search fallback: the direct scrape got nothing
                    # first-party (bot wall / empty / network). Recover SECONDARY
                    # evidence from web search before giving up. This is honest and
                    # supplementary — it does NOT flip readiness to ready.
                    try:
                        from .deep_search import gather_deep_search_evidence
                        manifest.deep_search = gather_deep_search_evidence(normalized)
                        if manifest.deep_search and manifest.deep_search.used:
                            manifest.notes.append(
                                "deep_search fallback used (blocked/empty scrape): "
                                f"name={manifest.deep_search.business_name!r}, "
                                f"{len(manifest.deep_search.social_links)} social, "
                                f"{len(manifest.deep_search.sources)} sources."
                            )
                    except Exception as exc:                       # never crash the run
                        manifest.notes.append(f"deep_search fallback errored: {exc!r}")
                    manifest.scrape_meta.duration_ms = int((time.monotonic() - started) * 1000)
                    manifest.readiness = compute_readiness(manifest)
                    _write_manifest(manifest, out_dir)
                    return manifest, out_dir

                manifest.scrape_meta.final_url = home_result.final_url
                if manifest.language_evidence is not None:
                    # Did the BROWSER end up somewhere other than the representation the server
                    # chose? Compared on the normalized URL, not on a path suffix: "/en" in a URL
                    # is a guess, a different destination is a fact.
                    _boot = manifest.language_evidence.bootstrap_final_url or home_fetch_url
                    manifest.language_evidence.browser_final_url = home_result.final_url
                    manifest.language_evidence.representation_changed = (
                        normalize_url(str(home_result.final_url or "")) != normalize_url(str(_boot))
                    )
                manifest.scrape_meta.pages_succeeded += 1

                # Visual identity from homepage (only place we read computed CSS)
                computed = extract_from_page(home_result._page, home_result.final_url)
                manifest.visual = build_visual_identity(
                    home_result.full_screenshot_bytes, computed, home_result.final_url
                )
                visual_built = True

                # Process homepage extractions
                home_type, home_tier = classify_homepage(home_result.final_url)
                home_record, home_links, home_meta, home_images, home_nd_phones = _process_fetched_page(
                    home_result, home_result._page, home_type, home_tier, out_dir, 0
                )

                # Close the live homepage page
                try:
                    home_result._page.close()
                except Exception:
                    pass

                manifest.pages.append(home_record)
                all_links.extend(home_links)
                all_html_by_page[home_result.final_url] = home_result.html
                all_text_blocks_by_page[home_result.final_url] = home_record.text_blocks
                all_page_texts.append(home_result.rendered_text)
                all_next_data_phones.extend(home_nd_phones)
                if home_meta.html_lang:
                    html_lang_hints.append(home_meta.html_lang)
                site_metadata = merge_metadata(home_meta, site_metadata)
                manifest.images_of_interest.extend(home_images)
                # A non-fatal homepage screenshot failure: the scrape is kept (its
                # text/links/offerings are intact); only the screenshot-derived visual
                # identity degrades. Surfaced honestly rather than discarding the page.
                if getattr(home_result, "screenshot_failed", False):
                    manifest.notes.append("homepage_screenshot_failed (non-fatal; visual "
                                          "identity degraded to logo pixels + header/footer)")

                # ---- Adaptive budget: detect e-commerce UNIVERSALLY (product-URL density) so a
                # store (100-300+ pages) is crawled deeper than the default 12/150s. Raises BOTH
                # the page cap AND the time budget (raising the cap alone is a no-op — MEASURED).
                # NOTE (H1): this initial detection reads ONLY the homepage snapshot's links,
                # which a JS store can under-render nondeterministically (MEASURED: nahdi flipped
                # 6-vs-24 pages on the same store, same morning, when the sitemap host was dead —
                # homepage had 2 product links one run, 164 another). The signal is therefore
                # RE-EVALUATED over accumulated links inside the fetch loop below.
                is_store, n_prod = _looks_like_ecommerce(home_links, sitemap_result.urls)
                if light:                      # competitor benchmarking: surface signals only
                    is_store, budget_secs = False, 70
                    page_cap = 4 if max_pages is None else min(4, max_pages)
                elif max_pages is not None:
                    # An explicit request ceiling wins over the adaptive store upgrade.  The store
                    # signal may still select its established time budget; it may not buy more
                    # page slots than the caller requested.
                    page_cap = max_pages
                    budget_secs = ECOMMERCE_BUDGET_SECONDS if is_store else TOTAL_BUDGET_SECONDS
                else:
                    page_cap = ECOMMERCE_MAX_INTERNAL_PAGES if is_store else MAX_INTERNAL_PAGES
                    budget_secs = ECOMMERCE_BUDGET_SECONDS if is_store else TOTAL_BUDGET_SECONDS
                if crawl_depth_limit == 0:
                    page_cap = 0
                if is_store:
                    manifest.notes.append(
                        f"E-commerce detected ({n_prod}+ product URLs) -> adaptive crawl budget "
                        f"(cap={page_cap}, {budget_secs}s)"
                    )

                # ---- Select subpages -----------------------------
                # Build the frontier at the ECOMMERCE max cap so a mid-crawl store upgrade
                # (H1) has candidates to fetch; `page_cap` (below) bounds how many we
                # actually consume and stays at the small default until the signal flips.
                # FIX-R1 (measured on assih.com -> alameda-hc.com): when the homepage
                # REDIRECTS to a different registrable host (the canonical "site moved /
                # merged" signal), internality must re-anchor to the FINAL host — otherwise
                # every link on the landed page classifies EXTERNAL and the crawl dies at
                # 1 page with NO_INTERNAL_LINKS_FOUND. Universal: any brand, any migration.
                anchor_url = normalized
                if (home_result.final_url
                        and not same_registrable_host(home_result.final_url, normalized)):
                    anchor_url = home_result.final_url
                    manifest.notes.append(
                        f"cross_domain_redirect: internality re-anchored to "
                        f"{home_result.final_url} (requested {normalized})")
                frontier_cap = (
                    ECOMMERCE_MAX_INTERNAL_PAGES if max_pages is None else max_pages
                )
                if crawl_depth_limit == 0:
                    frontier_cap = 0
                subpages = _select_subpages_to_fetch(
                    home_links, anchor_url, home_result.final_url,
                    extra_urls=sitemap_result.urls, cap=frontier_cap,
                )
                # Re-anchored to the root -> ensure the ORIGINAL deep seed is fetched
                # FIRST (its content — offerings / menu / track — is why the user gave
                # that URL). Deduped against the selected subpages.
                if deep_root and normalize_url(home_result.final_url) != normalize_url(normalized):
                    seed_pt, seed_tier = classify_url(normalized)
                    subpages = [(normalized, seed_pt, seed_tier, "")] + [
                        s for s in subpages
                        if normalize_url(s[0]) != normalize_url(normalized)
                    ]

                # URL-less 'modal' detail discovery: some sites reveal a page's real content via a
                # JS .load(...) that BUILDS the detail URL from a data-attribute (no href) — e.g.
                # NTI course popovers -> pages/modules/<data-target>.html. We resolve those and add
                # them to the frontier so their text is actually scraped. Bounded by a per-crawl cap
                # + a shared JS-body cache (the .load template lives in ONE first-party script).
                _js_cache: dict = {}

                def _cached_js(u, _c=_js_cache):
                    if u not in _c:
                        from .ajax_details import _default_script_fetch
                        _c[u] = _default_script_fetch(u)
                    return _c[u]

                _ajax_added = 0
                # 120 (was 40): modal detail pages are tiny + light-fetched; MEASURED on nti:
                # 7 course categories x ~6-10 modals each blew past 40 and the owner's exact
                # course (catID=205 -> 2631.html) died on the cap with 140s of budget unused.
                # The time budget remains the real bound.
                _AJAX_MAX = 120
                # CS-94: the runtime frontier de-dupes on the SAME scheme/slash-insensitive key as
                # `_select_subpages_to_fetch` (dedup_key), not normalize_url — otherwise a URL that
                # collapses under dedup_key but differs under normalize_url (http/https, trailing
                # slash) re-entered the frontier via the BFS/ajax feeds and was fetched twice.
                frontier_norms = {dedup_key(s[0]) for s in subpages}
                # FIX-A: depth per frontier URL (homepage=0, its links=1). Links found on a
                # depth-d page re-seed the frontier at d+1, up to MAX_CRAWL_DEPTH.
                depth_by_norm = {normalize_url(s[0]): 1 for s in subpages}
                _bfs_added_total = 0

                def _add_ajax_details(
                    page_html: str, page_final_url: str, insert_at: int = 0,
                    page_depth: int = 0,
                ) -> int:
                    """Resolve JS-built detail URLs on a page and splice them into the frontier at
                    `insert_at` (right AFTER the page being processed) so they're fetched NEXT —
                    they're the real content the user came for, not tail candidates. Returns how
                    many were added. Best-effort; never raises into the crawl."""
                    nonlocal _ajax_added, page_cap
                    if (light or (max_depth is not None and page_depth >= crawl_depth_limit)
                            or _ajax_added >= _AJAX_MAX or not page_html):
                        return 0
                    added = 0
                    try:
                        from .ajax_details import discover_ajax_details
                        pos = insert_at
                        for du in discover_ajax_details(page_html, page_final_url, fetch=_cached_js):
                            # CS-49→CS-79: the Ajax feed must enforce the SAME same-host guard the
                            # three other frontier feeds do (crawler.py:165,183,262) — otherwise an
                            # Ajax-discovered off-site URL gets crawled and its content attributed to
                            # this brand. `_same_origin` inside discover_* is netloc-exact; use the
                            # registrable-host check the rest of the crawler trusts.
                            if not same_registrable_host(du, normalized):
                                continue
                            ndu = dedup_key(du)          # CS-94: one frontier key everywhere
                            if ndu in frontier_norms:
                                continue
                            frontier_norms.add(ndu)
                            dpt, dtier = classify_url(du)
                            subpages.insert(pos, (du, dpt, dtier, ""))   # splice in ahead of the tail
                            depth_by_norm[normalize_url(du)] = page_depth + 1
                            pos += 1
                            added += 1
                            _ajax_added += 1
                            if _ajax_added >= _AJAX_MAX:
                                break
                    except Exception:  # noqa: BLE001 — never fail the crawl over this
                        return added
                    # Give the resolved detail pages their OWN slots so they don't push planned
                    # pages past the cap (the time budget still bounds the crawl). Bounded by _AJAX_MAX.
                    if max_pages is None:
                        page_cap += added
                    return added

                _home_ajax = _add_ajax_details(home_result.html or "", home_result.final_url)
                if _home_ajax:
                    manifest.notes.append(
                        f"ajax_modal_details(home): +{_home_ajax} JS-built detail URLs resolved")

                # H2 redirect-dedup: a selected subpage URL can redirect to (or serve) a
                # page we ALREADY fetched (the homepage, or another subpage). Seed the
                # set with the homepage's final url; each fetched subpage is checked via
                # _register_fetched_or_skip below.
                fetched_final_norms: set[str] = set()
                _register_fetched_or_skip(home_result.final_url, fetched_final_norms)

                # ---- Fetch subpages within budget ----------------
                for i, (sub_url, pt, tier, anchor) in enumerate(subpages, start=1):
                    # Effective page cap — bounds how many frontier candidates we consume.
                    # It CAN rise mid-crawl when the store signal flips (H1), so it is
                    # checked live here rather than baked into the frontier length.
                    if (i - 1) >= page_cap:
                        break
                    elapsed = time.monotonic() - started
                    # ALWAYS attempt a minimum number of subpages: pre-crawl stages
                    # (dead sitemaps, a heavy homepage) can eat the whole budget, and
                    # a check that fires at i=1 yields a homepage-only scrape of a
                    # 300-link site (measured on nahdionline: budget_exceeded after
                    # 0 subpages -> no product pages -> "no prices" downstream).
                    if elapsed > budget_secs and (i - 1) >= MIN_SUBPAGE_ATTEMPTS:
                        manifest.scrape_meta.budget_exceeded = True
                        manifest.notes.append(
                            f"Budget exceeded after {i-1} subpages (elapsed {elapsed:.1f}s)"
                        )
                        break

                    if not can_fetch(rp, sub_url):
                        manifest.failures.append(FailureRecord(
                            code=ErrorCode.ROBOTS_DISALLOWED,
                            message="Subpage disallowed by robots.txt",
                            page_url=sub_url,
                        ))
                        continue

                    # Polite delay
                    effective_delay = max(inter_page_delay, manifest.robots.crawl_delay_seconds)
                    time.sleep(effective_delay)

                    manifest.scrape_meta.pages_attempted += 1
                    # CRASH-SAFETY: a single heavy page can hard-fail (Playwright
                    # context death, OOM, an extractor raising). Without this guard the
                    # exception unwinds past the whole crawl and NO manifest is written
                    # — every already-scraped page is lost. Catch it, record a failure,
                    # and keep going so the scrape always yields what it got.
                    try:
                        # LIGHT fetch: block heavy resources + skip the screenshot. Sub-pages are
                        # crawled for text + links (visual identity is the homepage's job), so this
                        # cuts per-page render time — the lever that makes deep e-commerce crawls
                        # fit the budget (the page cap/time bind together at ~11-16s/page).
                        sub_result = fetch_page(context, sub_url, keep_page=True, light=True)
                        manifest.scrape_meta.bytes_downloaded += sub_result.bytes_downloaded

                        if not sub_result.ok:
                            manifest.failures.append(FailureRecord(
                                code=sub_result.error_code or ErrorCode.UNKNOWN_ERROR,
                                message=sub_result.error_message or "subpage fetch failed",
                                page_url=sub_url,
                            ))
                            if sub_result._page is not None:
                                try:
                                    sub_result._page.close()
                                except Exception:
                                    pass
                            continue

                        # H2: skip a page whose FINAL (post-redirect) url we already
                        # fetched — duplicate content that would waste this slot and
                        # double-count in the evidence pack. It counts as an attempt
                        # (we did fetch it) but not as a succeeded/kept page.
                        if _register_fetched_or_skip(sub_result.final_url, fetched_final_norms):
                            manifest.notes.append(
                                f"redirect_duplicate_skipped: {sub_url} -> {sub_result.final_url}"
                            )
                            if sub_result._page is not None:
                                try:
                                    sub_result._page.close()
                                except Exception:
                                    pass
                            continue

                        manifest.scrape_meta.pages_succeeded += 1

                        sub_record, sub_links, sub_meta, sub_images, sub_nd_phones = _process_fetched_page(
                            sub_result, sub_result._page, pt, tier, out_dir, i
                        )
                        try:
                            sub_result._page.close()
                        except Exception:
                            pass

                        manifest.pages.append(sub_record)
                        all_links.extend(sub_links)
                        # This subpage may itself reveal url-less 'modal' details (e.g. the NTI
                        # category page carries the course popovers). Splice them in right AFTER the
                        # current page (0-based index i) so they're fetched next, not at the tail.
                        _cur_depth = depth_by_norm.get(normalize_url(sub_url), 1)
                        _sub_ajax = _add_ajax_details(
                            sub_result.html or "", sub_result.final_url,
                            insert_at=i, page_depth=_cur_depth,
                        )
                        if _sub_ajax:
                            manifest.notes.append(
                                f"ajax_modal_details({sub_url}): +{_sub_ajax} JS-built detail URLs")
                        # FIX-A (multi-depth crawl): the links THIS page revealed re-seed the
                        # frontier tail — category pages surface their leaf items (courses,
                        # services, menu dishes) which the old build-once frontier never fetched.
                        # Bounded by the same live page_cap/time checks + BFS_MAX_NEW_CANDIDATES.
                        if (not light and _cur_depth < crawl_depth_limit
                                and _bfs_added_total < BFS_MAX_NEW_CANDIDATES):
                            _new = _bfs_new_candidates(
                                sub_links, anchor_url, frontier_norms,
                                max_new=BFS_MAX_NEW_CANDIDATES - _bfs_added_total)
                            if _new:
                                for cand in _new:
                                    depth_by_norm[cand[0]] = _cur_depth + 1
                                # LEAF candidates SPLICE right after the current page (the ajax
                                # resolver's philosophy: they're the real content, not tail
                                # material) — MEASURED: nti's depth-3 coursesev leaves were
                                # discovered (+7/+20 per hub) yet the run always ended before
                                # the tail, so the owner's course modals never got fetched.
                                # Non-leaf discoveries still queue at the tail.
                                leaf_new = [c for c in _new if is_leaf_detail(c[0])]
                                tail_new = [c for c in _new if not is_leaf_detail(c[0])]
                                for k, cand in enumerate(leaf_new):
                                    subpages.insert(i + _sub_ajax + k, cand)
                                subpages.extend(tail_new)
                                _bfs_added_total += len(_new)
                                manifest.notes.append(
                                    f"bfs_reseed({sub_url}): +{len(_new)} depth-{_cur_depth + 1} "
                                    f"candidates ({len(leaf_new)} leaf spliced ahead)")
                        # H1: re-evaluate the store signal over the ACCUMULATED links as
                        # subpages reveal more product URLs, and upgrade the budget ONCE if
                        # it crosses the threshold — the one-shot homepage detection above
                        # under-counts when a JS store renders few links on the snapshot
                        # (offline-validated: nahdi's failing 6-page run had 15 product
                        # URLs across the pages it DID fetch — enough to flip).
                        if (not is_store and not light and max_pages is None):
                            # A caller-supplied page ceiling is immutable for this job; only jobs
                            # using the crawler default participate in the adaptive cap upgrade.
                            is_store, n_prod = _looks_like_ecommerce(all_links, sitemap_result.urls)
                            if is_store:
                                page_cap = ECOMMERCE_MAX_INTERNAL_PAGES
                                budget_secs = ECOMMERCE_BUDGET_SECONDS
                                manifest.notes.append(
                                    f"E-commerce detected mid-crawl ({n_prod}+ product URLs) "
                                    f"-> budget upgraded (cap={page_cap}, {budget_secs}s)"
                                )
                        all_html_by_page[sub_result.final_url] = sub_result.html
                        all_text_blocks_by_page[sub_result.final_url] = sub_record.text_blocks
                        all_page_texts.append(sub_result.rendered_text)
                        all_next_data_phones.extend(sub_nd_phones)
                        if sub_meta.html_lang:
                            html_lang_hints.append(sub_meta.html_lang)
                        site_metadata = merge_metadata(site_metadata, sub_meta)
                        manifest.images_of_interest.extend(sub_images)
                    except Exception as exc:                          # noqa: BLE001
                        manifest.failures.append(FailureRecord(
                            code=ErrorCode.UNKNOWN_ERROR,
                            message=f"subpage crashed, skipped: {type(exc).__name__}: {exc}",
                            page_url=sub_url,
                        ))
                        continue

            finally:
                try:
                    context.close()
                except Exception:
                    pass
        finally:
            try:
                browser.close()
            except Exception:
                pass

    # ---- Site-wide aggregations -----------------------------------
    manifest.site_metadata = site_metadata
    # Phone region comes from the SITE's own reliable signal (locale path / ccTLD)
    # before the home-market fallback — never a blanket country: a national number
    # parsed under the wrong region is a valid-looking WRONG number (nahdi Saudi
    # 920024673 -> +20920024673; its /ar-sa now correctly resolves to SA).
    manifest.contact = extract_contact(
        all_html_by_page, all_text_blocks_by_page,
        site_url=(manifest.scrape_meta.final_url or normalized),
    )
    # 2026-05 PR-next-data: merge Next.js-derived phones (whitelisted
    # i18n hotline keys) into manifest.contact.phones. Dedupe against
    # the DOM/href-derived phones already there by digit-string.
    if all_next_data_phones:
        seen = set()
        for p in manifest.contact.phones:
            key = p.e164 or p.raw
            if key:
                seen.add(key)
        for p in all_next_data_phones:
            key = p.e164 or p.raw
            if not key or key in seen:
                continue
            seen.add(key)
            manifest.contact.phones.append(p)
    # Enrich contact addresses from schema.org PostalAddress
    if manifest.pages:
        fill_addresses_from_schema_org(
            manifest.contact, site_metadata.schema_org, manifest.pages[0].final_url
        )
    manifest.links = build_inventory(all_links)
    manifest.languages = detect_languages(all_page_texts, html_lang_hints)
    # The site's OWN answer, union over every page that published an alternate.
    try:
        from .extractors.language import declared_languages
        _declared = set()
        for _html in (all_html_by_page or {}).values():
            _declared.update(declared_languages(_html))
        manifest.declared_languages = sorted(_declared)
    except Exception:  # noqa: BLE001 — a missing tag must never break a crawl
        manifest.declared_languages = []
    try:
        from .extractors.testimonials import extract_testimonials
        _seen, _revs = set(), []
        for _html in (all_html_by_page or {}).values():
            for _t in extract_testimonials(_html):
                _k = (_t["author"], _t["quote"][:60])
                if _k in _seen:
                    continue
                _seen.add(_k)
                _revs.append(_t)
        manifest.testimonials = _revs
    except Exception:  # noqa: BLE001 — an unreadable review section must never break a crawl
        manifest.testimonials = []

    if not visual_built:
        # Should not happen since homepage build is required, but guard
        manifest.notes.append("Visual identity was not extracted (no homepage screenshot)")

    if not manifest.links.internal:
        manifest.notes.append("No internal links discovered from homepage")
        manifest.failures.append(FailureRecord(
            code=ErrorCode.NO_INTERNAL_LINKS_FOUND,
            message="Homepage had no usable internal links",
            page_url=manifest.scrape_meta.final_url or normalized,
        ))

    # Finalize
    manifest.scrape_meta.duration_ms = int((time.monotonic() - started) * 1000)

    # PR-1: ScrapeDiagnostics — small counts that explain "how
    # comprehensive was the crawl". The business_profile readiness
    # layer (Stage F-readiness) reads these to decide whether
    # single-page evidence is a hard block or a soft warning.
    from .schemas import ScrapeDiagnostics
    # PR-3: prefer the trafilatura-derived `has_meaningful_content`
    # signal when it's populated. Falls back to "has any text_blocks"
    # for pages where content_quality couldn't run (e.g. trafilatura
    # missing). The fallback preserves the pre-PR-3 behavior.
    def _page_carries_real_text(p) -> bool:
        if getattr(p, "has_meaningful_content", False):
            return True
        # Fallback: pages with text_blocks but no content_quality signal
        # (could be a legacy manifest, or trafilatura wasn't installed).
        if p.cleaned_main_text is None and p.text_blocks:
            return True
        return False
    pages_with_text = sum(1 for p in manifest.pages if _page_carries_real_text(p))
    pages_failed = sum(
        1 for f in manifest.failures
        if f.code in (ErrorCode.ROBOTS_DISALLOWED, ErrorCode.UNKNOWN_ERROR)
        or (f.page_url and f.page_url != normalized)
    )
    # `pages_discovered` reflects all URLs that survived classification
    # (homepage + sitemap, post-dedupe), before the per-scrape budget
    # potentially cut them off. We approximate from what we see in the
    # manifest plus any uncrawled candidates: in practice equal to
    # pages_attempted when budget wasn't exceeded.
    discovered = max(
        manifest.scrape_meta.pages_attempted,
        len(manifest.pages) + len(sitemap_result.urls),
    )

    manifest.scrape_diagnostics = ScrapeDiagnostics(
        pages_discovered=discovered,
        pages_scraped=manifest.scrape_meta.pages_succeeded,
        pages_with_text_blocks=pages_with_text,
        pages_failed=pages_failed,
        sitemap_used=sitemap_result.sitemap_used,
        sitemap_urls_found=sitemap_result.sitemap_urls_found,
        js_rendering_used=True,
    )

    manifest.readiness = compute_readiness(manifest)

    _write_manifest(manifest, out_dir)
    return manifest, out_dir


# ---------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------

def _empty_readiness():
    from .schemas import ReadinessReport
    return ReadinessReport(
        has_homepage=False, has_internal_pages=False,
        has_contact_signals=False, has_visual_signals=False,
        has_cta_candidates=False, has_metadata=False,
        major_missing=[], ready_for_extraction=False,
    )


def _write_manifest(manifest: ScrapeManifest, out_dir: Path) -> None:
    p = out_dir / "manifest.json"
    p.write_text(
        manifest.model_dump_json(indent=2, exclude_none=False),
        encoding="utf-8",
    )
