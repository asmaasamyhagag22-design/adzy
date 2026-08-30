"""Deep Search fallback (Tier 3): secondary evidence for BLOCKED / empty sites.

When the direct scrape gets nothing first-party — a bot wall (Akamai/Cloudflare
"Access Denied"), a CAPTCHA, an empty DOM, or a hard network failure — we can't
read the site ourselves. Rather than fail with zero data, we ask a web-search
provider (Serper, via competitor.search_providers) what the public web ALREADY
knows about the business: the site's own search listing (the title + meta
description Google indexed), and any social profiles that surface.

HONESTY CONTRACT (the whole point — this must never erode zero-hallucination):
  * Every datum is SECONDARY, tagged source="deep_search:<provider>". It is what
    Google's index reports, NOT first-party site content.
  * It NEVER fabricates: no provider -> used=False; empty results -> empty fields.
  * It is SUPPLEMENTARY: the manifest stays 'not ready for extraction'. This gives
    an honest *degraded* signal, never a fake-complete profile.
"""
from __future__ import annotations

import re
from typing import List, Optional
from urllib.parse import urlparse

from .config import SOCIAL_DOMAINS
from .schemas import DeepSearchResult, DeepSearchSource


def _domain(url: str) -> str:
    host = (urlparse(url).netloc or url).lower().split(":")[0]
    return host.removeprefix("www.")


# Second-level qualifiers that precede a ccTLD (com.eg / co.uk / gov.sa / org.au ...) — not the brand.
_SECOND_LEVEL_LABELS = {"com", "co", "org", "net", "gov", "edu", "ac", "gob", "or", "ne", "mil"}


def _brand_guess(domain: str) -> str:
    """Readable brand guess = the REGISTRABLE domain's main label, not the leftmost (CS-124).
    'lcwaikiki.eg' -> 'lcwaikiki'; a SUBDOMAINED brand 'store.nike.com' -> 'nike' (was 'store', a
    meaningless subdomain token then searched on the open web); 'brand.com.eg' -> 'brand'."""
    parts = [p for p in domain.split(".") if p]
    if len(parts) <= 1:
        return parts[0] if parts else domain
    labels = parts[:-1]                                   # drop the TLD
    if len(labels) >= 2 and labels[-1] in _SECOND_LEVEL_LABELS:
        labels = labels[:-1]                              # drop a second-level ccTLD qualifier too
    return labels[-1]                                     # the registrable main label


def _clean_title(title: str) -> str:
    """Brand-ish name: text before the first separator."""
    for sep in (" | ", " - ", " – ", " — ", " :: ", ": "):
        if sep in title:
            return title.split(sep)[0].strip()
    return title.strip()


def _social_platform(url: str) -> Optional[str]:
    # CS-93: match the social domain as an EXACT host or a proper subdomain suffix, NOT a substring
    # (the bug links.py:67 already fixed) — `frag in host` let "box.com" match "dropbox.com",
    # "fb.com" match unrelated hosts, etc., mislabeling non-social sites as socials.
    host = _domain(url)
    for frag, _platform in SOCIAL_DOMAINS.items():
        if host == frag or host.endswith("." + frag):
            return _platform
    return None


def gather_deep_search_evidence(
    url: str,
    provider=None,
    *,
    results_per_query: int = 10,
) -> DeepSearchResult:
    """Recover secondary evidence about a blocked/empty site via web search.

    Returns a DeepSearchResult that is always honest about its provenance.
    Never raises for ordinary failures (no provider, empty results, network).
    """
    if provider is None:
        # Lazy import keeps `scraper` importable without the competitor package
        # and avoids any import-order coupling. The KEY must already be in the
        # environment — entry points load .env (scraper.__main__ / the API);
        # this library function never mutates os.environ (that would break test
        # isolation and is a side effect a pure helper shouldn't have).
        try:
            from competitor.search_providers import get_default_search_provider
            provider = get_default_search_provider()
        except Exception:
            provider = None

    if provider is None:
        return DeepSearchResult(
            used=False,
            note="no search provider configured (set SERPER_API_KEY / SEARCHAPI_API_KEY / GOOGLE_CSE_*)",
        )

    domain = _domain(url)
    brand = _brand_guess(domain)

    # Query 1: the site's OWN indexed listing — title + meta description Google
    # cached are real first-party content that survives even when we're blocked.
    own_hits = provider.search(f"site:{domain}", num=results_per_query) or []
    # Query 2: the brand broadly — surfaces official socials + an about-style blurb.
    brand_hits = provider.search(brand, num=results_per_query) or []

    # CS-49: track which links are FIRST-PARTY (from the site's own `site:` listing). A social
    # profile from the BROAD brand search only counts as the brand's if it is first-party OR its
    # handle contains the brand token — otherwise a same-named other company / a private person is
    # attributed to the brand ("NEVER fabricates" violated).
    _own_links = {(getattr(h, "link", "") or "") for h in own_hits}
    sources: List[DeepSearchSource] = []
    seen_links: set[str] = set()
    for hit in list(own_hits) + list(brand_hits):
        link = getattr(hit, "link", "") or ""
        if not link or link in seen_links:
            continue
        seen_links.add(link)
        sources.append(DeepSearchSource(
            title=getattr(hit, "title", "") or "",
            url=link,
            snippet=getattr(hit, "snippet", "") or "",
            rank=getattr(hit, "rank", 0) or 0,
        ))

    # business_name + description: prefer the site's own homepage listing (an
    # own_hit whose path is root/shallow), else the top own_hit, else the brand.
    business_name: Optional[str] = None
    description: Optional[str] = None
    own_sorted = sorted(
        (h for h in own_hits if getattr(h, "link", None)),
        key=lambda h: len(urlparse(h.link).path.rstrip("/")),
    )
    if own_sorted:
        top = own_sorted[0]
        if getattr(top, "title", None):
            business_name = _clean_title(top.title)
        if getattr(top, "snippet", None):
            description = top.snippet.strip() or None

    # social links: a result on a known social platform THAT IS PLAUSIBLY THE BRAND'S (CS-49) —
    # first-party (linked from the site's own listing) or a handle carrying the brand token. A
    # bare brand-search hit for a stranger's profile is dropped, never attributed.
    brand_tokens = {t for t in re.split(r"[^a-z0-9]+", (brand or "").lower()) if len(t) >= 3}
    social_links: List[str] = []
    seen_social: set[str] = set()
    for src in sources:
        if not _social_platform(src.url):
            continue
        handle = (urlparse(src.url).path or "").lower()
        is_first_party = src.url in _own_links
        handle_matches = any(t in handle for t in brand_tokens)
        if not (is_first_party or handle_matches):
            continue                              # a same-named stranger's profile — not the brand's
        key = _domain(src.url) + urlparse(src.url).path.rstrip("/")
        if key not in seen_social:
            seen_social.add(key)
            social_links.append(src.url)

    return DeepSearchResult(
        used=True,
        provider=getattr(provider, "name", "search"),
        query=f"site:{domain} | {brand}",
        business_name=business_name,
        description=description,
        social_links=social_links[:8],
        sources=sources[:10],
        note=(None if sources else "search returned no results"),
    )
