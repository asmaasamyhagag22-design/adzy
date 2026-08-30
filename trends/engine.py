"""Trend engine — fetch, RANK, and MATCH trending items to a business so a campaign can
ride a CURRENT trend (relevant + fresh), not a generic evergreen angle.

Ranking (v1, no time-series): per-source popularity is normalized (so HN points and
Dev.to reactions are comparable) and combined with a recency decay:

    trend_score = 0.7 * normalized_popularity + 0.3 * recency      (recency = 1 at now, 0.5 at 24h)

Matching: a trend is RELEVANT when its title shares a keyword with the business
(category / offerings / value props). `top_trends` returns matched trends first, each
ranked by trend_score — the campaign picks the strongest on-topic moment.

Pure: sources are injected (defaults are keyless), nothing here mutates global state.
"""
from __future__ import annotations

import logging
import time
from collections import defaultdict
from typing import Any, Optional

from .sources import TrendItem, TrendSource, default_trend_sources

logger = logging.getLogger(__name__)

# Generic words that must never count as a topical match (they match everything). The
# default trend sources (HN / Reddit / Dev.to) are TECH-skewed, so a plain word shared with
# a tech headline is almost always a coincidence — hence the extra cross-domain + tech-
# ambiguous terms below. (Pure-numeric tokens like years are dropped separately.)
_STOPWORDS = {
    "the", "and", "for", "with", "you", "your", "our", "are", "from", "this", "that",
    "new", "best", "top", "how", "why", "what", "online", "service", "services",
    # CS-107 (D-8): NO country name here. 'egypt' used to sit in this list, so an Egyptian brand's
    # own market keyword was silently unmatchable while 'qatar'/'uae' matched — the exact asymmetry
    # D-8 forbids. A country IS a meaningful market signal; it must match like any other keyword.
    "business", "company", "near", "home", "more", "get", "all",
    # generic
    "free", "day", "days", "time", "way", "ways", "make", "made", "use", "using", "world",
    "life", "people", "year", "years", "week", "month", "thing", "things", "good", "great",
    "here", "into", "out", "about", "over", "just", "like", "one", "two", "now", "today",
    # tech-ambiguous (match tech news for ANY business)
    "app", "apps", "tech", "data", "web", "api", "code", "dev", "software", "startup",
    "startups", "digital", "tool", "tools", "update", "updates", "release", "launch",
    "review", "reviews", "guide", "tips", "news", "app",
}


# SERP-noise disclosure (MEASURED 2026-07-15, logs/spend.md:40): repeating the identical query
# minutes apart changed ~66% of returned URLs — one snapshot is a DIRECTIONAL signal, not a
# measurement. Surfaces (dashboard p2) must say so; multi-sampling is a separate design ticket.
NOISE_DISCLOSURE = ("لقطة بحث واحدة — الإشارات دي اتجاهية؛ في قياسنا (2026-07-15) "
                    "تكرار نفس البحث غيّر نحو ثلثي الروابط")


def _norm_url(url: str) -> str:
    from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse
    try:
        u = urlparse(str(url or "").strip())
    except Exception:  # noqa: BLE001
        return str(url or "")
    if not u.netloc:
        return str(url or "").strip().lower()
    q = [(k, v) for k, v in parse_qsl(u.query, keep_blank_values=True)
         if not k.lower().startswith("utm_") and k.lower() not in ("fbclid", "gclid", "ref")]
    return urlunparse((u.scheme.lower(), u.netloc.lower(), u.path.rstrip("/"),
                       u.params, urlencode(q), ""))


def fetch_trends(sources: Optional[list[TrendSource]] = None, *, limit_per_source: int = 30) -> list[TrendItem]:
    """Aggregate items from all sources. A source that fails (down / schema-drifted) is
    logged as a WARNING and contributes nothing — so a silently-empty trend list is
    traceable to which source(s) failed, not mistaken for 'no trends'."""
    sources = sources if sources is not None else default_trend_sources()
    items: list[TrendItem] = []
    seen_urls: set[str] = set()
    failed = 0
    for s in sources:
        try:
            got = s.fetch(limit_per_source) or []
            # CS-63: crossposts/duplicates used to eat top_k slots — ONE normalized-URL
            # slot each (host lowercased, trailing slash + tracking params stripped).
            fresh = []
            for it in got:
                key = _norm_url(getattr(it, "url", ""))
                if key and key in seen_urls:
                    continue
                if key:
                    seen_urls.add(key)
                fresh.append(it)
            got = fresh
            items.extend(got)
            if not got:
                logger.warning("trend source %r returned no items", getattr(s, "name", s))
        except Exception as exc:  # noqa: BLE001 — one bad source must not break the rest
            failed += 1
            logger.warning("trend source %r failed: %s", getattr(s, "name", s), exc)
    if failed and not items:
        logger.warning("ALL %d trend source(s) failed — trend list is empty", failed)
    return items


def rank_trends(items: list[TrendItem], *, now: Optional[float] = None) -> list[TrendItem]:
    """Set each item's `trend_score` (popularity normalized per source + recency) and
    return the list sorted by it, highest first. Mutates the items in place."""
    now = now if now is not None else time.time()
    by_source: dict[str, list[TrendItem]] = defaultdict(list)
    for it in items:
        by_source[it.source].append(it)
    for group in by_source.values():
        mx = max((it.score for it in group), default=0.0) or 1.0
        for it in group:
            pop = max(0.0, it.score) / mx
            age_h = max(0.0, (now - it.created_ts) / 3600.0) if it.created_ts else 48.0
            recency = 1.0 / (1.0 + age_h / 24.0)
            it.trend_score = round(0.7 * pop + 0.3 * recency, 4)
    return sorted(items, key=lambda it: it.trend_score, reverse=True)


def match_to_keywords(items: list[TrendItem], keywords: list[str]) -> list[TrendItem]:
    """Tag each item with the business keywords that appear (whole-word) in its title.
    Mutates `matched_terms` in place and returns the list."""
    import re
    kws = sorted({k.lower().strip() for k in keywords
                  if k and len(k.strip()) >= 3
                  and not k.strip().isdigit()          # a bare year/number is not a topic
                  and k.lower().strip() not in _STOPWORDS},
                 key=len, reverse=True)
    for it in items:
        title = it.title.lower()
        it.matched_terms = tuple(k for k in kws if re.search(r"\b" + re.escape(k) + r"\b", title))
    return items


def brand_identity_tokens(profile: dict[str, Any]) -> set[str]:
    """The brand's OWN name tokens (name words + registrable domain label + glued name).
    Owner ask (2026-07-20 'عايزاها للمجال كله مش منتج بعينه'): a trend seed carrying the brand's
    own name pulls results ABOUT THE BRAND, not the market — these tokens are excluded from the
    seed AND from trend matching so the pulse reads the CATEGORY, never the mirror."""
    import re as _re
    from urllib.parse import urlparse as _up

    def _val(x):
        return x.get("value") if isinstance(x, dict) and "value" in x else x

    toks: set[str] = set()
    name = _val(profile.get("name"))
    if isinstance(name, str) and name.strip():
        words = [w.lower() for w in _re.findall(r"[\w؀-ۿ]+", name) if len(w) >= 3]
        toks |= set(words)
        if len(words) >= 2:
            toks.add("".join(words))                    # "نانو كير" -> "نانوكير"
    src = _val(profile.get("source_url")) or profile.get("final_url") or ""
    host = _up(str(src)).netloc.lower().replace("www.", "")
    label = host.split(".")[0] if host else ""
    if len(label) >= 3:
        toks.add(label)                                  # "nanocareegypt"
        toks.add(_re.sub(r"(egypt|misr|store|shop|online)$", "", label) or label)
    # CROSS-SCRIPT brand detection (the name may be English while products carry the Arabic
    # brand): a token repeated across ≥70% of offering names IS the brand prefix, not a
    # category word ("نانوكير" rides every product; "جلوتاثيون" rides one). Deterministic,
    # language-agnostic; needs ≥4 offerings so tiny catalogs never false-positive.
    offerings = profile.get("offerings") or []
    if len(offerings) >= 4:
        from collections import Counter
        any_pos: list[set] = []      # tokens anywhere in the name
        leading: list[set] = []      # the first TWO tokens — the brand-prefix slot
        for o in offerings:
            n = _val(o.get("name") if isinstance(o, dict) else o)
            if isinstance(n, str) and n.strip():
                ws = [w.lower() for w in _re.findall(r"[\w؀-ۿ]+", n) if len(w) >= 3]
                any_pos.append(set(ws))
                leading.append(set(ws[:2]))
        n_names = len(any_pos) or 1
        counts_any = Counter(t for s in any_pos for t in s)
        counts_lead = Counter(t for s in leading for t in s)
        for t in counts_any:
            # brand = rides ≥70% of names ANYWHERE, or ≥40% of names in the LEADING slot
            # (the brand prefix position — "نانوكير ليبوسومال X" starts with the brand,
            # while a tech/category word like ليبوسومال floats mid-name).
            if counts_any[t] / n_names >= 0.7 or counts_lead.get(t, 0) / n_names >= 0.4:
                toks.add(t)
    return {t for t in toks if len(t) >= 3}


def keywords_from_profile(profile: dict[str, Any]) -> list[str]:
    """Pull topical keywords from a BusinessProfile dict (category + offering names +
    value-prop / tagline words). Best-effort; tolerant of dict-wrapped EvidencedFields.
    The brand's OWN name tokens are EXCLUDED (brand_identity_tokens) — the trend pulse is
    about the MARKET, and a seed carrying the brand name returns the brand itself."""
    def _val(x):
        return x.get("value") if isinstance(x, dict) and "value" in x else x

    out: list[str] = []
    # process.md §8.11: prefer the free-text MARKET ("liposomal supplements") over the conflated
    # `category` ("ecommerce"/"services_b2c" — the business MODEL, useless as a trend seed).
    # Fall back to `category` for un-migrated profiles.
    cat = _val(profile.get("market_category")) or _val(profile.get("category"))
    if isinstance(cat, str):
        out += cat.replace("_", " ").split()
    # Count how many DISTINCT offerings each word rides — that is what separates a product class
    # from a product instance (see the tiering at the end of this function).
    offering_word_counts: dict[str, int] = {}
    for o in (profile.get("offerings") or []):
        name = _val(o.get("name") if isinstance(o, dict) else o)
        if isinstance(name, str):
            out += name.split()
            for w in set(_norm_words(name)):
                offering_word_counts[w] = offering_word_counts.get(w, 0) + 1
    # Which identity FIELDS each word appears in. A word corroborated across >=2 fields is about
    # the business; a word living in one value-prop sentence is usually filler ("provides",
    # "smart", "unique"). Same cross-field rule already proven in web_discovery._identity_terms.
    field_of: dict[str, set[str]] = {}

    def _note(field: str, text: str) -> None:
        for w in _norm_words(text):
            field_of.setdefault(w, set()).add(field)

    if isinstance(cat, str):
        _note("category", cat)
    for o in (profile.get("offerings") or []):
        nm = _val(o.get("name") if isinstance(o, dict) else o)
        if isinstance(nm, str):
            _note("offerings", nm)
    desc = _val(profile.get("description"))
    if isinstance(desc, str):
        _note("description", desc)

    business_words: set[str] = set()
    for vp in (profile.get("value_propositions") or [])[:5]:
        # A value_proposition is an EvidencedField -> its text is under 'value' (NOT
        # 'text'); _val unwraps {'value': ...} and passes a plain string through.
        v = _val(vp)
        if isinstance(v, str):
            out += v.split()
            _note("value_props", v)
    tagline = _val(profile.get("tagline"))
    if isinstance(tagline, str):
        out += tagline.split()
        _note("tagline", tagline)
    # Normalize: strip punctuation, drop short/stopwords + the brand's OWN tokens, dedupe
    # (order-preserving). Excluding the brand keeps the pulse on the MARKET, not the mirror.
    brand = brand_identity_tokens(profile)
    # ...EXCEPT the evidenced CATEGORY. MEASURED (Volume Qatar): the trade name "Volume Cafe" put
    # `cafe` into the brand-token set, so the one word that says what the business IS was deleted
    # from its own market pulse. Same for "X Pharmacy" / "Y Jewelry" / "Z Bakery" — a whole class.
    cat_words = {w for w in _norm_words(cat if isinstance(cat, str) else "")}
    brand = {b for b in brand if b not in cat_words}

    seen: set[str] = set()
    cleaned: list[str] = []
    for w in out:
        w = "".join(ch for ch in w if ch.isalnum() or ch in "-&").strip("-&").lower()
        if len(w) >= 3 and not w.isdigit() and w not in _STOPWORDS and w not in seen \
                and w not in brand:
            seen.add(w)                                 # drop bare years/numbers too
            cleaned.append(w)
    # MARKET FIRST. The consumer truncates (`trends/sources.py` takes the first three), and profile
    # order puts one dish's ingredients ahead of the category — the measured query was
    # "catering tomato basil". Ordering, NOT filtering: product terms stay (they carry real signal
    # and tests/test_trends_batch3.py rightly pins them present), they just stop crowding out the
    # market. Deterministic and stable: a partition, original order kept inside each tier.
    #
    #   tier 1  the evidenced category — what the business IS
    #   tier 2  terms that RECUR across offerings (a class: "ليبوسومال" rides 4 of 5 products) plus
    #           value-prop / tagline words, which describe the business rather than one item
    #   tier 3  terms tied to a single offering — a product INSTANCE ("tomato", "glutathione")
    #
    # The recurrence rule is the universal one: a word on many offerings names the kind of thing
    # sold; a word on exactly one names that one thing.
    recurring = {w for w, n in offering_word_counts.items() if n >= 2}
    corroborated = {w for w, fields in field_of.items() if len(fields) >= 2}
    tier2 = recurring | corroborated

    def _tier(w: str) -> int:
        if w in cat_words:
            return 1
        return 2 if w in tier2 else 3

    return sorted(cleaned, key=lambda w: (_tier(w), cleaned.index(w)))


def _norm_words(text: str) -> list[str]:
    """The same normalization the keyword cleaner applies, so category words compare equal."""
    words = []
    for w in str(text or "").replace("_", " ").split():
        w = "".join(ch for ch in w if ch.isalnum() or ch in "-&").strip("-&").lower()
        if len(w) >= 3 and not w.isdigit() and w not in _STOPWORDS:
            words.append(w)
    return words


def top_trends(
    keywords: list[str],
    *,
    sources: Optional[list[TrendSource]] = None,
    limit_per_source: int = 30,
    top_k: int = 10,
    require_match: bool = False,
    now: Optional[float] = None,
    gl: str = "",
    hl: str = "",
) -> list[TrendItem]:
    """Fetch → rank → match, then return the strongest trends with on-topic ones FIRST.
    `require_match=True` drops trends that share no keyword with the business.

    `gl`/`hl` (D-8, 2026-07-15) carry the brand's MARKET into the SERP — see
    `trends.sources.SerperTrendSource`. Resolve them at the call site with
    `poster.locale.search_locale(profile)`; both default to "" -> no market claim is made and
    the request is byte-identical to the pre-D-8 one. Ignored when `sources` is supplied
    explicitly (the caller has already chosen what to ask).
    """
    if sources is None:                                    # pick sources by the brand's vertical
        sources = default_trend_sources(keywords=keywords, gl=gl, hl=hl)
    items = rank_trends(fetch_trends(sources, limit_per_source=limit_per_source), now=now)
    match_to_keywords(items, keywords)
    if require_match:
        items = [it for it in items if it.matched_terms]
    items.sort(key=lambda it: (len(it.matched_terms), it.trend_score), reverse=True)
    return items[: max(0, top_k)]


def top_trends_bounded(keywords: list[str], *, timeout_s: float = 12.0, **kwargs) -> list[TrendItem]:
    """`top_trends` with a HARD wall-clock deadline. Trend sources do live network I/O, so a slow or
    hanging source must NEVER block the caller (e.g. the SWOT analysis) — a miss just degrades to []
    (the SWOT already handles empty trends). Runs the fetch on a daemon thread and abandons it if it
    overruns; returns [] on timeout or any error."""
    import threading
    box: dict = {"items": []}

    def _work():
        try:
            box["items"] = top_trends(keywords, **kwargs)
        except Exception:
            box["items"] = []

    t = threading.Thread(target=_work, daemon=True)
    t.start()
    t.join(timeout_s)
    return box["items"] if not t.is_alive() else []
