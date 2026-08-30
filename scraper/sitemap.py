"""Sitemap discovery (PR-1).

Fetches a site's sitemap and returns the URLs it lists, with these
guarantees:

- Same-host filter: only URLs whose registrable host matches the input
  URL are kept. No cross-site URLs leak into the crawl queue.
- URL cap: at most `MAX_SITEMAP_URLS` (default 200) URLs per host.
- Sitemap-index recursion depth: 1 level. A sitemap-of-sitemaps fans
  out one step. Sub-sitemaps that themselves contain sitemap-of-sitemaps
  are NOT followed further, to keep the budget bounded.
- Gzipped sitemaps (.xml.gz / Content-Encoding: gzip) are decoded.
- Malformed XML is tolerated: a parse failure on one sitemap yields an
  empty list for that file, not a crash. The caller continues.
- The module has NO Playwright dependency. It uses urllib like robots.py.

Public surface:
    discover_sitemap_urls(input_url, robots_record) -> SitemapDiscoveryResult

The caller is the crawler. It feeds the result into the existing
`_select_subpages_to_fetch` so sitemap URLs go through the same
classify_url() + tier-priority pipeline as homepage-discovered links.
"""
from __future__ import annotations

import gzip
import logging
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import Optional
from urllib.parse import urljoin, urlparse, urlunparse
from urllib.request import Request, urlopen

from .config import USER_AGENT
from .schemas import RobotsRecord
from .url_utils import normalize_url, same_registrable_host

logger = logging.getLogger(__name__)


# ---- Limits (you confirmed these on 2026-05-21) ---------------------
MAX_SITEMAP_URLS = 300   # more /products/<slug> candidates for the frontier's product quota to spread across
MAX_INDEX_RECURSION_DEPTH = 1  # one level of sitemap-index fan-out
SITEMAP_FETCH_TIMEOUT_S = 5.0
MAX_SITEMAP_BYTES = 8 * 1024 * 1024  # 8 MiB hard cap per file
# Hard ceiling for the WHOLE sitemap stage: sitemaps are a shortcut, never worth
# starving the actual page crawl (measured on nahdi: a dead sitemap host serially
# burned the budget before any subpage was fetched).
SITEMAP_STAGE_MAX_S = 20.0

# Namespaces seen in real sitemaps.
_NS = {
    "sm": "http://www.sitemaps.org/schemas/sitemap/0.9",
    # Older Google-style namespace, occasionally still in the wild.
    "sm0": "http://www.google.com/schemas/sitemap/0.84",
}


@dataclass
class SitemapDiscoveryResult:
    """Outcome of a sitemap discovery pass for one host."""
    urls: list[str] = field(default_factory=list)        # same-host URLs, deduped, capped
    sitemap_used: bool = False                            # any sitemap actually contributed URLs
    sitemap_urls_found: int = 0                           # pre-cap, pre-same-host filter count
    sitemaps_attempted: list[str] = field(default_factory=list)  # which sitemap files we tried
    notes: list[str] = field(default_factory=list)        # human-readable diagnostics


# ---------------------------------------------------------------------
# Public entry
# ---------------------------------------------------------------------

def discover_sitemap_urls(
    input_url: str,
    robots_record: RobotsRecord,
) -> SitemapDiscoveryResult:
    """Discover URLs from sitemap.xml files for the given site.

    Order of attempts:
      1. Sitemap directives discovered in robots.txt.
      2. Fallback: <host>/sitemap.xml at the standard location.

    Returns a SitemapDiscoveryResult. Never raises — failures are
    recorded as notes and the result is degraded gracefully.
    """
    result = SitemapDiscoveryResult()

    candidates: list[str] = list(robots_record.sitemap_urls or [])
    if not candidates:
        candidates = [_default_sitemap_url(input_url)]

    seen_urls: set[str] = set()
    raw_count = 0
    sitemap_files_processed = 0
    # STAGE guards (measured on nahdionline.com: robots advertised 10+ sitemap files
    # on a DEAD host — each burned a full fetch timeout SEQUENTIALLY, eating the crawl
    # budget before a single subpage was fetched):
    #  - consecutive-unreachable breaker: 2 dead fetches in a row -> stop trying
    #    (the host is down; the 3rd..10th file won't differ);
    #  - stage time cap: the whole sitemap stage may never spend more than
    #    SITEMAP_STAGE_MAX_S of the crawl's budget.
    import time as _time
    stage_started = _time.monotonic()
    consecutive_unreachable = 0

    for sm_url in candidates:
        if sitemap_files_processed >= 10:  # safety: never process more than 10 sitemap files
            result.notes.append("sitemap_file_cap_reached")
            break
        if consecutive_unreachable >= 2:
            result.notes.append("sitemap_unreachable_breaker_open")
            break
        if _time.monotonic() - stage_started > SITEMAP_STAGE_MAX_S:
            result.notes.append("sitemap_stage_time_cap_reached")
            break

        # 2026-05: reject cross-host top-level Sitemap directives BEFORE
        # attempting any fetch. Some sites advertise a sitemap on a
        # canonical host that differs from the host we're scraping (e.g.
        # buffaloburger.com → thebuffaloburger.com/sitemap.xml). Without
        # this guard we'd record the foreign sitemap as "attempted" in
        # diagnostics, which is misleading — we never actually used it.
        if not same_registrable_host(sm_url, input_url):
            result.notes.append(f"sitemap_cross_host_rejected:{sm_url}")
            continue

        result.sitemaps_attempted.append(sm_url)

        body = _fetch_sitemap_bytes(sm_url)
        sitemap_files_processed += 1
        if body is None:
            consecutive_unreachable += 1
            result.notes.append(f"sitemap_unreachable:{sm_url}")
            continue
        consecutive_unreachable = 0

        kind, entries = _parse_sitemap(body)
        if kind == "invalid":
            result.notes.append(f"sitemap_invalid:{sm_url}")
            continue

        if kind == "sitemap_index":
            # Recurse one level — fetch each child sitemap, gather URLs.
            for child_sm in entries[:10]:  # cap fan-out
                if sitemap_files_processed >= 10:
                    result.notes.append("sitemap_file_cap_reached")
                    break
                if consecutive_unreachable >= 2:
                    result.notes.append("sitemap_unreachable_breaker_open")
                    break
                if _time.monotonic() - stage_started > SITEMAP_STAGE_MAX_S:
                    result.notes.append("sitemap_stage_time_cap_reached")
                    break
                if not same_registrable_host(child_sm, input_url):
                    continue
                result.sitemaps_attempted.append(child_sm)
                sitemap_files_processed += 1
                child_body = _fetch_sitemap_bytes(child_sm)
                if child_body is None:
                    consecutive_unreachable += 1
                    result.notes.append(f"sitemap_unreachable:{child_sm}")
                    continue
                consecutive_unreachable = 0
                child_kind, child_entries = _parse_sitemap(child_body)
                if child_kind == "urlset":
                    raw_count += len(child_entries)
                    for u in child_entries:
                        if _accept_url(u, input_url, seen_urls):
                            seen_urls.add(_dedupe_key(u))
                            result.urls.append(u)
                            if len(result.urls) >= MAX_SITEMAP_URLS:
                                break
                elif child_kind == "sitemap_index":
                    # Per your policy: 1 level of sitemap-index recursion only.
                    # We do NOT descend into a sitemap-of-sitemaps inside a
                    # sitemap-of-sitemaps.
                    result.notes.append(f"sitemap_index_too_deep:{child_sm}")
                if len(result.urls) >= MAX_SITEMAP_URLS:
                    result.notes.append("url_cap_reached")
                    break
            continue

        # kind == "urlset"
        raw_count += len(entries)
        for u in entries:
            if _accept_url(u, input_url, seen_urls):
                seen_urls.add(_dedupe_key(u))
                result.urls.append(u)
                if len(result.urls) >= MAX_SITEMAP_URLS:
                    result.notes.append("url_cap_reached")
                    break
        if len(result.urls) >= MAX_SITEMAP_URLS:
            break

    result.sitemap_urls_found = raw_count
    result.sitemap_used = len(result.urls) > 0
    return result


# ---------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------

def _default_sitemap_url(input_url: str) -> str:
    p = urlparse(input_url)
    return urlunparse((p.scheme or "https", p.netloc, "/sitemap.xml", "", "", ""))


def _fetch_sitemap_bytes(url: str) -> Optional[bytes]:
    """Fetch a sitemap. Returns raw bytes, or None on any error.

    Tolerates: HTTP errors, timeouts, oversized responses, gzip
    transparently. Never raises.
    """
    try:
        # SSRF guard (P-60): a sitemap URL can be attacker-influenced (robots.txt Sitemap: line
        # or a guessed path on a redirected host). Never urlopen a non-public target.
        from .url_utils import is_safe_public_url
        if not is_safe_public_url(url):
            logger.debug("sitemap fetch blocked (non-public URL): %s", url)
            return None
        req = Request(
            url,
            headers={
                "User-Agent": USER_AGENT,
                "Accept-Encoding": "gzip",
                "Accept": "application/xml, text/xml, */*",
            },
        )
        with urlopen(req, timeout=SITEMAP_FETCH_TIMEOUT_S) as resp:
            data = resp.read(MAX_SITEMAP_BYTES + 1)
            if len(data) > MAX_SITEMAP_BYTES:
                logger.warning("sitemap too large, ignoring: %s", url)
                return None
            # Decompress if needed (Content-Encoding gzip or .gz extension)
            content_encoding = (resp.headers.get("Content-Encoding") or "").lower()
            if content_encoding == "gzip" or url.lower().endswith(".gz"):
                try:
                    data = gzip.decompress(data)
                except (OSError, EOFError) as exc:
                    logger.warning("sitemap gzip decode failed: %s (%s)", url, exc)
                    return None
            return data
    except Exception as exc:  # network, HTTP, etc.
        logger.debug("sitemap fetch failed: %s (%s)", url, exc)
        return None


def _parse_sitemap(body: bytes) -> tuple[str, list[str]]:
    """Parse a sitemap blob.

    Returns (kind, entries) where:
      kind == "urlset"         -> entries are URLs
      kind == "sitemap_index"  -> entries are sub-sitemap URLs
      kind == "invalid"        -> entries is []
    """
    try:
        root = ET.fromstring(body)
    except ET.ParseError:
        return ("invalid", [])

    # Strip namespace from the root tag to detect type robustly.
    tag = root.tag
    if "}" in tag:
        tag = tag.split("}", 1)[1]
    tag = tag.lower()

    if tag == "urlset":
        urls = _collect_loc_values(root, "url")
        return ("urlset", urls)
    if tag == "sitemapindex":
        urls = _collect_loc_values(root, "sitemap")
        return ("sitemap_index", urls)
    return ("invalid", [])


def _collect_loc_values(root: ET.Element, child_tag: str) -> list[str]:
    """Find every <child_tag><loc>...</loc></child_tag> across namespaces."""
    out: list[str] = []
    for child in root:
        # Strip namespace from the child tag
        c_tag = child.tag
        if "}" in c_tag:
            c_tag = c_tag.split("}", 1)[1]
        if c_tag.lower() != child_tag:
            continue
        loc = _find_loc(child)
        if loc:
            out.append(loc)
    return out


def _find_loc(element: ET.Element) -> Optional[str]:
    """Find a <loc> grand/child regardless of namespace."""
    for child in element:
        c_tag = child.tag
        if "}" in c_tag:
            c_tag = c_tag.split("}", 1)[1]
        if c_tag.lower() == "loc" and child.text:
            return child.text.strip()
    return None


def _accept_url(url: str, input_url: str, seen: set[str]) -> bool:
    """Decide whether to accept a sitemap-listed URL into the queue.

    Rules:
      - Must parse as http(s).
      - Must be same registrable host as input_url.
      - Must not already be queued (dedupe by normalized URL).
    """
    try:
        p = urlparse(url)
    except ValueError:
        return False
    if p.scheme not in ("http", "https"):
        return False
    if not p.netloc:
        return False
    if not same_registrable_host(url, input_url):
        return False
    key = _dedupe_key(url)
    if key in seen:
        return False
    return True


def _dedupe_key(url: str) -> str:
    """Normalize for dedupe — uses the project's normalize_url to match
    what the crawler uses elsewhere."""
    try:
        return normalize_url(url)
    except Exception:
        return url


# ---------------------------------------------------------------------
# Helper for robots.txt sitemap extraction
# ---------------------------------------------------------------------

def extract_sitemap_urls_from_robots(body: str, base_url: str) -> list[str]:
    """Pull every `Sitemap: <url>` line out of a robots.txt body.

    Per robots.txt spec, Sitemap directives are global (not tied to a
    User-agent). Relative URLs are resolved against `base_url`.
    """
    out: list[str] = []
    for raw_line in (body or "").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        if key.strip().lower() != "sitemap":
            continue
        url = value.strip()
        if not url:
            continue
        # Resolve relative against site root if needed
        if url.startswith("/"):
            url = urljoin(base_url, url)
        out.append(url)
    # Dedupe, preserve order
    seen: set[str] = set()
    result: list[str] = []
    for u in out:
        if u not in seen:
            seen.add(u)
            result.append(u)
    return result