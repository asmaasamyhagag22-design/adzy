"""Brand-level SWOT signals from the grounded business profile.

The mechanical gap matrix (matrix.py) sees a brand as page attributes — cta_count, whatsapp,
social_count. A strategist's SWOT leads with the BRAND: its distinctive promises and its proof.
Those signals already live in the profile (`value_propositions`, `trust_signals` — each a grounded
EvidencedField) but `synthesize_swot` threw them away. This module mines them into Strengths, every
line GATED by the same Evidence Ledger the rest of the pipeline uses (no free-form invention: a line
whose hard claims don't resolve to real evidence is DROPPED). Universal — keyed only off generic
profile fields that exist for every brand, never a vertical or brand name.
"""
from __future__ import annotations

from typing import Any

from competitor.swot import SWOTItem


# Trust signals are proof points, but a site also lists generic ones ("Secure Payments",
# "International Shipping"). Keep only signals carrying REAL proof — a number/social-proof figure
# or a substantive claim keyword — so Strengths stay differentiators, not checkout boilerplate.
_TRUST_PROOF_KEYS = (
    "certif", "award", "iso", "haccp", "guarantee", "warrant", "cruelty", "vegan", "organic",
    "handmade", "handcraft", "hand-craft", "official", "authorized", "authorised", "since",
    "year", "trusted", "loved", "rated", "star", "customer", "client", "patient", "member",
    "no animal", "natural ingredient",
)


# Strategist phrasing for the subject's own-site dimensions, so a standalone SWOT reads like a
# strategist wrote it ("Thin conversion path (3 CTAs)") instead of a raw attribute dump
# ("Number of CTAs: 3"). TEXT ONLY — the SWOTItem's citation/evidence/claim_strength are unchanged,
# and the cell is the same scrape-grounded fact. Keyed off dim.key (universal; no vertical wording).
_DIM_STRONG = {   # present / count > 0
    "online_booking": "Online booking available on-site",
    "whatsapp": "Direct WhatsApp contact channel",
    "shows_reviews": "Customer reviews shown on-site",
    "bilingual": "Bilingual site (Arabic + English)",
    "cta_count": "Clear conversion paths ({v} calls-to-action)",
    "offerings_count": "Broad offering range ({v} lines)",
    "trust_count": "Visible trust markers ({v} on-site)",
    "social_count": "Active social presence ({v} channels)",
}
_DIM_WEAK = {     # absent / count == 0
    "online_booking": "No online booking path on-site",
    "whatsapp": "No WhatsApp contact channel",
    "shows_reviews": "No customer reviews shown on-site",
    "bilingual": "Single-language site (missing Arabic/English)",
    "cta_count": "No clear call-to-action on the page",
    "offerings_count": "No offerings surfaced on-site",
    "trust_count": "No visible trust markers on-site",
    "social_count": "No social links on-site",
}


def phrase_dimension(dim_key: str, label: str, kind: str, value: Any, positive: bool) -> str:
    """Strategist phrasing for one own-site dimension. Falls back to a clean label-based phrase for
    any dimension not in the tables (so a new dimension never regresses to a raw dump)."""
    tmpl = (_DIM_STRONG if positive else _DIM_WEAK).get(dim_key)
    if tmpl:
        return tmpl.format(v=value) if "{v}" in tmpl else tmpl
    if kind == "count":
        return f"{label}: {value}" if positive else f"{label}: none on-site"
    return f"{label} present on-site" if positive else f"{label} not on-site"


def _val(x: Any) -> str:
    if isinstance(x, dict):
        return str(x.get("value") or "").strip()
    return str(x or "").strip()


def _unwrap(profile: Any) -> dict:
    """Accept a BusinessProfile dict or a {'profile': {...}} full-run wrapper."""
    if isinstance(profile, dict) and "value_propositions" not in profile \
            and isinstance(profile.get("profile"), dict):
        return profile["profile"]
    return profile if isinstance(profile, dict) else {}


def _is_strong_trust(text: str) -> bool:
    low = text.lower()
    return any(ch.isdigit() for ch in text) or any(k in low for k in _TRUST_PROOF_KEYS)


def _gated(ledger: Any, text: str) -> bool:
    """The EXACT gate tows.py / strategy use: EVERY hard claim in the line must resolve to real
    evidence. Fails CLOSED (drop) on any ledger error. No ledger -> gating off (pass)."""
    if ledger is None:
        return True
    try:
        return all(v.sourced for v in ledger.audit_text(text or ""))
    except Exception:
        return False


def strengths_from_profile(profile: Any, ledger: Any = None) -> list[SWOTItem]:
    """Brand-level Strengths from the grounded profile: distinctive value propositions and real
    proof points. Each cites its profile field; claim_strength='internally_supported' (own-site
    truth, not peer-validated). Ledger-gated + de-duplicated. [] on an empty/absent profile."""
    p = _unwrap(profile)
    out: list[SWOTItem] = []
    seen: set[str] = set()

    def emit(text: str, field: str) -> None:
        t = (text or "").strip()
        k = t.lower()
        if not t or k in seen or not _gated(ledger, t):
            return
        seen.add(k)
        out.append(SWOTItem(text=t, citation=["your profile", field],
                            evidence=f"brand signal ({field})",
                            claim_strength="internally_supported"))

    for vp in (p.get("value_propositions") or []):
        emit(_val(vp), "value_propositions")
    for ts in (p.get("trust_signals") or []):
        t = _val(ts)
        if _is_strong_trust(t):
            emit(t, "trust_signals")
    return out


# Readiness signals that are a GENUINE, UNIVERSAL brand-foundation gap when False. Deliberately a
# WHITELIST — `locations_with_geo` / `hours_known` are False for every online brand and are NOT
# weaknesses there (rule #4: never a weakness for something simply irrelevant/unknown). Phrased
# WITHOUT numbers so the line is a pure paraphrase that passes the Ledger gate.
_READINESS_WEAKNESS = {
    "tagline": "No clear brand tagline established on-site",
    "value_propositions_3plus": "Thin value proposition — few distinct benefits stated",
    "trust_signals_2plus": "Few trust signals shown on-site",
    "offerings_3plus": "Narrow offering range surfaced on-site",
    "pricing_posture_known": "Pricing posture unclear on-site",
    "multi_page_evidence": "Shallow site presence — evidence from a single page",
}


def weaknesses_from_readiness(profile: Any, ledger: Any = None) -> list[SWOTItem]:
    """Brand-foundation Weaknesses from the grounded readiness audit — ONLY for a whitelisted signal
    that is explicitly False (never inferred from a missing/irrelevant field). Each cites the
    readiness audit; Ledger-gated; `internally_supported`. [] when the audit is absent or all
    whitelisted signals hold."""
    p = _unwrap(profile)
    rd = p.get("readiness") or {}
    sq = rd.get("swot_quality_signals") or {}
    out: list[SWOTItem] = []
    for key, text in _READINESS_WEAKNESS.items():
        if sq.get(key) is False and _gated(ledger, text):     # explicit False only, gated
            out.append(SWOTItem(text=text, citation=["your profile", "readiness audit"],
                                evidence=f"readiness.swot_quality_signals.{key}=false",
                                claim_strength="internally_supported"))
    return out


# A trend title signalling a category HEADWIND (a Threat) rather than an opening (Opportunity).
_TREND_THREAT_KEYS = (
    "ban", "banned", "decline", "declining", "shortage", "regulation", "regulat", "lawsuit",
    "recall", "boycott", "crackdown", "tariff", "restrict", "crisis", "shutdown", "layoff",
)


def opportunities_threats_from_trends(profile: Any, trends: list) -> tuple[list, list]:
    """Map on-topic market TRENDS to brand-level Opportunities/Threats — the market-shift signal the
    SWOT never had (why ecommerce brands with no Places peers got empty O/T quadrants). Each line
    cites the trend URL and is `directional_not_validated` (a single web signal). GROUNDED: a
    non-reputable/aggregator host is dropped up front, and every line is then Ledger-gated exactly
    like the rest of the pipeline. Returns (opportunities, threats); ([],[]) when no on-topic trend
    survives. Pure — the caller fetches trends (best-effort) and passes them in."""
    opps: list[SWOTItem] = []
    threats: list[SWOTItem] = []
    if not trends:
        return opps, threats
    try:
        from grounding.ledger import is_reputable_web_source
    except Exception:
        def is_reputable_web_source(_u):  # noqa: ANN001 — degrade permissive if grounding absent
            return True

    cand: list[tuple[str, str, str]] = []   # (bucket, text, url)
    seen: set[str] = set()
    for t in trends:
        title = str(getattr(t, "title", "") or "").strip()
        url = str(getattr(t, "url", "") or "").strip()
        terms = [str(x) for x in (getattr(t, "matched_terms", ()) or ()) if x]
        if not title or not terms:
            continue                                     # off-topic / unusable
        if url and not is_reputable_web_source(url):
            continue                                     # junk/aggregator host — not proof
        low = title.lower()
        if any(k in low for k in _TREND_THREAT_KEYS):
            bucket, text = "threats", f"Category headwind on {', '.join(terms)}: {title}"
        else:
            bucket, text = "opportunities", f"Rising interest in {', '.join(terms)}: {title}"
        key = text.strip().lower()
        if key in seen:
            continue
        seen.add(key)
        cand.append((bucket, text, url))
    if not cand:
        return opps, threats

    # Build a ledger with the candidate trends indexed as web evidence (+ the profile), then gate
    # each line — a reputable trend self-grounds; anything the ledger can't back is dropped.
    ledger = None
    try:
        from grounding import EvidenceLedger
        swot_ev = {
            "opportunities": [{"text": tx, "citation": [u]} for (b, tx, u) in cand if b == "opportunities"],
            "threats": [{"text": tx, "citation": [u]} for (b, tx, u) in cand if b == "threats"],
        }
        ledger = EvidenceLedger.from_profile(_unwrap(profile) or {}, swot=swot_ev)
    except Exception:
        ledger = None

    for bucket, text, url in cand:
        if not _gated(ledger, text):
            continue
        item = SWOTItem(text=text, citation=[url or "market trends"],
                        evidence="market trend signal (on-topic)",
                        claim_strength="directional_not_validated")
        (opps if bucket == "opportunities" else threats).append(item)
    return opps, threats


def threats_from_ad_presence(ad_presences: Any) -> list[SWOTItem]:
    """U2 -> Threats: a competitor's RUNNING ads are a market-position threat, graded by longevity
    (the market's own split-test verdict). Deterministic, no LLM; cites the Meta Ad Library. Empty
    input -> []. A proven winner (live >= the longevity threshold) is a `validated` threat; merely
    active advertising is `directional_not_validated`."""
    out: list[SWOTItem] = []
    for ap in (ad_presences or []):
        name = (getattr(ap, "page_name", "") or getattr(ap, "brand_ref", "") or "A competitor").strip()
        winners = int(getattr(ap, "proven_winners", 0) or 0)
        active = int(getattr(ap, "active_count", 0) or 0)
        longest = int(getattr(ap, "longest_days", 0) or 0)
        if winners > 0:
            out.append(SWOTItem(
                text=f"{name} runs {winners} proven-winner ad(s) (live 60+ days) — validated creative "
                     f"you're competing against.",
                citation=["Meta Ad Library"],
                evidence=f"proven_winners={winners}, longest_running={longest}d",
                claim_strength="validated"))
        elif active > 0:
            out.append(SWOTItem(
                text=f"{name} is actively advertising ({active} live ad(s)) — competing for the same "
                     f"audience's attention.",
                citation=["Meta Ad Library"],
                evidence=f"active_count={active}",
                claim_strength="directional_not_validated"))
    return out
