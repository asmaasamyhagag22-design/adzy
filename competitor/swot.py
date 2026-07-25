"""SWOT synthesizer.

Derives the SWOT *mechanically* from the Comparative Gap Matrix so every point is
traceable to a sourced cell — no free-form generation. Mapping:

    ahead  on any dimension            -> Strength    (you beat peers)
    behind on a SCRAPED capability     -> Weakness    (internal, fixable)
    behind on a PLACES dimension        -> Threat      (market position vs peers)
    whitespace                          -> Opportunity (gap across you + peers)

Review themes (optional, supplied by the theme extractor you build next) augment:
    complaint theme common to peers     -> Threat (a risk you share) or, if marked
                                           an unmet need, an Opportunity
    praise theme common to peers         -> context for Threats

Each SWOTItem carries `citation` (the sources) and `evidence` (the underlying
detail), which is what you show in the defense.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

from .matrix import ComparativeGapMatrix, DimensionGap, DIMENSIONS
from .swot_priority import Priority, score_gap, score_opinion_item


@dataclass
class ReviewTheme:
    """A theme extracted from customer reviews (peer themes from competitor/themes.py,
    own-brand + peer ABSA themes from reviews/absa.py)."""
    polarity: str                       # "praise" | "complaint"
    text: str                           # e.g. "long wait times"
    support: List[str] = field(default_factory=list)   # citations (which peers / how many reviews)
    is_unmet_need: bool = False         # True => treat as an Opportunity rather than a Threat
    is_own_brand: bool = False          # R-5: own voice -> S/W; peer voice -> O/T


@dataclass
class SWOTItem:
    text: str
    citation: List[str]                 # sources backing this point
    evidence: str                       # the underlying matrix detail / theme
    # Epistemic honesty ladder (adopted from the team's BI platform, computed
    # DETERMINISTICALLY here — never by the LLM):
    #   validated                 — compared against >=2 peers with known values
    #                               (or a multi-peer review theme)
    #   directional_not_validated — a thin comparison (single peer / single-peer theme);
    #                               treat as a signal, not a confirmed verdict
    #   internally_supported      — grounded in the subject's own site only
    #                               (standalone mode, unique insights)
    claim_strength: str = "internally_supported"
    # WHERE THIS FACTOR SITS ON THE TWO MATRICES Pickton & Wright (1998) prescribe — importance x
    # performance for S/W, probability x impact for O/T. Computed deterministically in
    # `swot_priority`, like `claim_strength` above and never by the LLM. None only for an item
    # built by a path that has not been taught to score yet.
    priority: Optional["Priority"] = None
    # A STABLE handle. TOWS used to derive S1/W2/O1/T3 from list POSITION, so sorting the quadrants
    # — the whole point of adding priorities — silently re-pointed every strategy at a different
    # factor. The id travels with the item instead.
    id: str = ""


@dataclass
class SWOT:
    strengths: List[SWOTItem] = field(default_factory=list)
    weaknesses: List[SWOTItem] = field(default_factory=list)
    opportunities: List[SWOTItem] = field(default_factory=list)
    threats: List[SWOTItem] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)
    mode: str = "competitive"           # "competitive" | "standalone"
    # Website mechanics — CTA counts, WhatsApp presence, social channel counts. True, useful, and
    # NOT strategy: stated without a comparison they are the paper's "factors described broadly:
    # lack of specificity" (broad compared to what?). The owner's ruling was to keep the
    # information and move it out of the strategic quadrants, so it lives here.
    site_readiness: List[SWOTItem] = field(default_factory=list)



# ---------------------------------------------------------------------------
# Derived threats + per-quadrant honesty (owner ruling, 2026-07-21)
# ---------------------------------------------------------------------------

# Signals that the CATEGORY is drawing money and attention — which means new entrants, whoever
# writes the headline and in whatever language. Deliberately not another English keyword list:
# `_TREND_THREAT_KEYS` is 16 English words, so "Qatar Catering Services Market Size" (a real
# growth signal) was filed as an Opportunity and the Threats quadrant shipped empty.
_GROWTH_SIGNALS = ("market size", "market share", "forecast", "growth", "boom", "expansion",
                   "fastest growing", "opens", "opening", "new branch", "investment", "funding",
                   "نمو", "توسع", "استثمار", "افتتاح", "حجم السوق")

# COMMODITIZATION — the moat closing. MEASURED (nanocare, 2026-07-21): the brand's entire
# positioning is Liposomal delivery, and the run filed "Why Liposomal Technology Is Becoming the
# Standard" and "finally replacing traditional tablets" as OPPORTUNITIES. When the thing that makes
# a brand special becomes the default, the differentiator stops differentiating — the single most
# consequential fact in that whole analysis, shipped in the wrong box. Distinct from growth
# signals: growth means more rivals, commoditization means less advantage.
_COMMODITIZATION_SIGNALS = ("becoming the standard", "the new standard", "becomes standard",
                            "now standard", "mainstream", "widely adopted", "replacing",
                            "replaces", "everyone is", "commodity", "ubiquitous",
                            "المعيار", "معيار جديد", "أصبحت شائعة", "يحل محل", "بديلاً عن")

_INFERRED = "directional_not_validated"


def derived_threats(*, trends=None, weaknesses=None, maturity=None) -> List[SWOTItem]:
    # `weaknesses` is accepted and intentionally UNUSED — see the note below.
    """Threats from the evidence a thin run DOES have — each labelled as inferred.

    MEASURED (Volume Qatar): every one of the four real generators needs a scrapable peer or a
    peer complaint, and with none of those the quadrant shipped empty next to five untouched market
    signals. The owner's ruling is to derive from weaker evidence and SAY SO, so each item here
    carries `claim_strength="directional_not_validated"` (the existing epistemic ladder, not a new
    field) and text that names the inference out loud. Nothing is emitted without a source.
    """
    out: List[SWOTItem] = []

    seen_kinds: set = set()
    for t in (trends or []):
        title = str(getattr(t, "title", "") or "").strip()
        if not title:
            continue
        low = title.lower()
        if any(sig in low for sig in _COMMODITIZATION_SIGNALS):
            kind, text = "moat", ("اللي بيميّزك بقى معياراً في السوق — ميزتك التنافسية بتتآكل؛ "
                                  "استنتاج من إشارة سوق، مش من منافس مقيس")
        elif any(sig in low for sig in _GROWTH_SIGNALS):
            kind, text = "growth", ("سوق الفئة بيكبر وبيجذب لاعبين جدد — استنتاج من إشارة سوق، "
                                    "مش من منافس مقيس")
        else:
            continue
        if kind in seen_kinds:
            continue               # one threat per KIND; repeating the same point is noise
        seen_kinds.add(kind)
        url = str(getattr(t, "url", "") or "")
        out.append(SWOTItem(text=text,
                            citation=[u for u in [url] if u] or [title],
                            evidence=title,
                            claim_strength=_INFERRED))

    # DELIBERATELY NOT a weakness->threat generator. The first version had one, and the existing
    # `test_scraped_behind_standalone_emits_no_phantom_threat` caught it immediately: in standalone
    # mode it turned "No online booking path on-site" into a threat. That breaks the framework
    # itself — in SWOT a threat is EXTERNAL and a weakness is INTERNAL, so re-labelling your own
    # gap as a threat is double-counting dressed as insight. Threats here come only from evidence
    # OUTSIDE the brand: market signals and the brand's standing in its market.

    level = str(getattr(maturity, "level", "") or "")
    if level == "nascent":
        out.append(SWOTItem(
            text=("براند ناشئ في سوق فيه أسماء متعرّفة — خطر ضعف الوعي؛ استنتاج من إشارات النضج"),
            citation=list(getattr(maturity, "signals", None) or []),
            evidence=str(getattr(maturity, "disclosure", "") or "nascent brand"),
            claim_strength=_INFERRED))
    return out


def disclose_empty_quadrants(swot: "SWOT", *, n_peers: int, scrapable_peers: int = 0) -> None:
    """Say WHY a quadrant is empty — per quadrant, not for the SWOT as a whole.

    MEASURED: `competitive_empty` at :190 is computed across all four quadrants, so Volume's single
    incidental review-count strength made it False and the "Opportunities/Threats need market
    comparison" note never fired. The deck then rendered a `0` badge and an em-dash with no
    explanation. An empty box a client cannot interpret is not honesty, it is silence.
    """
    reasons = {
        "الفرص": "الفرص محتاجة مقارنة سوق أو أصوات عملاء",
        "التهديدات": "التهديدات محتاجة قرين له موقع قابل للفحص، أو شكاوى عملاء، أو إشارة سوق",
        "نقاط القوة": "نقاط القوة محتاجة أبعاد مقارنة من موقعك أو من الأقران",
        "نقاط الضعف": "نقاط الضعف محتاجة أبعاد مقارنة من موقعك أو من الأقران",
    }
    quads = (("نقاط القوة", swot.strengths), ("نقاط الضعف", swot.weaknesses),
             ("الفرص", swot.opportunities), ("التهديدات", swot.threats))
    for label, items in quads:
        if items:
            continue
        swot.notes.append(
            f"{label}: فاضية — {reasons[label]}. المتاح في التشغيلة دي: "
            f"{n_peers} قرين، منهم {scrapable_peers} بموقع قابل للفحص."
        )

STANDALONE_LABEL = "Standalone Strategic Analysis (No Competitors Found)"
STANDALONE_THIN_LABEL = "Standalone Strategic Analysis (competitor comparison too thin)"


_SCRAPED_CAPABILITY_KEYS = {
    "online_booking", "whatsapp", "shows_reviews", "cta_count",
    "offerings_count", "bilingual", "trust_count", "social_count",
}


def unique_insight_texts(profile) -> List[str]:
    """Pull each `other_unique_insights` value from a BusinessProfile — works on both an
    object (full_run) and a serialized dict (API). Empty list when absent."""
    items = getattr(profile, "other_unique_insights", None)
    if items is None and isinstance(profile, dict):
        items = profile.get("other_unique_insights")
    out: List[str] = []
    for it in (items or []):
        v = getattr(it, "value", None)
        if v is None and isinstance(it, dict):
            v = it.get("value")
        if isinstance(v, str) and v.strip():
            out.append(v.strip())
    return out


def _prepend_unique(dst: List[SWOTItem], items: List[SWOTItem]) -> None:
    """Prepend `items` to `dst` (so they LEAD), skipping any whose text already appears — used to
    put brand-level strengths and market-trend O/T ahead of the mechanical lines without dupes."""
    existing = {s.text.strip().lower() for s in dst}
    fresh: List[SWOTItem] = []
    for it in items:
        k = it.text.strip().lower()
        if k and k not in existing:
            existing.add(k)
            fresh.append(it)
    dst[:0] = fresh


def _append_unique(dst: List[SWOTItem], items: List[SWOTItem]) -> None:
    """Append `items` to `dst` (supplementary, after the concrete lines), skipping text dupes."""
    existing = {s.text.strip().lower() for s in dst}
    for it in items:
        k = it.text.strip().lower()
        if k and k not in existing:
            existing.add(k)
            dst.append(it)


def synthesize_swot(
    matrix: ComparativeGapMatrix,
    themes: Optional[List[ReviewTheme]] = None,
    unique_insights: Optional[List[str]] = None,
    profile: Optional[dict] = None,
    trends: Optional[list] = None,
    ad_presence: Optional[list] = None,
) -> SWOT:
    themes = themes or []
    swot = SWOT()
    n_peers = len(matrix.competitors)

    for gap in matrix.gaps:
        strength = _gap_strength(gap)
        if gap.verdict == "ahead":
            swot.strengths.append(SWOTItem(gap.detail, _cite_ahead(gap), gap.detail,
                                           claim_strength=strength))
        elif gap.verdict == "behind":
            if gap.dimension.source == "scraped":
                # Internal lens: you lack/lag on something on your own site.
                swot.weaknesses.append(SWOTItem(gap.detail, _cite_behind(gap), gap.detail,
                                                claim_strength=strength))
                # External lens: a real competitor outperforming you on a site
                # dimension is also a THREAT. This is what populates Threats for
                # ECOMMERCE / web-discovered peers (which carry NO Places dims) —
                # without it, an online SWOT's Threats quadrant is always empty.
                # Grounded: cites the same leading peer(s); only when peers exist.
                if n_peers > 0:
                    swot.threats.append(SWOTItem(
                        text=f"Competitors lead on {gap.dimension.label} where you are behind.",
                        citation=_cite_behind(gap),
                        evidence=gap.detail,
                        claim_strength=strength,
                    ))
            else:  # places dimension -> market-position threat
                swot.threats.append(SWOTItem(gap.detail, _cite_places(gap), gap.detail,
                                             claim_strength=strength))
        elif gap.verdict == "whitespace":
            swot.opportunities.append(SWOTItem(gap.detail, _cite_whitespace(gap, n_peers),
                                               gap.detail, claim_strength=strength))

    # review themes (customer voice, grounded in real reviews/comments)
    for t in themes:
        theme_strength = "validated" if len(t.support or []) >= 2 else "directional_not_validated"
        if t.is_own_brand:
            # R-5: the customers' OWN voice about the subject — praise is a Strength,
            # a recurring complaint is a Weakness. Deterministic, evidence carried.
            item = SWOTItem(text=t.text, citation=list(t.support) or ["own-brand reviews"],
                            evidence=f"{t.polarity} theme in own-brand customer voice",
                            claim_strength=theme_strength)
            (swot.strengths if t.polarity == "praise" else swot.weaknesses).append(item)
            continue
        item = SWOTItem(text=t.text, citation=list(t.support) or ["competitor reviews"],
                        evidence=f"{t.polarity} theme across peers",
                        claim_strength=theme_strength)
        if t.polarity == "complaint" and t.is_unmet_need:
            # peers fail at this -> a differentiation opening for you
            swot.opportunities.append(SWOTItem(
                text=f"Peers are criticized for {t.text} — an opening to differentiate.",
                citation=item.citation, evidence=item.evidence,
                claim_strength=theme_strength))
        elif t.polarity == "complaint":
            swot.threats.append(SWOTItem(
                text=f"{t.text} is a recurring complaint in the category.",
                citation=item.citation, evidence=item.evidence,
                claim_strength=theme_strength))
        # praise themes are kept as notes (context), not forced into a quadrant
        elif t.polarity == "praise":
            swot.notes.append(f"Category strength to match: {t.text} ({_short(item.citation)})")

    # ---- Standalone degrade -------------------------------------------------
    # The competitive synthesis above is purely peer-gap-driven, so with no peers
    # (or a comparison too thin to clear the evidence bar) it yields nothing.
    # Rather than emit an empty SWOT, fall back to a profile-only analysis built
    # from the subject's OWN scraped dimensions. Still fully grounded: UNKNOWN
    # (None) cells are skipped — we never infer a value (rule #4).
    competitive_empty = not any(
        [swot.strengths, swot.weaknesses, swot.opportunities, swot.threats]
    )
    if n_peers == 0 or competitive_empty:
        s, w = _standalone_from_subject(matrix)
        swot.strengths.extend(s)
        swot.weaknesses.extend(w)
        swot.mode = "standalone"
        swot.notes.insert(0, STANDALONE_LABEL if n_peers == 0 else STANDALONE_THIN_LABEL)
        swot.notes.append(
            "Opportunities/Threats need market comparison; supply competitors "
            "(or a review-theme source) to populate them."
        )

    # Own-site S/W floor (competitive mode): on the web path peers' scraped dims are
    # UNKNOWN, so the competitive pass can yield THEME/PLACES items while a Strengths
    # or Weaknesses quadrant stays empty — those quadrants are still knowable from
    # the subject's OWN scraped site. Fill ONLY an empty quadrant with the own-site
    # items (internally_supported tier), keeping the mode competitive — a quadrant
    # that already carries real gap-derived items is never diluted.
    if swot.mode == "competitive" and not competitive_empty \
            and (not swot.strengths or not swot.weaknesses):
        s, w = _standalone_from_subject(matrix)
        added = False
        if not swot.strengths and s:
            swot.strengths.extend(s)
            added = True
        if not swot.weaknesses and w:
            swot.weaknesses.extend(w)
            added = True
        if added:
            swot.notes.append(
                "Own-site items added where the peer comparison had no signal "
                "(peers' site dimensions are unknown on this path).")

    # Unique competitive edges (profile.other_unique_insights) are STRENGTHS by nature —
    # append them grounded to the subject's own profile. Done AFTER the standalone degrade
    # so they don't suppress the 0-peer fallback (which keys on an otherwise-empty SWOT).
    seen_strengths = {s.text.strip().lower() for s in swot.strengths}
    for ins in (unique_insights or []):
        text = (ins or "").strip()
        if text and text.lower() not in seen_strengths:
            seen_strengths.add(text.lower())
            swot.strengths.append(SWOTItem(
                text=text, citation=["your profile"],
                evidence="unique competitive edge stated on the site"))

    # Brand-level strengths from the grounded profile (distinctive value props + real proof) — the
    # signals the mechanical matrix throws away (owner: "the SWOT is all page attributes, not
    # brand-level"). Ledger-gated (no invented facts) and PREPENDED so the SWOT LEADS with brand
    # strategy, not "Number of CTAs". profile=None -> unchanged (regression-safe). Added AFTER the
    # standalone/floor logic so it can't suppress the 0-peer fallback (keyed on an empty SWOT).
    if profile:
        try:
            from grounding import EvidenceLedger

            from competitor.brand_signals import strengths_from_profile, weaknesses_from_readiness
            # P-24: wire the SUBJECT JUDGE so a disjoint-subject superlative ("The best coffee"
            # sourced by "Best regards") is REJECTED, not leniently accepted. Without it the ledger
            # runs in its documented lenient mode (`ledger.py:685` accepts a disjoint subject when
            # judge is None), so the SWOT/compliance path certified fabricated superlatives.
            # Degrades safely by construction: default_caller()->None offline, make_subject_judge(
            # None)->None, and behaviour is unchanged (a blocked/absent caller yields a lenient gate).
            _judge = None
            try:
                from business_profile.llm import default_caller
                from grounding.subject_judge import make_subject_judge
                _judge = make_subject_judge(default_caller(strong=False))
            except Exception:  # noqa: BLE001
                _judge = None
            _ledger = EvidenceLedger.from_profile(profile, subject_judge=_judge)
            brand = strengths_from_profile(profile, _ledger)
            rweak = weaknesses_from_readiness(profile, _ledger)
        except Exception:  # noqa: BLE001 — a signal-mining error must never break the SWOT
            brand, rweak = [], []
        _prepend_unique(swot.strengths, brand)   # brand-level strengths lead
        _append_unique(swot.weaknesses, rweak)   # readiness-gap weaknesses are supplementary

    # Coverage Opportunities/Threats from on-topic TRENDS — the signal the SWOT never had, so an
    # ecommerce brand with no Places peers and no reachable reviews still gets real O/T (not an
    # empty quadrant). NOT Ledger-gated: this comment used to say "Ledger-gated inside the helper",
    # which was false — that gate indexed the trend lines as their own evidence and could never
    # reject one (deleted, P1). What is real: junk/aggregator hosts dropped, an on-topic term match,
    # a required URL, and claim_strength="directional_not_validated" on every line. Prepended so
    # market signals lead; trends=None/[] -> unchanged (regression-safe).
    if trends:
        try:
            from competitor.brand_signals import opportunities_threats_from_trends
            t_opps, t_threats = opportunities_threats_from_trends(profile or {}, trends)
        except Exception:  # noqa: BLE001
            t_opps, t_threats = [], []
        _prepend_unique(swot.opportunities, t_opps)
        _prepend_unique(swot.threats, t_threats)

    # U2 ad-presence -> Threats (competitors' proven-winner/active ads). ad_presence=None/[] ->
    # unchanged (regression-safe); flows the instant META_ADS_TOKEN + the identity confirm land.
    if ad_presence:
        try:
            from competitor.brand_signals import threats_from_ad_presence
            _prepend_unique(swot.threats, threats_from_ad_presence(ad_presence))
        except Exception:  # noqa: BLE001
            pass

    if not any([swot.strengths, swot.weaknesses, swot.opportunities, swot.threats]):
        swot.notes.append("No subject dimensions were known either — the scrape "
                          "yielded no comparable signal (all cells UNKNOWN).")
    # THREATS FROM WEAKER EVIDENCE (owner ruling, 2026-07-21). Runs LAST and only into an EMPTY
    # quadrant: measured, peer-compared threats always win, and inference never dilutes them.
    # Volume Qatar shipped `threats: []` beside five untouched Qatar market signals because every
    # real generator needs a scrapable peer. Each derived item is labelled `directional_not_
    # validated` and says in its own text that it is an inference.
    if not swot.threats:
        try:
            _maturity = None
            if profile:
                from media_plan.maturity import brand_maturity
                _maturity = brand_maturity(profile)
            swot.threats.extend(derived_threats(trends=trends, weaknesses=swot.weaknesses,
                                                maturity=_maturity))
        except Exception:  # noqa: BLE001 — inference must never break a grounded SWOT
            pass

    # Say WHY any quadrant is empty — per quadrant. The whole-SWOT check above is silenced by a
    # single unrelated item, which is exactly how Volume's empty Threats box shipped unexplained.
    try:
        _scrapable = sum(1 for c in matrix.competitors
                         if getattr(c, "has_scrapable_site", False))
    except Exception:  # noqa: BLE001
        _scrapable = 0
    disclose_empty_quadrants(swot, n_peers=n_peers, scrapable_peers=_scrapable)

    _prioritise(swot, matrix, n_peers=n_peers)
    return swot


# ---------------------------------------------------------------------------
# Prioritisation — Pickton & Wright (1998), the owner's paper
# ---------------------------------------------------------------------------

# Website mechanics. True facts about the site, and — stated with no comparison — not strategy:
# "Broad offering range (5 lines)" is broad compared to WHAT? The paper calls this "factors
# described broadly: lack of specificity". They move to `site_readiness` and stay visible there.
# A site dimension that WAS compared against peers is a genuine competitive gap and stays in the
# strategic quadrants, so the rule keys on whether a comparison exists, not on the topic.
_SITE_DIMENSION_KEYS = {d.key for d in DIMENSIONS if getattr(d, "source", "") == "scraped"}


def _is_site_mechanic(item: "SWOTItem") -> bool:
    """Structural, not textual: the item CITES one of the scraped site dimensions.

    The first version matched phrases, and an existing readiness test caught it over-reaching —
    "on-site" also appears in "No tagline on-site", which is a brand-positioning gap, not a website
    mechanic. The own-site generators cite the dimension key itself, so asking what an item is
    grounded in answers the question exactly, and cannot drift when someone rewords a phrase."""
    return any(str(c) in _SITE_DIMENSION_KEYS for c in (getattr(item, "citation", None) or []))


def _gap_by_text(matrix: ComparativeGapMatrix) -> dict:
    out = {}
    for gap in getattr(matrix, "gaps", []) or []:
        if getattr(gap, "detail", None):
            out[str(gap.detail)] = gap
    return out


def _prioritise(swot: SWOT, matrix: ComparativeGapMatrix, *, n_peers: int = 0) -> None:
    """Score, sort, separate and disclose — in that order.

    Everything here is deterministic and measured off the peer set already in `matrix`. Nothing is
    invented: an item with no comparison scores `unknown` and says so rather than receiving a
    number that looks like analysis."""
    by_detail = _gap_by_text(matrix)
    dropped = 0

    for prefix, quadrant in (("S", swot.strengths), ("W", swot.weaknesses),
                             ("O", swot.opportunities), ("T", swot.threats)):
        kept: List[SWOTItem] = []
        moved: List[SWOTItem] = []
        for item in quadrant:
            gap = by_detail.get(item.evidence) or by_detail.get(item.text)
            item.priority = (score_gap(gap) if gap is not None
                             else score_opinion_item(claim_strength=item.claim_strength))
            # An un-compared website mechanic is information, not strategy.
            if gap is None and _is_site_mechanic(item):
                moved.append(item)
            elif item.priority.drop:
                dropped += 1
            else:
                kept.append(item)
        # Sort AFTER scoring and BEFORE assigning ids, so the id names the item's final place and
        # `swot_item_ids` (which now reads the stored id) cannot be desynchronised by any later
        # reordering.
        kept.sort(key=lambda it: getattr(it.priority, "rank", 0.0), reverse=True)
        for i, item in enumerate(kept, 1):
            item.id = f"{prefix}{i}"
        quadrant[:] = kept
        swot.site_readiness.extend(moved)

    for i, item in enumerate(swot.site_readiness, 1):
        item.id = item.id or f"R{i}"

    if dropped:
        # Kotler's rule is applied, but a silent removal reads to the client as "there was nothing
        # there". Say how many left and why.
        swot.notes.append(
            f"استُبعد {dropped} عنصر لضعفه على المحورين معاً (أهمية منخفضة في السوق وفجوة صغيرة) — "
            "حسب مصفوفتَي الفرصة والتهديد عند Kotler: العنصر الضعيف على المحورين يُحذف بدل أن "
            "يوهم بثقلٍ ليس له.")
    if swot.site_readiness:
        swot.notes.append(
            f"نُقل {len(swot.site_readiness)} عنصر إلى «جاهزية الموقع» — حقائق صحيحة عن الموقع "
            "لكنها بلا مقارنة، والقوة أو الضعف لا يكونان إلا بالمقارنة.")
    # An empty strategic SWOT is the CORRECT answer when nothing was compared — and a blank page is
    # a terrible way to say it. Name the cause and the one action that fixes it, so the client is
    # not left deciding whether the analysis failed or their market is featureless.
    #
    # The two causes are DIFFERENT and must not share a sentence: "no competitor is registered" is
    # fixed by naming one; "a competitor is registered but nothing could be read from it" is not,
    # and telling that owner to add a competitor asks her to redo what she already did.
    # The condition is "nothing was COMPARED", not "the quadrants are empty". Measured on the real
    # nanocare profile: five self-claims filled the strengths box while zero items had been checked
    # against any peer, so an empty-quadrant test stayed silent and the client saw what looked like
    # a competitive analysis containing no comparison at all.
    compared = any(getattr(getattr(it, "priority", None), "peers_measured", 0)
                   for it in swot.strengths + swot.weaknesses + swot.opportunities + swot.threats)
    if not compared and swot.site_readiness:
        if n_peers:
            swot.notes.append(
                f"لا توجد نقاط قوة أو ضعف استراتيجية رغم وجود {n_peers} منافس مسجَّل: "
                "لم يُقرأ من موقعه أي بُعد قابل للمقارنة (الصفحة لم تُفتح أو لا تحتوي إشارات "
                "قابلة للقياس). جرّبي منافساً بموقع يعمل، أو راجعي الرابط المُدخَل.")
        else:
            swot.notes.append(
                "لا توجد نقاط قوة أو ضعف استراتيجية في هذه التشغيلة: لم يُقَس أي منافس، "
                "والقوة أو الضعف لا يكونان إلا بالمقارنة. أضيفي منافساً واحداً على الأقل "
                "لتتحول قراءات «جاهزية الموقع» أعلاه إلى تحليل استراتيجي حقيقي.")


# ---------------------------------------------------------------------------
# Standalone (profile-only) synthesis
# ---------------------------------------------------------------------------

def _standalone_from_subject(matrix: ComparativeGapMatrix):
    """Derive Strengths/Weaknesses from the subject's own scraped dimensions.

    Grounded in the subject's scrape; cites the dimension + 'your scraped site'.
    UNKNOWN (None) cells are skipped so we never infer. Counts are the already-
    deduped values from the matrix (no inflated 'N social links').
    """
    from competitor.brand_signals import phrase_dimension   # lazy: brand_signals imports SWOTItem

    vals = matrix.subject.values
    strengths: List[SWOTItem] = []
    weaknesses: List[SWOTItem] = []
    for dim in DIMENSIONS:
        if dim.source != "scraped":
            continue
        v = vals.get(dim.key)
        if v is None:                       # UNKNOWN — never inferred
            continue
        cite = ["your scraped site", dim.key]
        ev = f"subject {dim.key}={v}"
        # Same scrape-grounded cell, strategist phrasing (not a raw "label: value" dump).
        if dim.kind == "bool":
            bucket = strengths if v else weaknesses
            bucket.append(SWOTItem(phrase_dimension(dim.key, dim.label, dim.kind, v, bool(v)), cite, ev))
        elif dim.kind == "count":
            positive = bool(v and v > 0)
            bucket = strengths if positive else weaknesses
            bucket.append(SWOTItem(phrase_dimension(dim.key, dim.label, dim.kind, v, positive), cite, ev))
    return strengths, weaknesses


def _gap_strength(gap: DimensionGap) -> str:
    """Deterministic claim strength for a gap-driven item: a verdict compared against
    >=2 peers with KNOWN values is 'validated'; a single-peer comparison is only
    'directional_not_validated' (a signal, not a confirmed market verdict)."""
    return "validated" if len(gap.competitor_values) >= 2 else "directional_not_validated"


# ---------------------------------------------------------------------------
# citation builders
# ---------------------------------------------------------------------------

def _cite_ahead(gap: DimensionGap) -> List[str]:
    cites = ["your profile"]
    if gap.dimension.kind == "bool":
        without = [k for k, v in gap.competitor_values.items() if not v]
        if without:
            cites.append(f"peers without it: {_short(without)}")
    else:
        cites.append("Places: peer average" if gap.dimension.source == "places"
                     else "your scraped site vs peers")
    return cites


def _cite_behind(gap: DimensionGap) -> List[str]:
    cites = ["your profile"]
    if gap.dimension.kind == "bool":
        with_it = [k for k, v in gap.competitor_values.items() if v]
        if with_it:
            cites.append(f"peers with it: {_short(with_it)}")
    return cites


def _cite_places(gap: DimensionGap) -> List[str]:
    label = "Places review volume" if gap.dimension.key == "review_count" else "Places ratings"
    return [label, f"{len(gap.competitor_values)} peers"]


def _cite_whitespace(gap: DimensionGap, n_peers: int) -> List[str]:
    return ["your profile", f"{len(gap.competitor_values)}/{n_peers} peers also lack it"]


def _short(names, k=3):
    names = list(names)
    head = ", ".join(str(n) for n in names[:k])
    return head + (f" +{len(names) - k} more" if len(names) > k else "")


# ---------------------------------------------------------------------------
# pretty-print (for quick inspection / defense)
# ---------------------------------------------------------------------------

def format_swot(swot: SWOT) -> str:
    out = ["SWOT — STANDALONE (profile-only)" if swot.mode == "standalone"
           else "SWOT — COMPETITIVE"]
    for title, items in (("STRENGTHS", swot.strengths), ("WEAKNESSES", swot.weaknesses),
                         ("OPPORTUNITIES", swot.opportunities), ("THREATS", swot.threats)):
        out.append(f"\n{title} ({len(items)})")
        for it in items:
            out.append(f"  • {it.text}")
            out.append(f"      ↳ cite: {'; '.join(it.citation)}")
    if swot.notes:
        out.append("\nNOTES")
        for n in swot.notes:
            out.append(f"  - {n}")
    return "\n".join(out)