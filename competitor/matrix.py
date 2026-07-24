"""Comparative Gap Matrix.

Builds an evidence-grounded comparison of the subject business against its peers
across a fixed set of dimensions, then computes a per-dimension "gap" verdict
(ahead / behind / par / whitespace). The SWOT (swot.py) is derived mechanically
from these gaps, so every SWOT point traces back to a real, sourced cell.

Two kinds of dimension:
  - SCRAPED  : read from a business's website. Available for the subject (always)
               and for competitors only if they have a scrapable site AND a
               scrape function is supplied. Otherwise the cell is UNKNOWN.
  - PLACES   : from the Places listing (rating / review count / price level).
               Available for every competitor; for the subject only if you pass
               its own Places data.

UNKNOWN cells never count in a gap verdict — we never infer a value we don't have.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Any

from .models import CompetitorProfile


# ===========================================================================
# ADAPTER  ->  reads a scraped source into the matrix's dimension values.
# Handles three input shapes so the SAME function works for the subject and for
# competitors regardless of how far each was processed:
#   (1) your scraper's MANIFEST  (raw scrape: contact / links / languages / ...)
#   (2) a BusinessProfile        (post-LLM: offerings / existing_ctas / ...)
#   (3) an already-built dims dict (pass-through)
# Each value is the dimension's value, or None if UNKNOWN. Adjust the field names
# inside the two readers below to match your real schemas.
# ===========================================================================

_DIM_KEYS = [
    "online_booking", "whatsapp", "shows_reviews", "cta_count",
    "offerings_count", "bilingual", "trust_count", "social_count",
]


def extract_scraped_dimensions(obj) -> Dict[str, Any]:
    if obj is None:
        return {}
    # (3) already a dims dict?
    if isinstance(obj, dict) and any(k in obj for k in _DIM_KEYS):
        return {k: obj.get(k) for k in _DIM_KEYS}
    # (1) a scraper manifest? (has nested contact + links)
    if _looks_like_manifest(obj):
        return dimensions_from_manifest(obj)
    # (2) otherwise treat as a BusinessProfile-like (flat fields)
    return _dims_from_business_profile(obj)


def _looks_like_manifest(obj) -> bool:
    return hasattr(obj, "contact") and hasattr(obj, "links")


# --- (1) reader for YOUR scraper's manifest -------------------------------
# Attribute paths confirmed from scraper/__main__.py:
#   manifest.contact.whatsapp / .phones / .emails   (lists)
#   manifest.links.social / .cta_candidates          (lists)
#   manifest.languages -> entries with .code, .proportion
#   manifest.pages -> entries with .forms
# offerings / trust_signals are NOT in the manifest (LLM stage) -> None.
def dimensions_from_manifest(manifest) -> Dict[str, Any]:
    contact = getattr(manifest, "contact", None)
    links = getattr(manifest, "links", None)
    langs = getattr(manifest, "languages", None) or []

    whatsapp = (getattr(contact, "whatsapp", None) or []) if contact else []
    social = _dedup_by_href((getattr(links, "social", None) or []) if links else [])
    ctas = _dedup_by_href((getattr(links, "cta_candidates", None) or []) if links else [])
    lang_codes = {str(getattr(e, "code", "")).lower()[:2] for e in langs}

    return {
        "online_booking": _cta_has_booking(ctas),     # bool (we have CTA data, so absence is real)
        "whatsapp": len(whatsapp) > 0,
        "shows_reviews": None,                         # not detected at manifest stage
        "cta_count": len(ctas),
        "offerings_count": None,                       # LLM stage only
        "bilingual": ("ar" in lang_codes and "en" in lang_codes) if lang_codes else None,
        "trust_count": None,                           # LLM stage only
        "social_count": len(social),
    }


def _dedup_by_href(items):
    """Collapse link records pointing to the same href (first occurrence wins).

    The crawl emits one LinkRecord per (page, link), so an href that lives in a
    shared header/footer is counted once per page — inflating cta_count /
    social_count (measured: cta_candidates 14 -> 2 unique, social 7 -> 1 unique).
    Dedup by NORMALIZED href (strip trailing slash) before counting. Items we
    can't key (no href / empty) are kept as-is so we never drop real evidence.
    """
    if not items:
        return items
    seen = set()
    out = []
    for it in items:
        href = it if isinstance(it, str) else getattr(it, "href", None)
        if not href:
            out.append(it)
            continue
        key = str(href).strip().rstrip("/")
        if key in seen:
            continue
        seen.add(key)
        out.append(it)
    return out


def _cta_has_booking(ctas) -> bool:
    """ctas are LinkRecord objects: .anchor_text + .href (per scraper/schemas.py)."""
    if not ctas:
        return False
    blob = []
    for c in ctas:
        if isinstance(c, str):
            blob.append(c)
            continue
        # primary fields on LinkRecord, with fallbacks in case of schema drift
        for attr in ("anchor_text", "href", "text", "label", "title", "url"):
            v = getattr(c, attr, None)
            if v:
                blob.append(str(v))
    s = " ".join(blob).lower()
    return any(w in s for w in _BOOKING_WORDS)


# --- (2) reader for a BusinessProfile -------------------------------------
def _dims_from_business_profile(profile) -> Dict[str, Any]:
    offerings = _get(profile, "offerings", []) or []
    ctas = _get(profile, "existing_ctas", []) or _get(profile, "ctas", []) or []
    languages = _get(profile, "languages", []) or []
    trust = _get(profile, "trust_signals", []) or []

    # WhatsApp lives under contact_channels.whatsapp_numbers; the old top-level
    # `whatsapp_numbers`/`whatsapp` reads never existed (kept as fallbacks only).
    contact = _get(profile, "contact_channels", None) or {}
    whatsapp = _get(contact, "whatsapp_numbers", None)
    if whatsapp is None:
        whatsapp = _get(profile, "whatsapp_numbers", None) or _get(profile, "whatsapp", None)
    # The real BusinessProfile field is `social_presence` (list of SocialAccount).
    # The old `social_links`/`social_profiles` names never existed, so social_count
    # was always 0 -> a false "Social links: none detected" weakness in the SWOT.
    social = (
        _get(profile, "social_presence", None)
        or _get(profile, "social_links", None)
        or _get(profile, "social_profiles", None)
        or []
    )

    booking = _get(profile, "has_online_booking", None)
    if booking is None:
        booking = _looks_like_booking(ctas) or _looks_like_booking(
            [_get(profile, "booking_url", "")]
        )

    shows_reviews = _get(profile, "shows_reviews_on_site", None)

    return {
        "online_booking": _as_bool(booking),
        "whatsapp": _truthy_list(whatsapp),
        "shows_reviews": _as_bool(shows_reviews) if shows_reviews is not None else None,
        "cta_count": len(ctas) if ctas else (0 if ctas == [] else None),
        "offerings_count": len(offerings) if offerings is not None else None,
        "bilingual": _is_bilingual(languages) if languages else None,
        "trust_count": len(trust) if trust is not None else None,
        "social_count": len(social) if social is not None else None,
    }


def _get(obj, name, default=None):
    val = getattr(obj, name, None)
    if val is None and isinstance(obj, dict):
        val = obj.get(name)
    if val is None:
        return default
    inner = getattr(val, "value", None)
    return inner if inner is not None else val


_BOOKING_WORDS = ("book", "appointment", "احجز", "حجز", "موعد", "reserve", "booking")


def _looks_like_booking(items) -> Optional[bool]:
    if not items:
        return None
    blob = " ".join(str(x) for x in items if x).lower()
    if not blob.strip():
        return None
    return any(w in blob for w in _BOOKING_WORDS)


def _as_bool(v):
    if v is None:
        return None
    return bool(v)


def _truthy_list(v):
    if v is None:
        return None
    if isinstance(v, (list, tuple, set)):
        return len(v) > 0
    return bool(v)


def _is_bilingual(langs) -> bool:
    s = {str(l).lower()[:2] for l in langs}
    return "ar" in s and "en" in s


# ---------------------------------------------------------------------------
# Dimension registry
# ---------------------------------------------------------------------------

@dataclass
class Dimension:
    key: str
    label: str
    source: str            # "scraped" | "places"
    kind: str              # "bool" | "count" | "numeric" | "categorical"
    direction: str         # "higher_better" | "lower_better" | "info"


DIMENSIONS: List[Dimension] = [
    # scraped (website)
    Dimension("online_booking", "Online booking", "scraped", "bool", "higher_better"),
    Dimension("whatsapp", "WhatsApp contact", "scraped", "bool", "higher_better"),
    Dimension("shows_reviews", "Shows reviews on site", "scraped", "bool", "higher_better"),
    Dimension("cta_count", "Number of CTAs", "scraped", "count", "higher_better"),
    Dimension("offerings_count", "Breadth of offerings", "scraped", "count", "higher_better"),
    Dimension("bilingual", "Bilingual (AR + EN)", "scraped", "bool", "higher_better"),
    Dimension("trust_count", "Trust signals", "scraped", "count", "higher_better"),
    Dimension("social_count", "Social links", "scraped", "count", "higher_better"),
    # places (always available for competitors)
    Dimension("rating", "Google rating", "places", "numeric", "higher_better"),
    Dimension("review_count", "Review volume", "places", "numeric", "higher_better"),
    Dimension("price_tier", "Price tier", "places", "categorical", "info"),
]

_PRICE_TO_TIER = {
    "PRICE_LEVEL_FREE": "budget", "PRICE_LEVEL_INEXPENSIVE": "budget",
    "PRICE_LEVEL_MODERATE": "mid",
    "PRICE_LEVEL_EXPENSIVE": "premium", "PRICE_LEVEL_VERY_EXPENSIVE": "premium",
}


def _places_dimensions(cand) -> Dict[str, Any]:
    return {
        "rating": cand.rating,
        "review_count": cand.review_count,
        "price_tier": _PRICE_TO_TIER.get(cand.price_level or ""),
    }


# ---------------------------------------------------------------------------
# Matrix data structures
# ---------------------------------------------------------------------------

@dataclass
class MatrixColumn:
    name: str
    is_subject: bool
    is_local: bool = False
    has_scrapable_site: bool = False
    values: Dict[str, Any] = field(default_factory=dict)   # dim_key -> value or None


@dataclass
class DimensionGap:
    dimension: Dimension
    subject_value: Any
    competitor_values: Dict[str, Any]      # column name -> value (known only)
    verdict: str                           # "ahead" | "behind" | "par" | "whitespace" | "n/a"
    detail: str                            # human-readable, citation-ready


@dataclass
class ComparativeGapMatrix:
    columns: List[MatrixColumn]            # subject first, then competitors
    gaps: List[DimensionGap]
    notes: List[str] = field(default_factory=list)

    @property
    def subject(self) -> MatrixColumn:
        return self.columns[0]

    @property
    def competitors(self) -> List[MatrixColumn]:
        return self.columns[1:]


# ---------------------------------------------------------------------------
# Builder
# ---------------------------------------------------------------------------

def build_matrix(
    subject_profile,
    competitors: List[CompetitorProfile],
    *,
    scrape_fn: Optional[Callable[[str], Any]] = None,
    subject_name: str = "You",
    subject_places=None,
) -> ComparativeGapMatrix:
    """Assemble the matrix.

    scrape_fn(url) -> a BusinessProfile-like object (your scraper). Called only
    for competitors with a scrapable site. If None, competitor scraped cells are
    UNKNOWN and the comparison leans on Places dimensions.
    """
    notes: List[str] = []

    # subject column
    subj_vals = extract_scraped_dimensions(subject_profile)
    if subject_places is not None:
        subj_vals.update(_places_dimensions(subject_places))
    subject_col = MatrixColumn(name=subject_name, is_subject=True, values=subj_vals)

    # competitor columns
    comp_cols: List[MatrixColumn] = []
    scraped_ok = 0
    for comp in competitors:
        vals: Dict[str, Any] = {}
        if scrape_fn and comp.has_scrapable_site and comp.candidate.website:
            try:
                cp = scrape_fn(comp.candidate.website)
                if cp is not None:
                    vals.update(extract_scraped_dimensions(cp))
                    scraped_ok += 1
            except Exception as e:               # never let one bad scrape kill the matrix
                notes.append(f"scrape failed for {comp.candidate.name}: {type(e).__name__}")
        vals.update(_places_dimensions(comp.candidate))   # Places always available
        comp_cols.append(MatrixColumn(
            name=comp.candidate.name,
            is_subject=False,
            is_local=comp.is_local,
            has_scrapable_site=comp.has_scrapable_site,
            values=vals,
        ))

    if scrape_fn:
        notes.append(f"scraped {scraped_ok} competitor site(s) for scraped-dimension comparison")
    else:
        notes.append("no scrape_fn supplied; competitor scraped dimensions are UNKNOWN "
                     "(comparison uses Places dimensions only)")

    columns = [subject_col] + comp_cols
    gaps = [_gap_for(dim, columns) for dim in DIMENSIONS]
    return ComparativeGapMatrix(columns=columns, gaps=gaps, notes=notes)


# ---------------------------------------------------------------------------
# Per-dimension gap analysis
# ---------------------------------------------------------------------------

def _gap_for(dim: Dimension, columns: List[MatrixColumn]) -> DimensionGap:
    subject = columns[0]
    comps = columns[1:]
    s_val = subject.values.get(dim.key)
    comp_vals = {c.name: c.values.get(dim.key) for c in comps
                 if c.values.get(dim.key) is not None}

    # not enough information -> n/a
    if s_val is None or not comp_vals:
        return DimensionGap(dim, s_val, comp_vals, "n/a",
                            _na_detail(dim, s_val, comp_vals))

    if dim.direction == "info":
        return DimensionGap(dim, s_val, comp_vals, "n/a",
                            f"{dim.label}: you={s_val}; "
                            + ", ".join(f"{k}={v}" for k, v in comp_vals.items()))

    if dim.kind == "bool":
        return _bool_gap(dim, s_val, comp_vals)
    return _numeric_gap(dim, s_val, comp_vals)


def _bool_gap(dim, s_val, comp_vals) -> DimensionGap:
    n = len(comp_vals)
    with_it = [k for k, v in comp_vals.items() if v]
    without = [k for k, v in comp_vals.items() if not v]

    if s_val and len(without) >= 1 and len(without) >= len(with_it):
        verdict = "ahead"
        detail = f"You have {dim.label.lower()}; {len(without)}/{n} compared peers don't."
    elif (not s_val) and len(with_it) >= 1 and len(with_it) >= len(without):
        verdict = "behind"
        detail = f"You lack {dim.label.lower()}; {len(with_it)}/{n} peers have it ({_short(with_it)})."
    elif (not s_val) and len(with_it) == 0:
        verdict = "whitespace"
        detail = f"{dim.label} is absent for you and all {n} compared peers — an opening."
    else:
        verdict = "par"
        detail = f"{dim.label}: comparable to peers ({len(with_it)}/{n} have it)."
    return DimensionGap(dim, s_val, comp_vals, verdict, detail)


def _numeric_gap(dim, s_val, comp_vals) -> DimensionGap:
    # CS-101 (DISCLOSED, not silently fixed): for count dims like cta_count/social_count the SUBJECT's
    # value comes from a FULL crawl+LLM while each PEER's value is a single-homepage anchor count —
    # different measurement methods, so an "ahead/behind" verdict on the raw counts is only
    # directional, not commensurable. Normalising the two would need a peer full-crawl (a cost/scope
    # decision for the owner — logged in logs/NEEDS_OWNER.md). Presence dims (_presence_gap) are not
    # affected. Kept as-is deliberately; the verdict wording stays advisory.
    vals = list(comp_vals.values())
    avg = sum(vals) / len(vals)
    n = len(vals)
    # ~15% band around the peer average counts as "par"
    band = max(abs(avg) * 0.15, _abs_band(dim))
    if s_val > avg + band:
        verdict = "ahead"
        detail = f"Your {dim.label.lower()} ({_fmt(s_val)}) is above the peer average ({_fmt(avg)}, n={n})."
    elif s_val < avg - band:
        verdict = "behind"
        detail = f"Your {dim.label.lower()} ({_fmt(s_val)}) is below the peer average ({_fmt(avg)}, n={n})."
    else:
        verdict = "par"
        detail = f"Your {dim.label.lower()} ({_fmt(s_val)}) is around the peer average ({_fmt(avg)}, n={n})."
    return DimensionGap(dim, s_val, comp_vals, verdict, detail)


# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------

def _abs_band(dim) -> float:
    return 0.2 if dim.key == "rating" else (1.0 if dim.kind == "count" else 0.0)


def _fmt(v):
    if isinstance(v, float):
        return f"{v:.1f}"
    return str(v)


def _short(names, k=2):
    names = list(names)
    head = ", ".join(names[:k])
    return head + (f" +{len(names) - k} more" if len(names) > k else "")


def _na_detail(dim, s_val, comp_vals) -> str:
    if s_val is None and not comp_vals:
        return f"{dim.label}: unknown for you and all peers."
    if s_val is None:
        return f"{dim.label}: unknown for you (not extracted)."
    return f"{dim.label}: no peer data to compare against."