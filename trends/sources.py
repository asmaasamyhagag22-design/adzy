"""Trend sources — pull currently-trending items from public APIs.

Each source satisfies the `TrendSource` protocol and returns a list of `TrendItem`.
NEVER raises — a network/parse error yields `[]` so one bad source can't break a campaign
run (same discipline as `competitor/search_providers.py`).

⚠ THE FIRST SOURCE IS PAID AND KEYED. `SerperTrendSource` goes through
`competitor.search_providers.get_default_search_provider()` — normally **serper.dev**, which
bills per search and needs `SERPER_API_KEY`. It is FIRST in `default_trend_sources` because
it is the only source that is actually relevant to a non-tech brand.
  (This docstring used to open "FREE, keyless public APIs" — written before Serper was added
  and left standing after. `spend_tripwire.py:9-11` and `trends/__main__.py:7` both had to
  carry a correction pointing at this line. Fixed 2026-07-15.)
The genuinely keyless ones: HackerNews (Firebase), Reddit (public JSON, needs a UA), Dev.to.

D-8 — THE MARKET IS A SEARCH PARAMETER, NOT AN ASSUMPTION. `SerperTrendSource` takes the
brand's `gl` (country) and `hl` (language) and passes them to the SERP. Both come from
EVIDENCE at the call site (`poster.locale.country_of` -> the brand's own domain / service
areas / phone; `hl` from the brand's own `languages`), and BOTH default to empty -> the
provider omits them -> Google's own default. Never a guessed market.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Protocol, runtime_checkable

from scraper.net import get_json as _resilient_get_json

_UA = "Mozilla/5.0 (compatible; MarketingTrends/0.1)"


@dataclass
class TrendItem:
    """One trending item. `score` is the source's raw popularity (HN points / Reddit
    upvotes / Dev.to reactions); `trend_score` (0..1) and `matched_terms` are filled by
    the engine."""
    title: str
    url: str
    source: str
    score: float = 0.0
    created_ts: Optional[float] = None        # unix seconds (UTC)
    trend_score: float = 0.0
    matched_terms: tuple[str, ...] = field(default_factory=tuple)


@runtime_checkable
class TrendSource(Protocol):
    name: str

    def fetch(self, limit: int = 30) -> list[TrendItem]:
        ...


def _get_json(url: str, *, timeout: int = 8):
    """GET + parse JSON; returns the decoded object or None on ANY failure.

    Delegates to the shared resilient fetcher (bounded retry + per-host CIRCUIT
    BREAKER) so a dead/slow source (e.g. HackerNews, which fans out one call per
    story) is retried on a transient blip but stops being hammered once it has failed
    repeatedly — instead of paying the full timeout on every one of dozens of calls."""
    return _resilient_get_json(url, timeout=timeout, headers={"User-Agent": _UA})


def _parse_iso(ts) -> Optional[float]:
    if not ts or not isinstance(ts, str):
        return None
    try:
        from datetime import datetime
        return datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
    except Exception:
        return None


class HackerNewsSource:
    """HackerNews top stories via the public Firebase API (no key)."""
    name = "hackernews"
    BASE = "https://hacker-news.firebaseio.com/v0"

    def fetch(self, limit: int = 30) -> list[TrendItem]:
        ids = _get_json(f"{self.BASE}/topstories.json") or []
        out: list[TrendItem] = []
        for i in ids[: max(0, limit)]:
            item = _get_json(f"{self.BASE}/item/{i}.json")
            if not item or item.get("type") != "story" or item.get("dead") or item.get("deleted"):
                continue
            title = item.get("title") or ""
            if not title:
                continue
            out.append(TrendItem(
                title=title,
                url=item.get("url") or f"https://news.ycombinator.com/item?id={i}",
                source=self.name,
                score=float(item.get("score") or 0),
                created_ts=item.get("time"),
            ))
        return out


class RedditSource:
    """Hot posts from a few subreddits via Reddit's public JSON (no key; needs a UA)."""
    name = "reddit"

    def __init__(self, subreddits: Optional[list[str]] = None):
        self.subreddits = subreddits or ["technology", "business", "marketing", "smallbusiness"]

    def fetch(self, limit: int = 30) -> list[TrendItem]:
        per = max(1, limit // max(1, len(self.subreddits)))
        out: list[TrendItem] = []
        for sub in self.subreddits:
            data = _get_json(f"https://www.reddit.com/r/{sub}/hot.json?limit={per}")
            for child in ((data or {}).get("data") or {}).get("children", []):
                d = child.get("data") or {}
                if d.get("stickied") or not d.get("title"):
                    continue
                permalink = d.get("permalink") or ""
                out.append(TrendItem(
                    title=d.get("title") or "",
                    url=("https://www.reddit.com" + permalink) if permalink else (d.get("url") or ""),
                    source=self.name,
                    score=float(d.get("ups") or d.get("score") or 0),
                    created_ts=d.get("created_utc"),
                ))
        return out


class DevToSource:
    """Top recent articles from the Dev.to public API (no key)."""
    name = "devto"

    def fetch(self, limit: int = 30) -> list[TrendItem]:
        data = _get_json(f"https://dev.to/api/articles?top=7&per_page={min(max(1, limit), 100)}")
        out: list[TrendItem] = []
        for a in (data or []):
            if not isinstance(a, dict) or not a.get("title"):
                continue
            out.append(TrendItem(
                title=a.get("title") or "",
                url=a.get("url") or "",
                source=self.name,
                score=float(a.get("public_reactions_count") or 0),
                created_ts=_parse_iso(a.get("published_timestamp")),
            ))
        return out


_GENERIC_KW = {"ecommerce", "retail", "shop", "store", "online", "brand", "company", "business",
               "service", "services", "product", "products", "quality", "premium", "best"}


class SerperTrendSource:
    """CURRENT, vertical-relevant trends via the configured SERP provider (Serper) — the reliable
    consumer source now that Reddit's public JSON is largely blocked. Searches the brand's own
    salient keywords + 'trends', so a jeweller gets jewellery trends, not Hacker News. Returns []
    (never raises) when no provider/key is configured or the search fails."""
    name = "web"

    def __init__(self, keywords: Optional[list[str]] = None, *, gl: str = "", hl: str = ""):
        """`gl` = the brand's country as an ISO-3166 alpha-2 code ("qa"); `hl` = the brand's
        own content language ("ar"). BOTH must come from EVIDENCE at the call site, and BOTH
        default to "" -> omitted from the request -> Google's own default.

        Why they are constructor args and not derived here: this module is a pure source with
        no profile and no dependency on `poster.locale`. The caller resolves, the source
        receives — the same contract `build_oneshot_prompt(locale_line=)` uses for the locale
        primer (poster/oneshot.py:115-118).

        `hl` is NOT derived from `gl`. "The country is Arab, therefore search in Arabic" is
        exactly the inference D-8 forbids (it is `strategy/builder.py:154`'s "any Arabic brand
        => Egyptian dialect" with the arrow reversed). A Doha cafe whose own site is English
        gets `gl="qa", hl=""`.
        """
        self.keywords = [str(k).lower() for k in (keywords or [])]
        self.gl = (gl or "").strip().lower()
        self.hl = (hl or "").strip().lower()

    def _query(self) -> str:
        # CS-44: salience, not insertion order — a keyword repeated across profile fields
        # (duplicates in self.keywords) outranks a once-seen one; ties keep original order
        # (deterministic output).
        from collections import Counter
        cand = [k for k in self.keywords if len(k) >= 4 and k not in _GENERIC_KW]
        counts = Counter(cand)
        ordered, seen_t = [], set()
        for k in sorted(cand, key=lambda w: (-counts[w], cand.index(w))):
            if k not in seen_t:
                seen_t.add(k)
                ordered.append(k)
        terms = ordered[:3]
        if not terms:
            return ""
        # CS-108 (D-8, owner-measured): put the MARKET in the query TEXT (gl alone can't fix
        # relevance), localize the seed word by the brand's language, and DERIVE the year (a frozen
        # '2026' goes stale). No country evidence -> no market token (honest absence).
        from datetime import datetime
        market = ""
        try:
            from poster.locale import country_name_for
            market = country_name_for(self.gl).replace("the ", "").strip()
        except Exception:  # noqa: BLE001
            market = ""
        seed = "الأكثر رواجًا" if self.hl.startswith("ar") else "trends"
        parts = terms + ([market] if market else []) + [seed, str(datetime.now().year)]
        return " ".join(parts).strip()

    def fetch(self, limit: int = 30) -> list[TrendItem]:
        q = self._query()
        if not q:
            return []
        try:
            from competitor.search_providers import get_default_search_provider
            prov = get_default_search_provider()
            if prov is None:
                return []
            out: list[TrendItem] = []
            # `or None` is load-bearing: `country_of` returns "" (not None) when the evidence
            # carries no country (poster/locale.py:103), and an empty `gl=""` would be a
            # malformed request rather than an honest absence. The provider adds each key only
            # when truthy (search_providers.py:78-81), so no-country is byte-identical to the
            # request this made before D-8.
            for i, hit in enumerate(prov.search(q, num=min(max(1, limit), 15),
                                                gl=self.gl or None,
                                                hl=self.hl or None) or []):
                title = (getattr(hit, "title", "") or "").strip()
                if not title:
                    continue
                out.append(TrendItem(
                    title=title, url=getattr(hit, "link", "") or "", source=self.name,
                    # CS-64: created_ts=None does NOT mean "fresh" — rank_trends treats an unknown
                    # timestamp as OLD (the 48h recency floor), so this paid SERP source is
                    # hard-capped at ~0.8 trend_score. The SERP API carries no publish date; the
                    # rank order (score below) is the only recency signal we actually have.
                    score=float(max(1, 20 - i)), created_ts=None,   # rank-ordered; recency unknown -> aged

                ))
            return out
        except Exception:
            return []


# Consumer-vertical → relevant subreddits. The OLD default (HN + generic Reddit + Dev.to) was
# TECH-skewed, so a jewelry/food/beauty brand got "buttons/AI/compilers" trends (measured on Azza
# Fahmy). We now pick subreddits by the brand's own keywords, and only add the tech feeds (HN,
# Dev.to) for an actually-tech brand.
_VERTICAL_SUBS: dict[tuple, list[str]] = {
    ("jewelry", "jewellery", "ring", "earring", "necklace", "bracelet", "brooch", "cufflink",
     "gold", "diamond", "gem", "luxury"): ["jewelry", "femalefashionadvice", "Luxury"],
    ("fashion", "clothing", "apparel", "wear", "dress", "shoe", "bag", "accessor", "couture",
     "streetwear", "outfit"): ["femalefashionadvice", "malefashionadvice", "streetwear"],
    ("beauty", "cosmetic", "makeup", "skincare", "perfume", "fragrance", "hair", "salon", "nail",
     "spa", "lash"): ["MakeupAddiction", "SkincareAddiction", "beauty"],
    ("food", "restaurant", "cafe", "coffee", "bakery", "kitchen", "meal", "dish", "cuisine",
     "burger", "pizza", "grill", "dessert"): ["food", "FoodPorn", "Cooking"],
    ("fitness", "gym", "workout", "wellness", "nutrition", "supplement", "yoga"):
        ["Fitness", "nutrition", "loseit"],
    ("home", "furniture", "decor", "interior", "kitchenware", "appliance", "bedding"):
        ["InteriorDesign", "HomeDecorating"],
    ("pharmacy", "clinic", "medical", "dental", "doctor", "health", "care", "therapy"):
        ["Health", "pharmacy"],
    ("tech", "software", "app", "saas", "developer", "digital", "platform", "cloud", "code",
     "data", "ai"): ["technology", "gadgets", "programming"],
    ("education", "academy", "course", "training", "school", "learn", "tutor"):
        ["education", "GetMotivated"],
}
_GENERAL_SUBS = ["marketing", "business", "Entrepreneur"]
_TECH_KEYS = ("tech", "software", "app", "saas", "developer", "digital", "platform", "cloud",
              "code", "data", "ai", "startup")


def _kw_hit(key: str, kws: list[str]) -> bool:
    """WORD-level match — a stem key (>=4 chars) matches a token by prefix/containment, a short
    key (e.g. 'ai', 'app') only matches a WHOLE token. Prevents 'ai' matching inside 'keychains'
    (measured bug: a jewelry brand got the tech feeds because 'keychains' contains 'ai')."""
    for kw in kws:
        kw = str(kw).lower()
        if kw == key:
            return True
        if len(key) >= 4 and (kw.startswith(key) or key in kw):
            return True
    return False


def _subreddits_for(keywords: Optional[list[str]]) -> list[str]:
    """Subreddits relevant to the brand's own keywords; a diverse consumer set if nothing matches."""
    kws = [str(k).lower() for k in (keywords or [])]
    subs: list[str] = []
    for keys, sublist in _VERTICAL_SUBS.items():
        if any(_kw_hit(k, kws) for k in keys):
            subs.extend(sublist)
    if not subs:                                            # unknown vertical -> broad consumer mix
        subs = ["femalefashionadvice", "food", "gadgets"]
    subs += _GENERAL_SUBS
    seen: set[str] = set()
    out: list[str] = []
    for s in subs:
        if s.lower() not in seen:
            seen.add(s.lower())
            out.append(s)
    return out[:6]


def default_trend_sources(keywords: Optional[list[str]] = None, *,
                          gl: str = "", hl: str = "") -> list[TrendSource]:
    """Sources tuned to the brand's vertical AND its market. Reddit uses subreddits picked from
    the brand's keywords; the TECH-only feeds (Hacker News, Dev.to) are added ONLY for a tech
    brand.

    `gl`/`hl` (see `SerperTrendSource`) reach only the SERP source — the keyless feeds have no
    country dimension to give. Both default to "" -> no market claim is made.
    """
    kws = [str(k).lower() for k in (keywords or [])]
    # Web search (Serper) FIRST — the reliable, vertical-relevant source — then Reddit (best-effort).
    sources: list[TrendSource] = [
        SerperTrendSource(keywords=keywords, gl=gl, hl=hl),
        RedditSource(subreddits=_subreddits_for(keywords)),
    ]
    # TECH feeds are added ONLY when a tech key is a WHOLE keyword — not a stem-prefix, so
    # "techniques"/"application"/"database" (jewelry/retail words) don't drag in HN/Dev.to.
    if any(k in kws for k in _TECH_KEYS):
        sources += [HackerNewsSource(), DevToSource()]
    return sources
