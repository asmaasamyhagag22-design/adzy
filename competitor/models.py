"""Data models for competitor discovery and peer matching.

These types are deliberately decoupled from your `BusinessProfile`. The matching
engine operates only on `MatchCriteria` (a flat, explicit input) and `Candidate`
(what Places gives us). The single adapter that reads your real BusinessProfile
lives in `discovery.build_match_criteria` — that is the ONLY place that touches
your schema, so the engine stays pure and testable.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional


# ---------------------------------------------------------------------------
# Input to the matcher (built from your BusinessProfile by the adapter)
# ---------------------------------------------------------------------------

@dataclass
class MatchCriteria:
    """The business we are finding peers for, reduced to match signals.

    Everything here is what we *know about the subject business*. The matcher
    compares each Places candidate against these values.
    """

    # --- Identity / vertical ---
    category: str                       # your internal category label (for logs)
    place_types: List[str]              # Google Places type strings to search & filter on
    offering_keywords: List[str]        # normalized terms from offerings + category, for name overlap

    # --- Audience / tier (soft) ---
    audience_tier: Optional[str] = None         # "budget" | "mid" | "premium" | None
    audience_confidence: float = 0.0            # 0..1; below threshold -> tier dimension is dropped

    # --- Language (tie-breaker) ---
    languages: List[str] = field(default_factory=list)   # e.g. ["ar"] or ["ar", "en"]

    # --- Location ---
    lat: Optional[float] = None
    lng: Optional[float] = None
    address: Optional[str] = None       # raw address string; orchestrator geocodes if lat/lng missing
    is_online_only: bool = False        # True -> use text search, proximity weight redistributed

    # --- Size reference (from Places on the subject, may be None) ---
    review_count_self: Optional[int] = None


# ---------------------------------------------------------------------------
# What Places returns per candidate
# ---------------------------------------------------------------------------

@dataclass
class Candidate:
    place_id: str
    name: str
    primary_type: Optional[str] = None
    types: List[str] = field(default_factory=list)
    lat: Optional[float] = None
    lng: Optional[float] = None
    distance_m: Optional[float] = None          # filled in by the orchestrator (haversine)
    rating: Optional[float] = None
    review_count: Optional[int] = None
    website: Optional[str] = None
    business_status: Optional[str] = None       # e.g. "OPERATIONAL", "CLOSED_PERMANENTLY"
    price_level: Optional[str] = None           # e.g. "PRICE_LEVEL_MODERATE"
    formatted_address: Optional[str] = None
    maps_uri: Optional[str] = None


@dataclass
class Review:
    rating: Optional[float]
    text: str
    author: Optional[str] = None
    relative_time: Optional[str] = None         # e.g. "a month ago"
    language_code: Optional[str] = None


# ---------------------------------------------------------------------------
# Scoring / selection output
# ---------------------------------------------------------------------------

@dataclass
class PeerFitBreakdown:
    """Per-dimension sub-scores (0..1) and the weights actually applied.

    A dimension whose sub-score is None was *unavailable* for this candidate
    (e.g. proximity for an online-only business, or audience tier when Places
    returned no priceLevel). Unavailable dimensions are dropped and their weight
    is redistributed across the available ones — `weights_used` records that.
    """

    sub_vertical: Optional[float] = None
    proximity: Optional[float] = None
    size_similarity: Optional[float] = None
    audience_tier: Optional[float] = None
    language: Optional[float] = None
    total: float = 0.0
    weights_used: Dict[str, float] = field(default_factory=dict)
    # HOW `total` was derived (P-80). "weighted" = the real multi-dimension fit (Places peers, via
    # peer_match._aggregate). "search_rank" = a WEB peer with no Places data, so total is just
    # 1/SERP-rank — NOT a measured competitive fit. Downstream must not present a search_rank score
    # as a validated fit (that was exactly the P-30 artifact: fit=1.0 with an all-null breakdown).
    basis: str = "weighted"


@dataclass
class SelectionRecord:
    """The audit trail for *why this competitor was chosen*. This is what you
    show in the defense when asked 'why these four'."""

    place_id: str
    name: str
    website: Optional[str]
    peer_fit_score: float
    breakdown: PeerFitBreakdown
    why_selected: str


@dataclass
class CompetitorProfile:
    """A selected competitor: the candidate, why it was picked, and its reviews."""

    candidate: Candidate
    selection: SelectionRecord
    reviews: List[Review] = field(default_factory=list)
    # True if the website is a real, scrapable domain (not Facebook/Instagram/etc.).
    # The matrix scrapes only these (the "benchmark" role).
    has_scrapable_site: bool = False
    # True if this peer is in the primary local tier (top-fit, drives review themes).
    # A peer can be both local and scrapable. Added benchmarks have is_local=False.
    is_local: bool = True


@dataclass
class PeerMatchResult:
    competitors: List[CompetitorProfile]
    thin_peer_set: bool                 # True if fewer than target peers cleared the floor
    candidates_considered: int
    candidates_passed_filters: int
    notes: List[str] = field(default_factory=list)
    # per-candidate hard-filter verdicts (for explaining a thin result):
    # each: {name, type, reviews, website, dist_km, passed, reason}
    audit: List[Dict] = field(default_factory=list)