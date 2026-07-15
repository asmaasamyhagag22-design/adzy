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


@dataclass
class SWOT:
    strengths: List[SWOTItem] = field(default_factory=list)
    weaknesses: List[SWOTItem] = field(default_factory=list)
    opportunities: List[SWOTItem] = field(default_factory=list)
    threats: List[SWOTItem] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)
    mode: str = "competitive"           # "competitive" | "standalone"


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
            _ledger = EvidenceLedger.from_profile(profile)
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
    return swot


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