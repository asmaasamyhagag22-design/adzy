"""SERP-based competitor discovery for ECOMMERCE / online businesses.

Implements the router's `WebDiscoveryEngine` Protocol. For businesses the Places
(LOCAL) path can't reach, it finds market peers via a pluggable web-search
provider (search_providers.py) and returns them as `CompetitorProfile` objects —
evidence-grounded exactly like the Places path:

  * Every peer carries the QUERY + organic RANK that surfaced it (SelectionRecord.
    why_selected), so "why this peer" is always answerable.
  * Only real, scrapable domains survive: social profiles (Facebook/Instagram/...)
    and aggregators/marketplaces/review-sites (Amazon, Yelp, Wikipedia, G2, ...)
    are dropped — they are not direct competitors.
  * The subject's own domain is excluded.

It NEVER fabricates a peer. No provider, an empty result, or an all-filtered
result yields [] and the SWOT layer degrades to a grounded standalone analysis.
"""
from __future__ import annotations

import logging
import re
from typing import List, Optional
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

from .discovery import _get, _is_scrapable_site, _offering_name
from .models import (
    Candidate,
    CompetitorProfile,
    PeerFitBreakdown,
    SelectionRecord,
)
from .peer_match import TARGET_PEERS, expand_bilingual, tokenize
from .search_providers import SearchHit, SearchProvider, get_default_search_provider


def _field_text(profile, key: str) -> str:
    """Robust scalar read: handles object profiles (EvidencedField.value, enums)
    AND serialized dict profiles ({"value": ...}). Returns "" when absent."""
    val = _get(profile, key)
    if val is None:
        return ""
    if isinstance(val, dict):               # dict-wrapped EvidencedField
        val = val.get("value")
    val = getattr(val, "value", val)        # enum -> its .value
    return str(val).strip() if val else ""

# Directories / marketplaces / review aggregators / "X competitors" listicle &
# company-data sites / forums — never direct competitors. (Expanded after a live
# measurement: brand queries surfaced craft.co, gripsintelligence, weddingbee.)
_AGGREGATOR_HOSTS = {
    # marketplaces / encyclopedias / directories
    "wikipedia.org", "yelp.com", "tripadvisor.com", "yellowpages.com",
    "amazon.com", "ebay.com", "etsy.com", "alibaba.com", "aliexpress.com",
    "noon.com", "jumia.com", "walmart.com", "target.com",
    # social / generic platforms
    "crunchbase.com", "glassdoor.com", "indeed.com", "linkedin.com",
    "pinterest.com", "reddit.com", "quora.com", "medium.com", "youtube.com",
    "google.com", "bing.com", "apple.com", "play.google.com", "facebook.com",
    "instagram.com", "tiktok.com", "x.com", "twitter.com",
    # review / company-data / competitor-listicle / analytics sites
    "clutch.co", "g2.com", "capterra.com", "trustpilot.com", "producthunt.com",
    "craft.co", "owler.com", "gripsintelligence.com", "similarweb.com",
    "semrush.com", "ahrefs.com", "statista.com", "zoominfo.com", "6sense.com",
    "growjo.com", "datanyze.com", "enlyft.com", "slintel.com", "rocketreach.co",
    "comparably.com", "wappalyzer.com", "builtwith.com",
    # forums / Q&A / blog farms
    "weddingbee.com", "trustpilot.co.uk", "sitejabber.com", "knoji.com",
    # directories (locale variants — the eTLD+1 fix makes these matchable)
    "yellowpages.com.eg", "topuniversities.com", "yell.com", "dellooni.com",
}

# Real news/media + "best X of YYYY" listicle publishers. These are NOT competitors
# (measured live: 'best pharmacy health' surfaced forbes.com + usnews.com as a
# pharmacy's "peers") — but they ARE reputable EVIDENCE sources, and the Ledger's
# `_is_reputable_source` reuses _AGGREGATOR_HOSTS as its junk list. Two different
# semantics -> two sets: the PEER filter checks both; the reputability check must
# keep trusting reuters/forbes-class citations.
_MEDIA_LISTICLE_HOSTS = {
    "forbes.com", "usnews.com", "nytimes.com", "wsj.com", "bloomberg.com",
    "reuters.com", "cnn.com", "bbc.com", "theguardian.com", "businessinsider.com",
    "buzzfeed.com", "healthline.com", "webmd.com", "medicalnewstoday.com",
    "verywellhealth.com", "investopedia.com", "nerdwallet.com", "top10.com",
    "techradar.com", "tomsguide.com", "cnet.com", "wirecutter.com",
}


def _registrable(host: Optional[str]) -> Optional[str]:
    """Real eTLD+1 (bundled PSL via scraper.url_utils). The old last-two-labels
    guess collapsed EVERY .com.eg site to "com.eg" — so a second Egyptian peer was
    silently deduped away and denylisted hosts like yellowpages.com.eg never
    matched. Falls back to the naive form only when the PSL can't resolve."""
    if not host:
        return None
    host = host.split(":")[0].removeprefix("www.")
    try:
        from scraper.url_utils import _registrable_domain
        reg = _registrable_domain(f"https://{host}/")
        if reg:
            return reg
    except Exception:  # noqa: BLE001
        pass
    parts = [p for p in host.split(".") if p]
    return ".".join(parts[-2:]) if len(parts) >= 2 else host or None


def _is_aggregator(host: Optional[str]) -> bool:
    reg = _registrable(host)
    return bool(reg) and (reg in _AGGREGATOR_HOSTS or reg in _MEDIA_LISTICLE_HOSTS)


_DROP_ITEMS = ["listicles/rankings", "directories", "news/media", "universities/courses",
               "software or tools FOR that industry", "marketplaces"]
_SELF_KIND = {
    # subject-kind key -> (the DROP clause that names its own kind, the KEEP line replacing it)
    "education": ("universities/courses",
                  "The subject IS an education/training brand — institutes, academies, course "
                  "platforms and training providers ARE its direct competitors: KEEP them. "),
    "software": ("software or tools FOR that industry",
                 "The subject IS a software/tech brand — software products and tools serving "
                 "the same users ARE its direct competitors: KEEP them. "),
}
_KIND_WORDS = {
    "education": ("education", "training", "academy", "institute", "school", "course"),
    "software": ("software", "saas", "tech", "it service", "app", "platform"),
}


def _self_kind_key(category) -> Optional[str]:
    """Which DROP clause names the subject's OWN kind ('' -> None). 1.2 (D-8): a filter tuned to
    reject schools backfires when the subject IS a school — same for software; the exclusion has
    to travel with the subject's category, not live hardcoded in the prompt."""
    cat = str(category or "").lower()
    for key, words in _KIND_WORDS.items():
        if any(w in cat for w in words):
            return key
    return None


def _judge_system_prompt(category) -> str:
    """The relevance judge's system prompt, with the DROP list built dynamically: the clause
    naming the subject's own kind is lifted (and replaced by an explicit KEEP line). Every other
    category reproduces the legacy prompt BYTE-IDENTICAL (pinned by test)."""
    items = list(_DROP_ITEMS)
    keep_line = ""
    kind = _self_kind_key(category)
    if kind:
        clause, keep_line = _SELF_KIND[kind]
        items.remove(clause)
    return (
        "You filter competitor candidates for a business-comparison report. Keep ONLY "
        "candidates that are plausibly a DIRECT COMPETITOR — an actual operating business "
        "of the SAME KIND as the subject, in or serving the subject's market. DROP: "
        + ", ".join(items) + ", and anything unrelated. " + keep_line
        + "When unsure, DROP "
        "(a wrong competitor is worse than a missing one). Never add anything."
    )


def _subject_registrable(profile) -> Optional[str]:
    for key in ("final_url", "source_url", "website", "url"):
        val = _get(profile, key)
        if val:
            return _registrable(urlparse(str(val)).netloc or str(val))
    return None


_TITLE_SEPS = (" | ", " - ", " – ", " — ", ": ")


def _clean_title(title: str, host: Optional[str]) -> str:
    """The competitor's BRAND name — not the SEO phrase that happened to rank.

    MEASURED (nanocare, 2026-07-21): taking the text before the first separator listed real
    businesses as "أفضل مكمل غذائي في مصر 2026" (inshapesupplements.com) and "متجر مكملات غذائية
    في مصر" (beseha.com). On an SEO-optimised page the first segment IS the keyword phrase; the
    brand sits after the separator, or only in the domain.

    Keyed on the DOMAIN rather than a banned-words list, so it holds in any language: prefer the
    title segment that shares a token with the registrable domain, else prettify the domain.
    """
    reg = _registrable(host) or ""
    label = reg.split(".")[0] if reg else ""
    squashed = re.sub(r"[^a-z0-9]", "", label.lower())

    segments = [title]
    if title:
        for sep in _TITLE_SEPS:
            if sep in title:
                segments = [s.strip() for s in title.split(sep)]
                break
    segments = [s for s in (x.strip() for x in segments) if s]

    if squashed:
        for seg in segments:
            if re.sub(r"[^a-z0-9]", "", seg.lower()) and \
                    (squashed in re.sub(r"[^a-z0-9]", "", seg.lower())
                     or re.sub(r"[^a-z0-9]", "", seg.lower()) in squashed):
                return seg                      # this segment names the brand the domain belongs to
        # No segment matches the domain -> the title is pure SEO copy. The domain is the only
        # honest brand signal we have: "inshapesupplements" -> "Inshapesupplements".
        return label.replace("-", " ").strip().title() or (reg or "competitor")
    return (segments[0] if segments else "") or (reg or "competitor")


def _identity_terms(profile) -> List[str]:
    """The subject's CORROBORATED identity terms — what the business IS.

    MEASURED failure this fixes (El Ezaby Pharmacy): the query was built from the
    first offering-name tokens ("One Minute Clinic", "Basic Health Advice" ->
    'best one minute basic') and Google returned a MATH TEST as a "competitor" —
    while the word that actually identifies the business ("pharmacy") sat unused
    in the name, the domain and the description.

    A token qualifies only when it appears in >=2 DISTINCT identity fields
    ({name, domain, category, tagline, description, offerings}) — cross-field
    corroboration, same spirit as the Places brand-unanimity match. Tokens whose
    only support is name+domain are excluded (pure brand words like "ezaby":
    fine in a brand query, wrong in a category query). Ranked by field count."""
    name = _field_text(profile, "name")

    def _sans_name_echo(text: str) -> str:
        """Descriptions routinely restate the brand name verbatim ('El Ezaby Pharmacy
        serves...') — that echo would fake cross-field corroboration for brand words.
        Strip the name phrase before tokenizing."""
        if name and text:
            import re as _re
            return _re.sub(_re.escape(name), " ", text, flags=_re.IGNORECASE)
        return text

    fields: dict[str, set] = {
        "tagline": set(tokenize(_sans_name_echo(_field_text(profile, "tagline")))),
        "description": set(tokenize(_sans_name_echo(_field_text(profile, "description")))),
        "category": set(tokenize(_field_text(profile, "category").replace("_", " "))),
        "offerings": set(tokenize(" ".join(
            n for n in (_offering_name(o) for o in (_get(profile, "offerings", default=[]) or []))
            if n))),
    }
    domain = (_subject_registrable(profile) or "").lower()
    name_squash = "".join(tokenize(name))    # 'elezaby' — catches spelling variants

    # Corroborated across fields, with guards against ECHO corroboration (measured
    # on the real El Ezaby profile):
    #  - a token that is a SUBSTRING of the brand name is a brand word, not an
    #    identity word ('ezaby' ⊂ 'elezaby'), whatever its support;
    #  - description+offerings alone is NOT corroboration (the description narrates
    #    the offerings: 'One Minute Clinic' echoed 'minute' into both) — support
    #    must include category, tagline or the DOMAIN (the brand's own naming).
    # Function words that survive tokenize but identify nothing ("YOUR Gateway to
    # Better Health" corroborated 'your' via tagline+description).
    _function_words = {"your", "our", "their", "his", "her", "its", "you", "they",
                       "with", "from", "that", "this", "all", "more", "best", "our"}

    scored: List[tuple[int, int, str]] = []
    for token in set().union(*fields.values()):
        if token in _function_words:
            continue
        if len(token) >= 3 and token in name_squash:
            continue
        support = {f for f, toks in fields.items() if token in toks}
        if token.isascii() and len(token) >= 4 and token in domain:
            support.add("domain")
        if len(support) < 2 or not (support & {"category", "tagline", "domain"}):
            continue
        scored.append((1 if "domain" in support else 0, len(support), token))
    scored.sort(key=lambda x: (-x[0], -x[1], x[2]))
    if scored:
        return [t for _d, _n, t in scored][:4]

    # Fallback: the name's EDGE token when the domain confirms it — the category-
    # noun position in real brand names ("El Ezaby PHARMACY" / "مطعم قصر الكبابجي"),
    # e.g. 'pharmacy' from elezabypharmacy.com.
    name_toks = tokenize(name)
    for token in (name_toks[-1:] + name_toks[:1]):
        if len(token) >= 4 and token.isascii() and token in domain and token != name_squash:
            return [token]
    return []


_CCTLD_MARKET = {
    ".eg": "Egypt", ".sa": "Saudi Arabia", ".ae": "UAE", ".kw": "Kuwait",
    ".qa": "Qatar", ".jo": "Jordan", ".ma": "Morocco", ".tn": "Tunisia",
    ".tr": "Turkey", ".uk": "UK", ".de": "Germany", ".fr": "France",
    ".it": "Italy", ".es": "Spain", ".nl": "Netherlands", ".in": "India",
}


def _market_hint(profile) -> str:
    """The subject's MARKET (country/region) for the category query — without it an
    Egyptian pharmacy got US peers (GoodRx, a NYC pharmacy; measured live). Sources,
    strongest first: the profile's evidence-grounded service_areas, then the ccTLD.
    "" when unknown (query stays unanchored — honest)."""
    areas = _get(profile, "service_areas", default=[]) or []
    for a in areas:
        v = a.get("value") if isinstance(a, dict) else getattr(a, "value", a)
        v = str(v or "").strip()
        if v and len(v) <= 40:
            return v
    url = str(_get(profile, "source_url") or _get(profile, "website") or "").lower()
    host = urlparse(url).netloc if url else ""
    for cc, market in _CCTLD_MARKET.items():
        if host.endswith(cc):
            return market
    return ""


class SerpWebDiscoveryEngine:
    """Find online competitors via web search. Satisfies WebDiscoveryEngine."""
    name = "serp-web"

    def __init__(
        self,
        provider: Optional[SearchProvider] = None,
        *,
        max_peers: int = TARGET_PEERS,
        results_per_query: int = 10,
        judge_caller: Optional[object] = None,
    ):
        # Honor an explicit provider=None as "no provider -> discover() returns []"
        # (the documented contract + the guard in discover()). The production factory
        # default_web_engine() resolves the default provider externally, so the
        # constructor must NOT silently go live on None (that made the no-provider
        # path hit the real network whenever a SERP key was in the environment).
        self.provider = provider
        self.max_peers = max_peers
        self.results_per_query = results_per_query
        # Optional LLM relevance JUDGE (reject-only): 'best pharmacy Egypt' still
        # surfaced pharmacy-SOFTWARE listicles / university rankings / directories
        # (three DIFFERENT junk shapes in one measured day) — deterministic
        # denylists can't keep up. The judge can only DROP a candidate, never add
        # or invent one; on any error every candidate is kept (old behavior).
        self.judge_caller = judge_caller

    # -- query construction (universal: brand-similarity + category) -----------
    def _queries(self, profile) -> tuple[List[str], Optional[str]]:
        # Robust to object AND dict profiles; never trusts category enum repr.
        brand = _field_text(profile, "name")
        category = _field_text(profile, "category")
        offerings = _get(profile, "offerings", default=[]) or []
        kw_source = " ".join(
            [n for n in (_offering_name(o) for o in offerings) if n]
            + ([category] if category else [])
        )
        # English head terms drive web search; bilingual variants stay available.
        kws = [k for k in expand_bilingual(list(dict.fromkeys(tokenize(kw_source))))
               if k and k.isascii()][:3]
        languages = [str(l).lower()[:2] for l in (_get(profile, "languages", default=[]) or [])]
        hl = languages[0] if languages else None

        # Category/offering query FIRST — it returns real businesses in the space.
        # Brand "competitors/alternatives" queries are secondary: they tend to
        # surface listicle/data-aggregator pages (filtered below), but also
        # occasionally a genuine rival, so we keep them as a supplement.
        # The query is ANCHORED on the corroborated IDENTITY terms (what the
        # business IS — e.g. "pharmacy") with offering tokens only as specificity;
        # raw offering-token soup alone produced a garbage query and unrelated
        # "peers" (measured: El Ezaby -> 'best one minute basic' -> a math test).
        queries: List[str] = []
        market = _market_hint(profile)
        id_terms = [t for t in _identity_terms(profile) if t.isascii()][:3]
        if id_terms:
            queries.append(("best " + " ".join(id_terms) + f" {market}").strip())
        elif kws:
            queries.append(("best " + " ".join(kws) + f" {market}").strip())
        elif category:
            queries.append(f"top {category} {market}".strip())
        if brand:
            queries.append(f"{brand} competitors")
            queries.append(f"{brand} alternatives")
        # de-dup while preserving order
        seen_q, ordered = set(), []
        for q in queries:
            if q and q not in seen_q:
                seen_q.add(q)
                ordered.append(q)
        return ordered, hl

    # -- LLM query planner (bounded; never fabricates a peer) ------------------
    def _plan_queries_llm(self, profile) -> Optional[dict]:
        """ONE cheap LLM call that writes the search a real CUSTOMER would type to
        find businesses LIKE this one — in the right LANGUAGE and MARKET.

        The deterministic cross-field heuristic hit its ceiling (measured 3 shapes in
        3 sites): on nahdi (Arabic brand name + generic 'ecommerce' category + the
        identity words living only in the description) it degraded to 'best nahdi' —
        a brand query that returns only review/aggregator pages -> 0 peers. Guards
        unchanged: the denylist, the relevance gate, the reject-only judge and the
        never-fabricate contract all still apply to whatever this returns. None on
        any failure -> the deterministic path."""
        if self.judge_caller is None:
            return None
        try:
            from pydantic import BaseModel

            class _Plan(BaseModel):
                queries: list[str] = []          # 1-3 category searches, customer language
                identity_terms: list[str] = []   # what the business IS (all its languages)
                market: str = ""

            name = _field_text(profile, "name")
            desc = _field_text(profile, "description")[:400]
            tagline = _field_text(profile, "tagline")
            category = _field_text(profile, "category")
            offers = ", ".join(
                n for n in (_offering_name(o) for o in (_get(profile, "offerings", default=[]) or [])[:6]) if n)
            langs = ", ".join(str(l) for l in (_get(profile, "languages", default=[]) or []))
            system = (
                "You plan web searches to find DIRECT COMPETITORS of a business. Return 1-3 "
                "'queries' a real CUSTOMER in the business's market would type to find businesses "
                "of the SAME KIND (category searches like 'best online pharmacy Saudi Arabia' or "
                "'أفضل صيدلية اون لاين السعودية') — in the market's own language when appropriate. "
                "Determine 'market' (country/region) from ANY evidence: the URL's ccTLD or locale "
                "path (/ar-sa = Saudi Arabia, /en-eg = Egypt), currency, addresses, or wording — "
                "and INCLUDE the market in every query when known (otherwise results come from the "
                "wrong country). NEVER include the brand's own name in a query. Also return "
                "'identity_terms': the words that name what the business IS (its kind, in every "
                "language it uses — e.g. pharmacy, صيدلية) — never brand names. Base everything "
                "ONLY on the given facts; invent nothing."
            )
            homepage = str(_get(profile, "source_url") or _get(profile, "website") or "")
            user = (f"Business: {name}\nHomepage: {homepage}\nCategory: {category}\n"
                    f"Tagline: {tagline}\nDescription: {desc}\n"
                    f"Sample offerings: {offers}\nSite languages: {langs}")
            resp, _u = self.judge_caller(system, user, _Plan, group_name="peer_query_plan")
            queries = [str(q).strip() for q in (resp.queries or []) if str(q).strip()][:3]
            terms = [str(t).strip().lower() for t in (resp.identity_terms or []) if str(t).strip()]
            # Guard: drop any planned query that leaked the brand name.
            brand_toks = [t for t in tokenize(name) if len(t) >= 3]
            queries = [q for q in queries
                       if not any(bt in q.lower() for bt in brand_toks)]
            if not queries:
                return None
            return {"queries": queries, "identity_terms": terms,
                    "market": str(resp.market or "").strip()}
        except Exception:  # noqa: BLE001 — planner failure -> deterministic path
            return None

    # -- WebDiscoveryEngine.discover ------------------------------------------
    def discover(self, profile, manifest=None) -> List[CompetitorProfile]:
        if self.provider is None:
            return []
        queries, hl = self._queries(profile)
        plan = self._plan_queries_llm(profile)
        if plan:
            # Planned CATEGORY queries lead; the deterministic brand-competitors
            # queries stay as the supplement (dedup preserves order).
            merged = plan["queries"] + [q for q in queries if q not in plan["queries"]]
            queries = merged
        if not queries:
            return []

        subject = _subject_registrable(profile)
        # RELEVANCE GATE terms: a SERP hit only becomes a "competitor" if its
        # title/snippet/URL shares at least one corroborated identity term with
        # the subject. Without this, a literal query echo shipped a MATH TEST as
        # a pharmacy's peer — a fabricated comparison is worse than none (rule 4).
        # Empty identity terms -> no gate (old behavior; better under-filter than
        # invent a filter from nothing).
        gate = {t for t in expand_bilingual(_identity_terms(profile))}
        if plan and plan["identity_terms"]:
            gate |= set(expand_bilingual(plan["identity_terms"]))
        # 1.2: a REACH self-kind subject (education/software) with a THIN identity gate (<2
        # terms) was losing real institute/tool results to a single-token echo test — the
        # LLM judge downstream is the real filter there, so don't pre-drop on a thin gate.
        if len(gate) < 2 and _self_kind_key(_field_text(profile, "category")):
            gate = set()
        seen: set[str] = set()
        peers: List[CompetitorProfile] = []

        # Over-collect, then judge: the LLM relevance filter (reject-only) needs a
        # pool to prune, so gather up to ~2x the target before trimming.
        pool_cap = self.max_peers * 2 + 2 if self.judge_caller is not None else self.max_peers
        for query in queries:
            hits = self.provider.search(query, num=self.results_per_query, hl=hl)
            for hit in hits:
                peer = self._to_peer(hit, query, subject, seen, gate)
                if peer is not None:
                    peers.append(peer)
                    if len(peers) >= pool_cap:
                        break
            if len(peers) >= pool_cap:
                break
        peers = self._judge_relevance(profile, peers)
        return peers[: self.max_peers]

    def _judge_relevance(self, profile, peers: List[CompetitorProfile]) -> List[CompetitorProfile]:
        # NOTE: the system prompt is built by the module-level _judge_system_prompt(category)
        # (1.2): the DROP list lifts the clause naming the subject's OWN kind, so an education
        # subject keeps institutes and a software subject keeps tools. Other categories get the
        # legacy prompt byte-identical (pinned by test).
        """LLM reject-only filter: keep candidates that are plausibly DIRECT competitors
        (an actual business of the subject's kind) — drop listicles, directories,
        software-for-the-industry, schools, news. Never adds/invents; on no caller or
        any error, all candidates are kept (graceful old behavior)."""
        if self.judge_caller is None or not peers:
            return peers
        try:
            from pydantic import BaseModel

            class _Kept(BaseModel):
                # REQUIRED (no default): if the model OMITS the field, validation fails and
                # the `except` keeps all candidates — so an omitted judgment is not silently
                # read as pydantic's default [] and used to drop EVERY real peer. An explicit
                # empty list is still a genuine "keep none" (reject-only, drop-when-unsure).
                keep_indices: list[int]

            name = _field_text(profile, "name")
            category = _field_text(profile, "category")
            desc = _field_text(profile, "description")[:300]
            lines = "\n".join(
                f"[{i}] {p.candidate.name} | {p.candidate.website} | {p.selection.why_selected}"
                for i, p in enumerate(peers))
            system = _judge_system_prompt(category)
            user = (f"Subject: {name} — category: {category}.\n{desc}\n\n"
                    f"Candidates:\n{lines}\n\nReturn keep_indices (subset of the shown indices).")
            resp, _u = self.judge_caller(system, user, _Kept, group_name="peer_relevance")
            keep = {i for i in (resp.keep_indices or []) if 0 <= i < len(peers)}
            kept = [p for i, p in enumerate(peers) if i in keep]
            if peers and not kept:
                # A genuine "keep none" is valid (reject-only), but surface it so an empty
                # competitor set is traceable to the judge, not mistaken for "found nothing".
                logger.warning("peer-relevance judge dropped ALL %d candidate(s)", len(peers))
            return kept
        except Exception:  # noqa: BLE001 — the judge must never break discovery
            return peers

    def _to_peer(
        self, hit: SearchHit, query: str, subject: Optional[str], seen: set,
        gate: Optional[set] = None,
    ) -> Optional[CompetitorProfile]:
        host = urlparse(hit.link).netloc
        reg = _registrable(host)
        if not reg or reg in seen:
            return None
        if subject and reg == subject:
            return None
        if not _is_scrapable_site(hit.link) or _is_aggregator(host):
            return None
        if gate:
            blob = f"{hit.title} {hit.snippet} {hit.link}".lower()
            if not any(t in blob for t in gate):
                return None                    # unrelated to what the business IS
        seen.add(reg)

        # Benchmark the competitor's HOMEPAGE, not the deep page that ranked
        # (e.g. a "/blogs/...-vs-us" comparison post). The matrix scrapes this URL.
        parsed = urlparse(hit.link)
        home = f"{parsed.scheme or 'https'}://{parsed.netloc}/"

        name = _clean_title(hit.title, host)
        # P-80: a web/SERP peer has NO Places data (no rating/address/types/price), so the 5 fit
        # sub-dimensions (sub_vertical/proximity/size/audience/language) genuinely CANNOT be
        # computed here — unlike the Places path (peer_match._aggregate). So we score by SERP rank
        # and label it HONESTLY as `basis="search_rank"` rather than emitting a 1.0 "fit" with an
        # all-null breakdown that downstream reads as a measured/validated fit (the P-30 artifact).
        rank_score = round(1.0 / hit.rank, 3) if hit.rank else 0.0
        why = (f"web search peer (rank {hit.rank}) for query: {query!r} "
               f"— score is SERP rank, not a measured fit")
        candidate = Candidate(
            place_id="",                # no Places id for a web-sourced peer
            name=name,
            website=home,
            formatted_address=None,
        )
        selection = SelectionRecord(
            place_id="",
            name=name,
            website=home,
            peer_fit_score=rank_score,
            breakdown=PeerFitBreakdown(total=rank_score, basis="search_rank"),
            why_selected=why,
        )
        return CompetitorProfile(
            candidate=candidate,
            selection=selection,
            reviews=[],
            has_scrapable_site=True,    # filtered to real scrapable domains above
            is_local=False,             # web-sourced benchmark, not a local peer
        )


def default_web_engine() -> "SerpWebDiscoveryEngine | None":
    """Production factory: a live SERP engine when a search key is configured,
    else None (caller falls back to the router's NullWebDiscoveryEngine).
    Wires the cheap Gemini relevance judge when available (reject-only; the
    engine works judge-less without it)."""
    provider = get_default_search_provider()
    if provider is None:
        return None
    judge = None
    try:
        from business_profile.llm import default_caller
        judge = default_caller(strong=False)
    except Exception:  # noqa: BLE001
        judge = None
    return SerpWebDiscoveryEngine(provider, judge_caller=judge)
