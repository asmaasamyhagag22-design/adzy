# -*- coding: utf-8 -*-
"""A competitor the OWNER named, turned into a peer the rest of the pipeline can use.

Automatic discovery is still being improved, and a run that finds no peers cannot produce a SWOT at
all — "strengths and weaknesses are only such in comparison to the competition" (Pickton & Wright
1998, p.103). The rebuilt SWOT says so and points the client here: naming one competitor converts a
page of site-readiness readings into a real strategic analysis.

TWO PROPERTIES THIS MODULE EXISTS TO GUARANTEE:

  * PROVENANCE. A competitor the user typed is a different kind of evidence from one the system
    found, ranked and scored. `PeerFitBreakdown.basis` already exists for exactly this distinction
    (it carries "weighted" for a measured Places fit and "search_rank" for a SERP peer), so a typed
    peer carries `basis="user_provided"` and nothing downstream can mistake it for a measured fit.

  * NO INVENTED SCORES. A discovered peer earns a fit score against five sub-dimensions. A typed
    name has been scored against nothing, so every sub-dimension stays None and the total stays 0.
    Emitting a confident 1.0 here would put an unearned number into the client's evidence trail —
    the P-30 artifact this codebase already fixed once on the web-discovery path.

Structure mirrors `web_discovery.SerpWebDiscoveryEngine._to_peer`, which solved the same problem
(build a CompetitorProfile with no Places data). Universal: no vertical, country or language.
"""
from __future__ import annotations

from typing import Optional
from urllib.parse import urlparse

from .models import Candidate, CompetitorProfile, PeerFitBreakdown, SelectionRecord

_WHY = ("added by the owner — not discovered or scored by the system; "
        "included because they named it as a competitor")


def _homepage(url: str) -> str:
    """Benchmark the HOMEPAGE. The matrix scrapes this URL, and a deep link (a blog post, a product
    page) is not representative of the competitor's site — same reasoning as the web path."""
    parsed = urlparse(str(url or "").strip())
    if not parsed.netloc:
        return ""
    return f"{parsed.scheme or 'https'}://{parsed.netloc}/"


def competitor_from_user(name: str, website: str = "") -> Optional[CompetitorProfile]:
    """Build a peer from what the owner typed. Returns None if it is unusable.

    A competitor with no website is still worth recording — the owner knows who they compete with
    even when that rival has no site — but it cannot be scraped, so `has_scrapable_site` is False
    and the comparison matrix will simply have no cells for it (which it already handles: UNKNOWN
    cells are skipped, never guessed)."""
    name = str(name or "").strip()
    if not name:
        return None

    home = ""
    if str(website or "").strip():
        # A competitor URL is a URL this system will fetch, so it passes the same SSRF gate as
        # every other fetch target (scraper.url_utils.is_safe_public_url, used by /api/scrape).
        from scraper.url_utils import is_safe_public_url
        candidate_url = str(website).strip()
        scheme = urlparse(candidate_url).scheme.lower()
        if scheme and scheme not in ("http", "https"):
            # CAUGHT BY THIS MODULE'S OWN TEST: the first version prepended "https://" to anything
            # lacking an http prefix, which turned `file:///etc/passwd` into a string that slipped
            # past the gate. A non-web scheme is a refusal, never something to paper over.
            return None
        if not scheme:
            candidate_url = "https://" + candidate_url
        if not is_safe_public_url(candidate_url):
            return None
        home = _homepage(candidate_url)
        if not home:
            return None

    candidate = Candidate(place_id="", name=name, website=home or None,
                          formatted_address=None)
    selection = SelectionRecord(
        place_id="", name=name, website=home or None,
        peer_fit_score=0.0,
        # Every sub-dimension stays None: nothing was measured, so nothing is claimed.
        breakdown=PeerFitBreakdown(total=0.0, basis="user_provided"),
        why_selected=_WHY,
    )
    return CompetitorProfile(
        candidate=candidate,
        selection=selection,
        reviews=[],
        has_scrapable_site=bool(home),
        is_local=False,
    )
