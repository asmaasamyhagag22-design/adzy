"""MarketDefinition — the grounded PROJECTION of a brand's profile into its market
(v2.2 U8b slice, INTERFACES §F extend-in-place; consumed by Discovery v2 and referenced
by MediaPlan.market_definition_ref).

PURE projection: every field derives from the PROFILE's evidenced fields (PD-4: no
network here). Missing evidence stays empty/None — an honest sparse definition beats an
invented market (the advisor flags gaps downstream). Config-by-category with a universal
default (D-8): radius defaults per category class; nothing vertical-hacked in logic.
"""
from __future__ import annotations

from typing import Any, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


def _get(obj: Any, key: str, default: Any = None) -> Any:
    val = obj.get(key, default) if isinstance(obj, dict) else getattr(obj, key, default)
    return default if val is None else val


def _fv(obj: Any, key: str) -> str:
    v = _get(obj, key)
    if isinstance(v, dict) and "value" in v:
        v = v.get("value")
    v = getattr(v, "value", v)
    return str(v).strip() if v else ""


class Geo(BaseModel):
    model_config = ConfigDict(extra="forbid")
    country: str = ""
    city: str = ""
    districts: List[str] = Field(default_factory=list)
    radius_km: Optional[float] = Field(default=None, gt=0.0)


class AudienceSketch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    summary: str = ""
    audience_type: str = ""          # b2c / b2b / mixed when the profile knows it


class MarketDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")
    brand_ref: str = ""              # the brand's source_url (the stable reference key)
    geo: Geo = Field(default_factory=Geo)
    category: str = ""
    sub_services: List[str] = Field(default_factory=list)
    audience: AudienceSketch = Field(default_factory=AudienceSketch)
    tier: Literal["economy", "mid", "premium"] = "mid"
    query_seeds: List[str] = Field(default_factory=list)   # ar+en, services x geo

    @property
    def is_grounded(self) -> bool:
        """A definition is usable when it knows WHAT is sold and WHERE (country or city)."""
        return bool(self.category and (self.geo.country or self.geo.city))


# Universal default search radius per CATEGORY CLASS (config, not logic — D-8). A walk-in
# local business competes within a few km; a delivery brand city-wide; ecommerce is
# geography-free (None -> national).
_RADIUS_BY_CATEGORY: dict[str, float] = {
    "restaurant": 5.0, "cafe": 5.0, "clinic": 10.0, "hospital": 15.0,
    "fitness": 8.0, "beauty": 8.0, "education": 25.0, "government": 50.0,
    "services_b2c": 15.0, "automotive": 20.0, "real_estate": 25.0,
}
_DEFAULT_LOCAL_RADIUS = 15.0
_GEOGRAPHY_FREE = {"ecommerce", "retail", "services_b2b", "saas"}

_TIER_TOKENS = {
    "premium": ("premium", "luxury", "high-end", "فاخر", "بريميوم", "راقي"),
    "economy": ("budget", "cheap", "affordable", "اقتصادي", "بأسعار", "خصومات", "رخيص"),
}


def _tier_from(profile: Any) -> Literal["economy", "mid", "premium"]:
    blob = " ".join(_fv(profile, k) for k in ("pricing_posture", "tone_of_voice",
                                              "description", "tagline")).lower()
    for tier, toks in _TIER_TOKENS.items():
        if any(t in blob for t in toks):
            return tier  # type: ignore[return-value]
    return "mid"


def _city_from_addresses(profile: Any) -> tuple[str, List[str]]:
    """(city, districts) best-effort from the evidenced physical addresses: the LAST
    comma-segment before the country is usually the city; earlier segments districts."""
    cc = _get(profile, "contact_channels") or {}
    for a in (_get(cc, "physical_addresses") or []):
        a = a.get("value") if isinstance(a, dict) else getattr(a, "value", a)
        parts = [p.strip() for p in str(a or "").split(",") if p.strip()]
        if len(parts) >= 2:
            return parts[-1], parts[:-1][:3]
        if parts:
            return parts[0], []
    return "", []


def _brand_words(p) -> set:
    """The brand's own name words + domain label — never a useful competitor-search term."""
    import re as _re
    from urllib.parse import urlparse as _up
    words = set()
    name = _fv(p, "name")
    if name:
        words |= {w.lower() for w in _re.findall(r"[\w؀-ۿ]+", name) if len(w) >= 3}
    src = _fv(p, "source_url") or _fv(p, "url")
    host = _up(str(src)).netloc.lower().replace("www.", "")
    if host:
        label = host.split(".")[0]
        if len(label) >= 3:
            words.add(label)
            for w in list(words):
                if w != label and w in label:
                    words.add(w)
    return words


def build_market_definition(profile: Any) -> MarketDefinition:
    """Project the profile into its market. Pure; never raises; empty fields = honest gaps."""
    p = profile or {}
    if isinstance(p, dict) and isinstance(p.get("profile"), dict):
        p = p["profile"]

    from poster.locale import country_of
    country = country_of(p)
    city, districts = _city_from_addresses(p)
    if not city:
        for area in (_get(p, "service_areas") or []):
            v = area.get("value") if isinstance(area, dict) else getattr(area, "value", area)
            v = str(v or "").strip()
            if v and v.lower() != country.replace("the ", "").lower():
                city = v.split(",")[0].strip()
                break

    category = _fv(p, "category").lower()
    radius = (None if category in _GEOGRAPHY_FREE
              else _RADIUS_BY_CATEGORY.get(category, _DEFAULT_LOCAL_RADIUS))

    subs: List[str] = []
    for o in (_get(p, "offerings") or []):
        name = o.get("name") if isinstance(o, dict) else getattr(o, "name", None)
        if isinstance(name, dict):
            name = name.get("value")
        name = str(name or "").strip()
        if name and name.lower() not in {s.lower() for s in subs}:
            subs.append(name)
        if len(subs) >= 10:
            break

    # P-83 (D-8): the superlative seed used to be hardcoded Arabic ("أفضل {svc} في {place}") for
    # EVERY brand — wrong for a US/Turkish/French brand. Key it on the brand's MEASURED language;
    # the plain "{svc} {place}" seed is universal and always emitted, the localized "best …" seed
    # is added only for a language we can phrase (else the plain seed alone, never a wrong-language one).
    def _lang_code(x) -> str:
        c = getattr(x, "code", None) or (x.get("code") if isinstance(x, dict) else x)
        return str(c or "").strip().lower()[:2]
    _langs = _get(p, "languages") or []
    lang0 = next((_lang_code(x) for x in _langs if _lang_code(x)), "")
    _BEST_SEED = {
        "ar": "أفضل {svc} في {place}",
        "en": "best {svc} in {place}",
        "fr": "meilleur {svc} à {place}",
        "tr": "{place} en iyi {svc}",
        "es": "mejor {svc} en {place}",
        "de": "beste {svc} in {place}",
    }
    best_tmpl = _BEST_SEED.get(lang0, "")

    seeds: List[str] = []
    plain_country = country.replace("the ", "")
    # MEASURED (Volume Qatar): places used to be districts-then-city, so the lead seed carried the
    # micro-district "Qanat Quartier" alone — a place Google barely knows. A searchable anchor
    # (city, else country) leads; the district still follows for precision.
    places = [p for p in ([city] if city else []) + [d for d in districts[:2]] if p]
    if plain_country:
        places.append(plain_country)
    places = list(dict.fromkeys(places))
    if city and plain_country and city != plain_country:
        places.insert(0, f"{city} {plain_country}")     # "Qanat Quartier Qatar" resolves; either alone may not

    # MARKET FIRST. `subs` are raw offering names — for this café, individual dishes; for nanocare,
    # its own SKUs. Searching them finds nothing (nobody else sells "Volume Catering") and two of
    # three issued seeds carried the brand's own name. The CATEGORY is what the market is called,
    # so it leads; the product terms follow for precision, minus anything naming the brand itself.
    brand_words = _brand_words(p)

    def _clean(term: str) -> str:
        kept = [w for w in str(term or "").split() if w.lower().strip(".,&") not in brand_words]
        return " ".join(kept).strip()

    # process.md §8.11: lead a MARKET search with the market. `market_category` is the free-text
    # "what is sold" ("supplements"); `category` is the coarse label and, for many brands, the
    # business MODEL ("ecommerce"/"services_b2c") — which finds nobody. Prefer the market; fall
    # back to `category` for un-migrated profiles. The RADIUS above still keys on `category` (that
    # is a MODEL question — geography-free ecommerce vs a walk-in — so it stays on the right axis).
    market_lead = _fv(p, "market_category").strip() or category
    ordered_terms: List[str] = []
    if market_lead:
        ordered_terms.append(market_lead)
    for svc in subs[:5]:
        svc = _clean(svc)
        if svc and svc.lower() not in {t.lower() for t in ordered_terms}:
            ordered_terms.append(svc)

    for svc in ordered_terms:
        for place in places[:3]:
            seeds.append(f"{svc} {place}")
            if best_tmpl:
                seeds.append(best_tmpl.format(svc=svc, place=place))
    seeds = seeds[:20]

    return MarketDefinition(
        brand_ref=_fv(p, "source_url") or _fv(p, "url"),
        geo=Geo(country=country, city=city, districts=districts, radius_km=radius),
        category=category,
        sub_services=subs,
        audience=AudienceSketch(summary=_fv(p, "audience_type") or _fv(p, "description")[:200],
                                audience_type=_fv(p, "audience_type")),
        tier=_tier_from(p),
        query_seeds=seeds,
    )
