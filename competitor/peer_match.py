"""Peer-match engine.

Two stages, exactly as designed:

  Stage 1 - HARD FILTERS (pass/fail). A candidate that fails any of these is not
            a competitor at all and never reaches scoring.

  Stage 2 - PEER-FIT SCORE (0..1). Survivors are ranked by a weighted score over
            five dimensions. The weights mirror the agreed rubric:

                sub_vertical    0.35
                proximity       0.20
                size_similarity 0.20
                audience_tier   0.15
                language        0.10

Honesty note about the scoring *stage*:
    This runs BEFORE we scrape the competitors' websites, so we only have Places
    signals. `sub_vertical` is therefore approximated from (Places type match) +
    (name-keyword overlap with the subject's offerings). `audience_tier` uses the
    Places `priceLevel`. Both can be RE-RUN after you scrape the chosen
    competitors, feeding richer offering-overlap and tier signals into the same
    function. Dimensions we genuinely cannot assess for a candidate are dropped
    and their weight redistributed (see models.PeerFitBreakdown).

The engine is pure: no network, no I/O. Easy to unit-test.
"""

from __future__ import annotations

import math
import re
import unicodedata
from typing import Dict, List, Optional, Tuple

from .models import Candidate, MatchCriteria, PeerFitBreakdown, SelectionRecord


# ---------------------------------------------------------------------------
# Tunables (kept together so they are easy to defend / adjust)
# ---------------------------------------------------------------------------

BASE_WEIGHTS: Dict[str, float] = {
    "sub_vertical": 0.35,
    "proximity": 0.20,
    "size_similarity": 0.20,
    "audience_tier": 0.15,
    "language": 0.10,
}

TARGET_PEERS = 4
PEER_FIT_FLOOR = 0.45                 # below this => not counted as a "real" competitor
MIN_REVIEWS_FOR_CANDIDATE = 5         # too few => themes are weak (Egyptian small-clinic reality)
SIZE_NEUTRAL = 0.5                    # used when size can't be compared

AUDIENCE_CONFIDENCE_THRESHOLD = 0.55  # below this, the subject's tier is too soft to filter on

CLOSED_STATUSES = {"CLOSED_PERMANENTLY", "CLOSED_TEMPORARILY"}

# Google priceLevel enum -> coarse tier
_PRICE_TO_TIER = {
    "PRICE_LEVEL_FREE": "budget",
    "PRICE_LEVEL_INEXPENSIVE": "budget",
    "PRICE_LEVEL_MODERATE": "mid",
    "PRICE_LEVEL_EXPENSIVE": "premium",
    "PRICE_LEVEL_VERY_EXPENSIVE": "premium",
}
_TIER_ORDER = {"budget": 0, "mid": 1, "premium": 2}

_ARABIC_RANGE = re.compile(r"[\u0600-\u06FF]")
_STOPWORDS = {
    "the", "and", "for", "of", "in", "at", "clinic", "center", "centre", "store",
    "shop", "co", "company", "egypt", "cairo", "ال", "و", "في",
}


# ---------------------------------------------------------------------------
# Text normalization (Arabic-aware, matches the project's existing approach)
# ---------------------------------------------------------------------------

def normalize_text(s: str) -> str:
    """Lowercase, strip diacritics, unify Arabic alef/ya/ta-marbuta variants."""
    if not s:
        return ""
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = s.lower()
    # Arabic letter normalization
    s = s.replace("أ", "ا").replace("إ", "ا").replace("آ", "ا")
    s = s.replace("ى", "ي").replace("ة", "ه")
    s = re.sub(r"[^0-9a-z\u0600-\u06FF\s]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def tokenize(s: str) -> List[str]:
    toks = normalize_text(s).split()
    return [t for t in toks if t and t not in _STOPWORDS and len(t) > 1]


# English -> Arabic equivalents for the beauty / clinic domain. Without this, the
# sub_vertical name-overlap fails when offerings are in English ("laser", "skin")
# but the competitor's name is in Arabic ("ليزر", "جلدية") — the common Egyptian
# case (it under-ranked "معادى ليزر كلينيك" to below the floor in testing).
# Generic words ("clinic") are intentionally omitted: they match everything.
_BILINGUAL_RAW = {
    "laser": ["ليزر"],
    "skin": ["بشرة", "جلدية", "جلد"],
    "derma": ["جلدية"],
    "dermatology": ["جلدية"],
    "dermatologist": ["جلدية"],
    "hair": ["شعر"],
    "beauty": ["تجميل", "جمال"],
    "aesthetic": ["تجميل"],
    "cosmetic": ["تجميل"],
    "cosmetics": ["تجميل"],
    "cosmetology": ["تجميل"],
    "care": ["عناية"],
    "dental": ["أسنان"],
    "teeth": ["أسنان"],
    "spa": ["سبا"],
}
_BILINGUAL = {k: [normalize_text(x) for x in v] for k, v in _BILINGUAL_RAW.items()}


def expand_bilingual(tokens: List[str]) -> List[str]:
    """Add Arabic equivalents for known English terms, keeping the originals."""
    out = list(tokens)
    for t in tokens:
        out.extend(_BILINGUAL.get(t, []))
    return list(dict.fromkeys(out))


# ---------------------------------------------------------------------------
# Hard filters (Stage 1)
# ---------------------------------------------------------------------------

def passes_hard_filters(
    cand: Candidate,
    crit: MatchCriteria,
    *,
    require_website: bool = True,
    min_reviews: int = MIN_REVIEWS_FOR_CANDIDATE,
) -> Tuple[bool, Optional[str]]:
    """Return (passed, reason_if_failed). The first failed gate wins."""

    # 1. Same category (Places type must intersect the subject's types)
    cand_types = set(cand.types or [])
    if cand.primary_type:
        cand_types.add(cand.primary_type)
    if crit.place_types and not (cand_types & set(crit.place_types)):
        return False, "category_mismatch"

    # 2. Operational
    if cand.business_status and cand.business_status in CLOSED_STATUSES:
        return False, "not_operational"

    # 3. Enough reviews to be worth theming
    if cand.review_count is None or cand.review_count < min_reviews:
        return False, "too_few_reviews"

    # 4. Has a website (so the scraper can build a profile for the matrix).
    #    Relax with require_website=False if you want review-only competitors.
    if require_website and not (cand.website and cand.website.strip()):
        return False, "no_website"

    return True, None


# ---------------------------------------------------------------------------
# Per-dimension sub-scores (each returns 0..1, or None if unavailable)
# ---------------------------------------------------------------------------

def score_sub_vertical(cand: Candidate, crit: MatchCriteria) -> float:
    """0.6 * type-specificity match + 0.4 * name-keyword overlap with offerings.

    Pre-scrape proxy for 'same sub-vertical'. Refine post-scrape by feeding the
    competitor's scraped offerings into offering_keywords-style overlap.
    """
    # type component
    if cand.primary_type and cand.primary_type in crit.place_types:
        type_score = 1.0
    elif set(cand.types or []) & set(crit.place_types):
        type_score = 0.6
    else:
        type_score = 0.0

    # name component
    kw = {t for t in (crit.offering_keywords or [])}
    name_score = 0.0
    if kw:
        name_tokens = set(tokenize(cand.name))
        hits = name_tokens & kw
        denom = min(len(kw), 3)  # don't punish for huge keyword lists
        name_score = min(1.0, len(hits) / denom) if denom else 0.0

    return round(0.6 * type_score + 0.4 * name_score, 4)


def score_proximity(cand: Candidate, crit: MatchCriteria,
                    d_min: float, d_max: float) -> Optional[float]:
    """Linear, normalized within the returned pool. None when online-only."""
    if crit.is_online_only:
        return None
    if cand.distance_m is None:
        return None
    if d_max <= d_min:
        return 1.0
    return round(1.0 - (cand.distance_m - d_min) / (d_max - d_min), 4)


# A median needs a real distribution behind it. Below this many survivors the "pool median" is
# just one of the candidates being compared to itself (MEASURED: a pool of 1 scored a free 1.0).
_MIN_POOL_FOR_MEDIAN = 3


def score_size_similarity(cand: Candidate, crit: MatchCriteria,
                         pool_median_reviews: Optional[float],
                         pool_size: Optional[int] = None) -> Optional[float]:
    """Log-ratio similarity of review counts. 1.0 when equal, decays with
    orders-of-magnitude divergence. Compares to the subject's own count, or to
    the pool median when the subject has no/too-few reviews.

    Returns None (dropped, weight redistributed) when the median is the ONLY reference and the
    pool is too small for a median to mean anything. MEASURED (Volume Qatar): with one survivor
    the median IS that survivor's own review count, so it scored a free 1.0 and a catering company
    cleared the café peer floor at 0.73. `crit.review_count_self` reads a field absent from
    `BusinessProfile`, so the median path is the only one that ever runs — making the
    self-reference the norm, not an edge case. A number derived from nothing must vanish rather
    than turn into confidence.
    """
    own = crit.review_count_self if (crit.review_count_self and crit.review_count_self > 0) else None
    if own is None and pool_size is not None and pool_size < _MIN_POOL_FOR_MEDIAN:
        return None
    ref = own if own is not None else pool_median_reviews
    if not ref or ref <= 0 or not cand.review_count or cand.review_count <= 0:
        return SIZE_NEUTRAL
    r = cand.review_count / ref
    return round(1.0 / (1.0 + abs(math.log(r))), 4)


def score_audience_tier(cand: Candidate, crit: MatchCriteria) -> Optional[float]:
    """Map Places priceLevel -> tier and compare to the subject's tier.

    Returns None (=> dropped, weight redistributed) when the subject's tier is
    too low-confidence to filter on, or when Places gave no priceLevel.
    """
    if not crit.audience_tier or crit.audience_confidence < AUDIENCE_CONFIDENCE_THRESHOLD:
        return None
    cand_tier = _PRICE_TO_TIER.get(cand.price_level or "")
    if cand_tier is None:
        return None
    a, b = _TIER_ORDER.get(crit.audience_tier), _TIER_ORDER.get(cand_tier)
    if a is None or b is None:
        return None
    gap = abs(a - b)
    return {0: 1.0, 1: 0.5}.get(gap, 0.0)


def score_language(cand: Candidate, crit: MatchCriteria,
                  review_langs: Optional[List[str]] = None) -> Optional[float]:
    """Weak tie-breaker from script of the name (+ review languages if known).

    Returns None when we can't tell (so it is dropped rather than guessed).
    """
    if not crit.languages:
        return None
    subject_ar = "ar" in crit.languages
    subject_bilingual = subject_ar and ("en" in crit.languages)

    name_has_ar = bool(_ARABIC_RANGE.search(cand.name or ""))
    langs = set(l.split("-")[0] for l in (review_langs or []) if l)
    cand_ar = name_has_ar or ("ar" in langs)
    cand_en = (not name_has_ar) or ("en" in langs)

    if subject_bilingual:
        return 1.0 if (cand_ar or cand_en) else None
    if subject_ar:
        return 1.0 if cand_ar else 0.5
    # subject English-only
    return 1.0 if cand_en else 0.5


# ---------------------------------------------------------------------------
# Weighted aggregation with redistribution
# ---------------------------------------------------------------------------

# TIER-MISMATCH CAP (owner rule 2026-07-12: "a local single-branch burger joint must NOT
# surface McDonald's — tier mismatch outranks category match"). A SEVERE size or price-band
# mismatch is DECISIVE: it caps the total below PEER_FIT_FLOOR so a strong category (sub_vertical)
# score can't carry a wrong-tier business in. Transparent, not a magic weight — it only fires on
# an extreme mismatch (≈2 orders of magnitude of reviews, or the opposite price tier), so genuine
# near-tier peers (what the U1 gate selects) are untouched.
_SIZE_MISMATCH_SEVERE = 0.20     # log-ratio size score below this ≈ 100x review-count gap
_TIER_CAP = 0.44                 # just under PEER_FIT_FLOOR (0.45) -> "not a real competitor"


def _aggregate(subscores: Dict[str, Optional[float]]) -> PeerFitBreakdown:
    """Drop None dimensions, renormalize remaining weights to sum 1, weight-sum. Then apply
    the decisive tier-mismatch cap (size/price band outrank category)."""
    available = {k: v for k, v in subscores.items() if v is not None}
    weight_sum = sum(BASE_WEIGHTS[k] for k in available) or 1.0
    weights_used = {k: round(BASE_WEIGHTS[k] / weight_sum, 4) for k in available}
    total = sum(weights_used[k] * available[k] for k in available)
    size = subscores.get("size_similarity")
    tier = subscores.get("audience_tier")
    severe_size = isinstance(size, (int, float)) and size < _SIZE_MISMATCH_SEVERE
    opposite_tier = isinstance(tier, (int, float)) and tier <= 0.0
    if (severe_size or opposite_tier) and total > _TIER_CAP:
        total = _TIER_CAP        # decisive: wrong tier -> below the real-competitor floor
    return PeerFitBreakdown(
        sub_vertical=subscores.get("sub_vertical"),
        proximity=subscores.get("proximity"),
        size_similarity=subscores.get("size_similarity"),
        audience_tier=subscores.get("audience_tier"),
        language=subscores.get("language"),
        total=round(total, 4),
        weights_used=weights_used,
    )


def score_candidate(cand: Candidate, crit: MatchCriteria, *,
                   d_min: float, d_max: float,
                   pool_median_reviews: Optional[float],
                   pool_size: Optional[int] = None,
                   review_langs: Optional[List[str]] = None) -> PeerFitBreakdown:
    subs: Dict[str, Optional[float]] = {
        "sub_vertical": score_sub_vertical(cand, crit),
        "proximity": score_proximity(cand, crit, d_min, d_max),
        "size_similarity": score_size_similarity(cand, crit, pool_median_reviews,
                                                 pool_size=pool_size),
        "audience_tier": score_audience_tier(cand, crit),
        "language": score_language(cand, crit, review_langs),
    }
    return _aggregate(subs)


# ---------------------------------------------------------------------------
# Dedup, selection, audit string
# ---------------------------------------------------------------------------

def _domain(url: Optional[str]) -> Optional[str]:
    if not url:
        return None
    m = re.search(r"https?://([^/]+)/?", url)
    host = (m.group(1) if m else url).lower()
    host = host[4:] if host.startswith("www.") else host
    return host or None


def _brand_key(cand: Candidate) -> str:
    """Collapse multiple branches of the same brand: prefer domain, else name root."""
    d = _domain(cand.website)
    if d:
        return d
    toks = tokenize(cand.name)
    return " ".join(toks[:2]) if toks else (cand.name or cand.place_id).lower()


def _why(cand: Candidate, crit: MatchCriteria, bd: PeerFitBreakdown) -> str:
    parts = []
    if cand.primary_type:
        parts.append(f"type={cand.primary_type}")
    tier = _PRICE_TO_TIER.get(cand.price_level or "")
    if tier:
        parts.append(f"tier={tier}")
    if cand.review_count is not None:
        ref = crit.review_count_self
        if ref and ref > 0:
            parts.append(f"reviews={cand.review_count} ({cand.review_count / ref:.1f}x yours)")
        else:
            parts.append(f"reviews={cand.review_count}")
    if cand.distance_m is not None and not crit.is_online_only:
        parts.append(f"dist={cand.distance_m / 1000:.1f}km")
    parts.append(f"fit={bd.total:.2f}")
    return " · ".join(parts)


def select_peers(
    candidates: List[Candidate],
    crit: MatchCriteria,
    *,
    floor: float = PEER_FIT_FLOOR,
    require_website: bool = False,
    review_langs_by_place: Optional[Dict[str, List[str]]] = None,
) -> Tuple[List[Tuple[Candidate, SelectionRecord]], Dict[str, int]]:
    """Run Stage-1 + Stage-2 and return ALL peers above the floor, ranked by fit
    (deduped by brand). The caller decides how to tier / truncate them.

    Returns (ranked_above_floor, stats).
    """
    review_langs_by_place = review_langs_by_place or {}
    considered = len(candidates)

    # Stage 1
    survivors = [c for c in candidates
                 if passes_hard_filters(c, crit, require_website=require_website)[0]]
    passed = len(survivors)

    # pool stats for normalization
    dists = [c.distance_m for c in survivors if c.distance_m is not None]
    d_min, d_max = (min(dists), max(dists)) if dists else (0.0, 0.0)
    rc = sorted(c.review_count for c in survivors if c.review_count)
    pool_median = (rc[len(rc) // 2] if rc else None)

    # Stage 2
    scored: List[Tuple[Candidate, SelectionRecord]] = []
    for c in survivors:
        bd = score_candidate(
            c, crit, d_min=d_min, d_max=d_max,
            pool_median_reviews=pool_median, pool_size=len(survivors),
            review_langs=review_langs_by_place.get(c.place_id),
        )
        rec = SelectionRecord(
            place_id=c.place_id, name=c.name, website=c.website,
            peer_fit_score=bd.total, breakdown=bd, why_selected=_why(c, crit, bd),
        )
        scored.append((c, rec))

    # dedup by brand, keep best per brand
    best_by_brand: Dict[str, Tuple[Candidate, SelectionRecord]] = {}
    for c, rec in scored:
        k = _brand_key(c)
        if k not in best_by_brand or rec.peer_fit_score > best_by_brand[k][1].peer_fit_score:
            best_by_brand[k] = (c, rec)

    ranked = sorted(best_by_brand.values(), key=lambda t: t[1].peer_fit_score, reverse=True)
    above_floor = [t for t in ranked if t[1].peer_fit_score >= floor]

    stats = {
        "considered": considered,
        "passed_filters": passed,
        "above_floor": len(above_floor),
    }
    return above_floor, stats