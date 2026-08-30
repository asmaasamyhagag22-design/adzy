"""Competitor discovery orchestrator.

Flow:
    BusinessProfile
        -> build_match_criteria()        # the ONLY adapter to your schema
        -> [geocode if needed]
        -> Places search (nearby, or text for online-only)
        -> compute distances (haversine)
        -> select_peers()                 # hard filters + peer-fit + dedup
        -> fetch reviews for the chosen peers
        -> PeerMatchResult

What this module does NOT do: it does not scrape the competitors' websites and
it does not build the Comparative Gap Matrix or the SWOT. Those are the next
chunk and they consume `PeerMatchResult`.
"""

from __future__ import annotations

import logging
import math
from typing import List, Optional

from .models import CompetitorProfile, MatchCriteria, PeerMatchResult
from .peer_match import (
    TARGET_PEERS, select_peers, tokenize, passes_hard_filters, expand_bilingual,
)
from .places_client import PlacesClient, _redact_secrets

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Category -> Google Places type strings.
# STARTER MAP for your four verticals. Refine the right-hand side against the
# current Places "Place Types (New)" table — Google updates it (last update
# Feb 2026). These are the search/filter types, not your internal labels.
# ---------------------------------------------------------------------------
CATEGORY_TYPE_MAP = {
    # These are GENEROUS families used only by the hard "is it the right kind of
    # place at all" sanity filter (candidate.types must intersect this set). They
    # are intentionally wide so legit sub-vertical variants are not excluded by
    # Places' inconsistent typing. Fine relevance (e.g. derma vs radiology) comes
    # from the offerings-driven text query + the sub_vertical score, NOT from here.
    # Extra type strings that don't exist in Places are harmless in a filter set.
    "restaurant": ["restaurant", "cafe", "bakery", "bar", "meal_takeaway",
                   "meal_delivery", "food", "fast_food_restaurant"],
    "cafe": ["cafe", "coffee_shop", "bakery", "restaurant", "meal_takeaway", "food"],
    "clinic": ["doctor", "dentist", "dental_clinic", "medical_clinic", "medical_center",
               "hospital", "physiotherapist", "spa", "beauty_salon", "skin_care_clinic",
               "wellness_center", "chiropractor"],
    "hospital": ["hospital", "doctor", "medical_center", "medical_clinic", "dental_clinic",
                 "physiotherapist", "wellness_center"],
    "skincare": ["beauty_salon", "skin_care_clinic", "spa", "cosmetics_store",
                 "hair_care", "wellness_center", "doctor", "medical_clinic"],
    # P-82 (D-8): the map used to cover only 4 verticals, so EVERY other real BusinessCategory
    # (retail/ecommerce/beauty/fitness/automotive/…) had place_types=[] and the coarse category
    # sanity-filter silently passed ALL place types. (It also had a phantom "skincare" key that is
    # not even a BusinessCategory value, while the real "beauty" had none.) These entries give the
    # common consumer verticals a universal coarse filter; fine relevance still comes from the
    # offerings text query + sub_vertical score. B2B/agency/government stay unmapped by design
    # (weak Places typing) and continue to rely on the text query.
    "education": ["school", "primary_school", "secondary_school", "university",
                  "preschool", "tutoring_center", "library"],
    "beauty": ["beauty_salon", "hair_care", "hair_salon", "nail_salon", "spa",
               "skin_care_clinic", "cosmetics_store", "wellness_center", "barber_shop"],
    "retail": ["store", "clothing_store", "shoe_store", "department_store", "shopping_mall",
               "supermarket", "convenience_store", "electronics_store", "furniture_store",
               "jewelry_store", "book_store", "market", "grocery_store"],
    "ecommerce": ["store", "clothing_store", "shoe_store", "department_store", "electronics_store",
                  "furniture_store", "jewelry_store", "supermarket", "market"],
    "fitness": ["gym", "fitness_center", "sports_complex", "yoga_studio", "sports_club"],
    "automotive": ["car_dealer", "car_repair", "car_wash", "auto_parts_store", "gas_station"],
    "real_estate": ["real_estate_agency"],
    "hospitality": ["hotel", "lodging", "resort_hotel", "motel", "guest_house", "bed_and_breakfast"],
}


# ===========================================================================
# ADAPTER  ->  the only place that reads your BusinessProfile.
# Adjust the attribute names on the right to match your real BusinessProfile.
# Everything below the adapter is schema-agnostic.
# ===========================================================================
def build_match_criteria(profile, *,
                         category_type_map: Optional[dict] = None) -> MatchCriteria:
    category_type_map = category_type_map or CATEGORY_TYPE_MAP

    # --- category (your internal label) ---
    category = _get(profile, "category", default="") or ""
    # category may be a BusinessCategory enum (a str-subclass): use its .value.
    # str(SomeEnum.X) yields "BusinessCategory.X" — that broke the type-map lookup
    # AND leaked "BusinessCategory.ECOMMERCE" into the Places text query.
    category_str = str(getattr(category, "value", category)).strip()
    place_types = category_type_map.get(category_str.lower(), [])

    # --- offerings + category -> keyword set for name overlap ---
    # Use each offering's NAME only. str(o) would dump the whole object repr
    # (field names + evidence + page_url) into the query, producing garbage like
    # "name short description evidence block id https ... llm" -> 0 Places hits.
    offerings = _get(profile, "offerings", default=[]) or []
    offering_text = " ".join(n for n in (_offering_name(o) for o in offerings) if n)
    offering_text = (offering_text + " " + category_str).strip()
    offering_keywords = expand_bilingual(list(dict.fromkeys(tokenize(offering_text))))
    # The brand's OWN name words are useless in every downstream consumer — searching Places for
    # "Volume Catering" can only return Volume. Two of the three seeds this profile issued carried
    # it (MEASURED, 2026-07-21).
    #
    # `brand_identity_tokens` rather than `tokenize(name)`: nanocare's profile name is the ENGLISH
    # "Nano Care Therapeutics" while its products are Arabic ("نانو كير ليبوسومال حديد"), so a
    # name-only comparison stripped nothing and the query still went out as
    # 'ecommerce نانو كير ليبوسومال فيتامين'. That helper already infers brand words from OFFERING
    # FREQUENCY — a token riding most offerings is the brand, in whatever alphabet — which is the
    # same cross-script gap solved once for trend seeds. One guard, not two.
    _brand: set = set()
    try:
        from trends.engine import brand_identity_tokens
        _brand = {b.lower() for b in brand_identity_tokens(
            profile if isinstance(profile, dict) else {})}
    except Exception:  # noqa: BLE001
        _brand = set()
    _brand |= {t.lower() for t in tokenize(str(_get(profile, "name", default="") or ""))}
    # Also the GLUED form of any two adjacent brand tokens ("نانو"+"كير" -> "نانوكير"), which the
    # scraper emits as a single word on some product names and which slipped through otherwise.
    _brand |= {a + b for a in list(_brand) for b in list(_brand) if a != b}
    _brand -= {category_str.lower()}          # the category is never a brand word (D-8, "X Cafe")
    if _brand:
        offering_keywords = [k for k in offering_keywords if k.lower() not in _brand]

    # --- audience tier + confidence ---
    audience_tier = _normalize_tier(_get(profile, "audience_type", default=None))
    audience_conf = float(_get(profile, "audience_confidence", default=0.0) or 0.0)

    # --- languages ---
    languages = _get(profile, "languages", default=[]) or []
    languages = [str(l).lower()[:2] for l in languages]

    # --- location: prefer explicit lat/lng, else an address the geocoder can resolve ---
    lat = _get(profile, "lat", default=None)
    lng = _get(profile, "lng", default=None)
    address = _subject_address(profile)

    # --- online-only heuristic: no address and no coords. Override if you have
    #     a real flag on the profile. ---
    is_online_only = not address and lat is None and lng is None

    # --- subject's own Places review count (often None pre-enrichment) ---
    review_count_self = _get(profile, "review_count", default=None)

    crit = MatchCriteria(
        category=category_str,
        place_types=place_types,
        offering_keywords=offering_keywords,
        audience_tier=audience_tier,
        audience_confidence=audience_conf,
        languages=languages,
        lat=lat,
        lng=lng,
        address=address,
        is_online_only=is_online_only,
        review_count_self=review_count_self,
    )
    return crit


def _get(obj, name, default=None):
    """Read attr or dict key; unwrap a `.value` wrapper if present (your
    BusinessProfile uses name.value etc.).

    Handles BOTH profile shapes: the OBJECT profile (EvidencedField with a .value
    attribute — the CLI path) and the SERIALIZED dict profile (the web-API path,
    where an EvidencedField arrives as {"value": ..., "evidence": [...]}). The
    dict shape was NOT unwrapped before, so on the web path the whole evidence
    dict leaked into the Places text query as its repr — a garbage query that
    made discovery return 0 usable peers (measured root cause of the always-
    standalone, no-Threats/no-Opportunities SWOT in the web app)."""
    val = getattr(obj, name, None)
    if val is None and isinstance(obj, dict):
        val = obj.get(name)
    if val is None:
        return default
    if isinstance(val, dict) and "value" in val:
        inner = val.get("value")
        return inner if inner is not None else default
    inner = getattr(val, "value", None)
    return inner if inner is not None else val


def build_text_query(crit) -> str:
    """The Places text query — CATEGORY first, brand name never.

    MEASURED (Volume Qatar re-run, 2026-07-21): this was
    `offering_keywords[:6] + [category]`, so the query went out as
    `'tomato basil soup baked salmon with cafe'` — dish words in document order with the category
    pushed to the end. The market-first ordering had reached `market_definition.query_seeds` and
    stopped one function short of here. Same rule, one place later: the category opens the query,
    a few offering terms follow for precision. The brand's own name is already stripped from
    `offering_keywords` in `build_match_criteria` (searching for "Volume Catering" can only find
    Volume).
    """
    terms = []
    if crit.category:
        terms.append(str(crit.category))
    for t in (crit.offering_keywords or []):
        t = str(t or "").strip()
        if t and t.lower() not in {x.lower() for x in terms}:
            terms.append(t)
        if len(terms) >= 5:
            break
    return " ".join(terms).strip() or str(crit.category or "")


def _subject_address(profile) -> str:
    """The brand's location as a geocodable string — read from the fields that ACTUALLY exist.

    MEASURED (2026-07-21, all 22 saved profiles): none carries `physical_address`/`lat`/`lng`, the
    fields this module used to read; they are not on `business_profile/schemas.py`. The evidence
    lives in `locations` (2/22), `contact_channels.physical_addresses`, and above all
    `service_areas` (13/22). Because of that, `is_online_only` was True for EVERY brand and the
    implemented geocode→nearby search below never ran once — a Doha café was searched with no
    coordinates and no location bias.

    Most specific source first. `service_areas` are joined (specific → general: "Qanat Quartier,
    The Pearl Qatar, Qatar") because a lone micro-district often fails to geocode while the same
    string with its city and country resolves.
    """
    def _text(v) -> str:
        if isinstance(v, dict):
            v = v.get("value") or v.get("address") or v.get("formatted_address") or ""
        return " ".join(str(v or "").split())

    p = profile or {}
    if isinstance(p, dict) and isinstance(p.get("profile"), dict):
        p = p["profile"]

    for loc in (_get(p, "locations", default=[]) or []):
        addr = _text(loc)
        if addr:
            return addr
    cc = _get(p, "contact_channels", default={}) or {}
    for addr in (_get(cc, "physical_addresses", default=[]) or []):
        addr = _text(addr)
        if addr:
            return addr
    areas = [_text(a) for a in (_get(p, "service_areas", default=[]) or [])]
    areas = [a for a in areas if a]
    return ", ".join(dict.fromkeys(areas)) if areas else ""


def _offering_name(o) -> str:
    """The offering's display name only — never str(o) on a model (which serializes
    the whole Offering object, leaking field names + evidence + URLs into the query).
    Handles all three offering shapes: plain string, dict, or Offering object —
    and BOTH EvidencedField shapes (object .value / serialized {"value": ...})."""
    if isinstance(o, str):
        return o.strip()
    name = o.get("name") if isinstance(o, dict) else getattr(o, "name", None)
    if isinstance(name, dict) and "value" in name:      # serialized EvidencedField
        name = name.get("value")
    inner = getattr(name, "value", None)                # object EvidencedField
    name = inner if inner is not None else name
    return str(name).strip() if name else ""


def _normalize_tier(raw) -> Optional[str]:
    if not raw:
        return None
    s = str(raw).lower()
    if any(w in s for w in ("premium", "luxury", "high")):
        return "premium"
    if any(w in s for w in ("budget", "low", "cheap", "economy")):
        return "budget"
    if any(w in s for w in ("mid", "medium", "mass")):
        return "mid"
    return None


# ---------------------------------------------------------------------------
def haversine_m(lat1, lng1, lat2, lng2) -> Optional[float]:
    if None in (lat1, lng1, lat2, lng2):
        return None
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lng2 - lng1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return r * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


# ---------------------------------------------------------------------------
def discover_competitors(
    profile,
    client: PlacesClient,
    *,
    radius_m: float = 15000,
    candidate_pool: int = 20,
    target: int = TARGET_PEERS,
    require_website: bool = False,
    min_scrapable_benchmarks: int = 2,
    fetch_reviews: bool = True,
    category_type_map: Optional[dict] = None,
) -> PeerMatchResult:
    crit = build_match_criteria(profile, category_type_map=category_type_map)
    address = crit.address
    notes: List[str] = []

    if not crit.place_types:
        notes.append(f"no Places type mapping for category '{crit.category}'")

    # ---- build the sub-vertical query ----
    # THIS is the lever that makes the pool relevant. Searching the broad category
    # ("clinic") ranked by popularity returns the biggest medical places nearby,
    # not the right *kind* of business. A query built from offerings ("laser skin
    # acne dermatology clinic") makes Google return actual peers. We deliberately
    # do NOT pass includedType: Places typing is inconsistent (a derma clinic may
    # be typed medical_clinic, skin_care_clinic, or beauty_salon), so a hard type
    # filter at retrieval would drop real peers. Type sanity is handled by the
    # (generous) hard-filter family + the sub_vertical score instead.
    text_query = build_text_query(crit)

    # U8b DISCOVERY v2 (2026-07-12): the MarketDefinition's BILINGUAL query_seeds widen
    # retrieval — the single offerings-query is usually English-only, so Arabic-named local
    # peers never entered the pool at all. Additive: the v1 query stays first, seeds follow,
    # the union dedupes by place_id, and the scoring/hard-filter pipeline is UNCHANGED.
    seed_queries: List[str] = []
    try:
        from competitor.market_definition import build_market_definition
        _md = build_market_definition(profile)
        if _md.is_grounded:
            seed_queries = [q.strip() for q in (_md.query_seeds or []) if q and q.strip()][:3]
    except Exception:  # noqa: BLE001 — seeds are additive; their absence IS v1 behavior
        seed_queries = []
    queries = [text_query] + [q for q in seed_queries if q.lower() != text_query.lower()]

    def _union_search(**kw) -> List:
        seen_keys: set = set()
        pool: List = []
        for q in queries:
            try:
                found = client.search_text(q, **kw) or []
            except Exception:  # noqa: BLE001 — one bad seed never kills discovery
                continue
            for c in found:
                key = getattr(c, "place_id", None) or (getattr(c, "name", ""),
                                                       getattr(c, "website", ""))
                if key in seen_keys:
                    continue
                seen_keys.add(key)
                pool.append(c)
            if len(pool) >= candidate_pool:
                break
        if len(queries) > 1:
            notes.append(f"v2 multi-seed retrieval: {len(queries)} queries -> "
                         f"{len(pool)} unique candidates")
        return pool[:candidate_pool]

    # ---- get candidates ----
    if crit.is_online_only:
        notes.append(f"online-only: text search {text_query!r}, proximity dropped")
        candidates = _union_search(max_results=candidate_pool)
    else:
        if crit.lat is None or crit.lng is None:
            if not address:
                notes.append("no coordinates and no address; cannot run nearby search")
                return PeerMatchResult([], True, 0, 0, notes)
            # Prefer Places text search (needs only the Places API enabled).
            geo = client.geocode_via_places(address)
            if geo:
                notes.append(f"resolved center via Places text search -> ({geo[0]:.5f}, {geo[1]:.5f})")
            else:
                geo = client.geocode_address(address)  # fallback: Geocoding API
                if geo:
                    notes.append(f"resolved center via Geocoding API -> ({geo[0]:.5f}, {geo[1]:.5f})")
            if not geo:
                err = getattr(client, "last_geocode_error", None)
                notes.append(
                    f"could not resolve coordinates for {address!r}. "
                    f"Places text search returned nothing"
                    + (f"; Geocoding API said: {err}" if err else "")
                    + ". Fix: pass lat/lng directly in the profile, or enable the "
                    "Geocoding API on your key."
                )
                return PeerMatchResult([], True, 0, 0, notes)
            crit.lat, crit.lng = geo
        notes.append(f"text search {text_query!r} biased to "
                     f"({crit.lat:.4f},{crit.lng:.4f}) r={radius_m}m")
        candidates = _union_search(lat=crit.lat, lng=crit.lng,
                                   radius_m=radius_m, max_results=candidate_pool)

    # drop the subject itself if it appears (match by website domain / exact name)
    candidates = [c for c in candidates if not _is_self(c, profile)]

    # ---- distances ----
    for c in candidates:
        c.distance_m = haversine_m(crit.lat, crit.lng, c.lat, c.lng)

    # locationBias is a bias, not a hard limit, so cap to the radius for a local set
    if not crit.is_online_only:
        before = len(candidates)
        candidates = [c for c in candidates
                      if c.distance_m is None or c.distance_m <= radius_m]
        dropped = before - len(candidates)
        if dropped:
            notes.append(f"dropped {dropped} candidate(s) beyond {radius_m}m")

    # ---- per-candidate hard-filter audit (explains a thin result) ----
    audit = []
    for c in candidates:
        ok, reason = passes_hard_filters(c, crit, require_website=require_website)
        audit.append({
            "name": c.name,
            "type": c.primary_type,
            "reviews": c.review_count,
            "website": c.website,
            "dist_km": round(c.distance_m / 1000, 1) if c.distance_m is not None else None,
            "passed": ok,
            "reason": reason,
        })

    # ---- select (two-tier) ----
    ranked, stats = select_peers(candidates, crit, require_website=require_website)

    # Local tier: top `target` by fit — the close, representative peers that drive
    # review themes (the local customer voice).
    local = ranked[:target]
    local_ids = {c.place_id for c, _ in local}
    n_scrapable_local = sum(1 for c, _ in local if _is_scrapable_site(c.website))

    # Benchmark tier: pull the best scrapable peers (above floor) NOT already local,
    # so the matrix has something to scrape for the dimension comparison. Capped so
    # we don't drag in many far/big clinics.
    benchmarks: List = []
    need = max(0, min_scrapable_benchmarks - n_scrapable_local)
    if need:
        for c, rec in ranked:
            if c.place_id in local_ids:
                continue
            if _is_scrapable_site(c.website):
                benchmarks.append((c, rec))
                if len(benchmarks) >= need:
                    break

    chosen = list(local) + benchmarks  # local first, then added benchmarks

    if len(local) < target:
        notes.append(
            f"thin local tier: only {len(local)} of {target} peers cleared the floor "
            f"({stats['passed_filters']} passed filters of {stats['considered']} considered). "
            f"Not padding with sub-floor candidates."
        )

    # ---- reviews + build profiles for both tiers ----
    competitors: List[CompetitorProfile] = []
    for cand, rec in chosen:
        reviews = client.get_reviews(cand.place_id) if fetch_reviews else []
        competitors.append(CompetitorProfile(
            candidate=cand, selection=rec, reviews=reviews,
            has_scrapable_site=_is_scrapable_site(cand.website),
            is_local=cand.place_id in local_ids,
        ))

    n_scrapable = sum(1 for c in competitors if c.has_scrapable_site)
    n_added = len(benchmarks)
    notes.append(
        f"tiers: {len(local)} local (review themes) + {n_added} added benchmark(s); "
        f"{n_scrapable} scrapable peer(s) for the dimension matrix"
    )
    if n_scrapable == 0:
        notes.append(
            "no scrapable competitor found even after the benchmark pass; the matrix "
            "will rely on Places signals only (rating / reviews / price level). Consider "
            "a larger radius if you need scraped-dimension comparison."
        )

    return PeerMatchResult(
        competitors=competitors,
        thin_peer_set=len(local) < target,
        candidates_considered=stats["considered"],
        candidates_passed_filters=stats["passed_filters"],
        notes=notes,
        audit=audit,
    )


_SOCIAL_HOSTS = {
    "facebook.com", "m.facebook.com", "fb.com", "fb.me", "instagram.com",
    "tiktok.com", "linktr.ee", "wa.me", "api.whatsapp.com", "twitter.com",
    "x.com", "youtube.com", "youtu.be", "t.me",
}


def _is_scrapable_site(url: Optional[str]) -> bool:
    """True if the URL is a real domain we could scrape (not a social profile)."""
    if not url:
        return False
    from .peer_match import _domain
    d = _domain(url)
    return bool(d) and d not in _SOCIAL_HOSTS


# Generic registrable-domain labels that are NOT a distinctive brand token — matching one
# in a competitor's name would wrongly drop a real peer. The brand-label self-check skips these.
_GENERIC_BRAND_LABELS = {
    "shop", "store", "online", "web", "app", "site", "home", "group", "company",
    "official", "the", "your", "best", "top", "new", "info", "page", "portal", "eg",
}


def _subject_brand_label(profile) -> Optional[str]:
    """The subject's own brand token from its registrable domain (PSL-correct): the FIRST
    label of the eTLD+1 — 'iti' from iti.gov.eg, 'vodafone' from web.vodafone.com.eg. It's
    the subject's own web identity, so it's a reliable CROSS-SCRIPT self signal when the
    Places listing carries the brand acronym in another script ('معهد ... - ITI'). None when
    the URL has no PSL match or the label is a generic word that would over-match a peer."""
    for u in (_get(profile, "website", default=None), _get(profile, "url", default=None),
              _get(profile, "source_url", default=None)):
        if not u:
            continue
        try:
            from scraper.url_utils import _registrable_domain
            reg = _registrable_domain(str(u))
        except Exception:
            reg = None
        if not reg:
            continue
        label = reg.split(".", 1)[0].strip().lower()
        if len(label) >= 3 and label not in _GENERIC_BRAND_LABELS:
            return label
    return None


def _is_self(cand, profile) -> bool:
    """Best-effort: drop the subject business if Places returned it — including a DIFFERENT-
    SCRIPT branch of the same brand. Measured on ITI: the subject 'Information Technology
    Institute' / iti.gov.eg came back from Places as 'معهد تكنولوجيا المعلومات - ITI' with no
    website, so both the domain check (candidate has no site) and the exact-name check (English
    vs Arabic) missed it and the brand was listed as its OWN competitor."""
    from .peer_match import _domain, normalize_text, tokenize
    own_site = (_get(profile, "website", default=None) or _get(profile, "url", default=None)
                or _get(profile, "source_url", default=None))
    if own_site and cand.website:
        if _domain(own_site) and _domain(own_site) == _domain(cand.website):
            return True
    own_name = _get(profile, "name", default=None)
    if own_name and cand.name:
        if normalize_text(str(own_name)) == normalize_text(cand.name):
            return True
    # Cross-script same-brand: the subject's own domain brand-label appears as a distinct token
    # in the candidate's name ('iti' in '...- ITI'). Domain labels are brand-specific, so this
    # rarely touches a true competitor; when it does (a genuinely different firm sharing the
    # acronym) dropping the ambiguous peer is the conservative SWOT choice (rule 4: someone
    # else's numbers are worse than UNKNOWN).
    label = _subject_brand_label(profile)
    if label and cand.name and label in tokenize(cand.name):
        return True
    return False


def find_subject_places(profile, client, *, max_results: int = 8):
    """Find the SUBJECT's own Places listing (rating / review volume / price tier).

    Without it the matrix's Places dimensions have no subject value, every Places gap
    is "n/a", and the SWOT can never emit a market-position THREAT ("your review volume
    is below the peer average") — the measured root cause of the always-empty Threats
    quadrant on the web path.

    Grounded matching only: the candidate must match the subject by WEBSITE registrable
    domain (strongest signal — it's the subject's own site) or by exact normalized name.
    No fuzzy guessing: grounding the SWOT in someone ELSE's ratings would be worse than
    UNKNOWN (rule 4). Returns the matched Candidate or None; never raises.
    """
    if client is None or profile is None:
        return None
    try:
        name = _get(profile, "name", default=None)
        if not name or not str(name).strip():
            return None
        address = _get(profile, "physical_address", default=None)
        query = f"{name} {address}".strip() if address else str(name)
        candidates = client.search_text(query, max_results=max_results) or []

        from .peer_match import _domain, normalize_text
        own_site = (_get(profile, "website", default=None)
                    or _get(profile, "url", default=None)
                    or _get(profile, "source_url", default=None))
        own_domain = _domain(own_site) if own_site else None
        if own_domain:
            for c in candidates:
                if c.website and _domain(c.website) == own_domain:
                    return c
        norm_name = normalize_text(str(name))
        for c in candidates:
            if c.name and normalize_text(c.name) == norm_name:
                return c

        # Cross-script fallback (measured on Qasr Elkbabgi): the query IS the subject's
        # exact scraped name, but the Places listings are in another SCRIPT (Latin
        # profile name vs Arabic «قصر الكبابجي») with no website — so both grounded
        # matches above miss a true positive. Accept ONLY when >=2 returned candidates
        # are all the SAME brand (mutual containment of normalized names — branches/
        # variants of one business); ambiguity (different businesses) -> None, honest.
        named = [c for c in candidates if c.name and normalize_text(c.name)]
        if len(named) >= 2:
            cores = [normalize_text(c.name) for c in named]
            core = min(cores, key=len)
            if len(core) >= 4 and all(core in n for n in cores):
                return max(named, key=lambda c: c.review_count or 0)
        return None
    except Exception as exc:  # noqa: BLE001 — a lookup failure must never break the SWOT
        # "PLACES REFUSED US" AND "THIS BRAND HAS NO LISTING" ARE DIFFERENT FACTS, and this
        # `return None` made them the same one. The consequence was not cosmetic: Threats is empty
        # for every brand on this path, this function's own docstring names itself "the measured
        # root cause", and a whole investigation concluded the branch was dead in the CODE.
        #
        # MEASURED 2026-08-02, calling it directly:
        #     403 Permission denied: Consumer 'api_key:…' has been suspended
        # The key is suspended — the same Google account problem that stopped Veo and Gemini. There
        # was never anything wrong with this branch, and nobody could tell, because a refusal and
        # an absence both arrived here as None.
        #
        # Logged, not raised: the contract that a lookup failure must not break a SWOT stands.
        # REDACTED, because Google echoes the API KEY back inside the 403 body — logging the
        # failure verbatim would write a live credential into logs/ on every empty SWOT.
        logger.warning("[places] subject lookup failed (%s: %s) — the Threats quadrant will be "
                       "empty for a REASON, not for lack of a listing",
                       type(exc).__name__, _redact_secrets(str(exc))[:200])
        return None