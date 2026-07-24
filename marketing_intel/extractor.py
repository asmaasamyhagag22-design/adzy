"""Evidence-bound marketing-intel extractor (Media Plan v2, step 1).

The LLM proposes offers/USPs/pain-points, each with a VERBATIM `quote` + the `permalink` it came
from. The CODE then re-verifies the quote is an exact (whitespace-normalised) substring of a real
snapshot signal — and binds the evidence to the signal the code actually matched, NOT the permalink
the model claimed (PD-1: the model never self-certifies). Any item whose quote is not found verbatim
is REJECTED. That is the moat: groundedness is decided in code, so the model cannot hallucinate an
offer/USP/pain-point into the plan.
"""
from __future__ import annotations

import logging
import re
from typing import Any, List, Optional

from pydantic import BaseModel, ConfigDict

from business_profile.schemas import Confidence, EvidencedField, EvidenceItem, SourceType

from .schemas import ItemKind, MarketingIntelligence

logger = logging.getLogger(__name__)

_MIN_QUOTE_CHARS = 8       # a quote shorter than this could match almost anything -> not evidence
_GROUP = "ig_marketing_intel"


class _RawItem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: ItemKind
    text: str                  # the extractor's short phrasing of the signal
    quote: str                 # MUST be a verbatim substring of one caption/comment
    permalink: str = ""        # the post the quote came from (advisory — code re-derives it)


class _RawResponse(BaseModel):
    items: List[_RawItem] = []


def _norm(s: str) -> str:
    """Whitespace-normalised, casefolded — 'verbatim' robust to spacing/case only."""
    return re.sub(r"\s+", " ", str(s or "")).strip().casefold()


def _system_prompt(mode: str = "own_brand") -> str:
    if mode == "competitor":
        return (
            "You are a marketing analyst reading a COMPETITOR's PUBLIC ad texts (Meta Ad Library "
            "creatives, each with a snapshot permalink). Extract ONLY what the text actually says "
            "- never infer, never invent. Return items of four kinds:\n"
            "- offer: an ACTIVE promotion the competitor is running (discount, bundle, free trial).\n"
            "- usp: a selling point the competitor claims in its own words.\n"
            "- pain_point: a customer pain the competitor's copy TARGETS, in the copy's own words.\n"
            "- message_angle: the persuasion play the ad leans on (fear-of-pain / price / prestige "
            "/ speed / social-proof / urgency-scarcity). Name the angle in `text`.\n"
            "For EVERY item you MUST include a `quote` copied WORD-FOR-WORD from one ad text (no "
            "paraphrase, no ellipsis) and the `permalink` it came from. If you cannot quote it "
            "verbatim, DO NOT include it. Fewer grounded items beat guesses."
        )
    return (
        "You are a marketing analyst reading a brand's own Instagram/social SNAPSHOT (captions, "
        "posts, and audience comments, each with a permalink). Extract ONLY what the text actually "
        "says — never infer, never invent. Return items of three kinds:\n"
        "- offer: an ACTIVE promotion/offer the BRAND is running (discount, bundle, free trial, "
        "limited-time deal).\n"
        "- usp: a unique selling point the BRAND claims in its own words (what makes it different).\n"
        "- pain_point: a customer PAIN or complaint expressed in the AUDIENCE's own words (comments).\n"
        "For EVERY item you MUST include a `quote` that is copied WORD-FOR-WORD from one caption or "
        "comment (no paraphrase, no ellipsis, no added words) and the `permalink` it came from. If "
        "you cannot quote it verbatim, DO NOT include it. Better to return fewer, fully-grounded "
        "items than to guess."
    )


def _corpus(signals) -> str:
    lines = []
    for i, s in enumerate(signals or []):
        url = getattr(s, "source_url", "") or ""
        kind = getattr(s, "signal_kind", "") or ""
        text = getattr(s, "text", "") or ""
        lines.append(f"[{i}] ({kind}) permalink={url}\n{text}")
    return "\n\n".join(lines)


def _match_signal(quote: str, signals) -> Optional[Any]:
    """Return the snapshot signal whose text CONTAINS `quote` verbatim (whitespace/case-normalised),
    or None. This is the moat check — a quote the model fabricated matches no real signal."""
    nq = _norm(quote)
    if len(nq) < _MIN_QUOTE_CHARS:
        return None
    for s in (signals or []):
        if nq in _norm(getattr(s, "text", "")):
            return s
    return None


def _to_field(text: str, signal: Any) -> EvidencedField:
    """Bind the item to the REAL signal the code matched (its permalink), not the model's claim."""
    permalink = str(getattr(signal, "source_url", "") or "")
    quote = str(getattr(signal, "text", "") or "")[:200]
    return EvidencedField(
        value=str(text).strip(),
        evidence=[EvidenceItem(page_url=permalink, quote=quote, extractor="ig_snapshot")],
        confidence=Confidence.HIGH,
        source_type=SourceType.EXTRACTED,
    )


def extract_marketing_intel(signals, caller: Any = None, *, brand_ref: str = "",
                            mode: str = "own_brand", log=logger.info) -> MarketingIntelligence:
    """Extract offers/USPs/pain-points from a social snapshot, each bound to a VERBATIM quote from a
    real signal. `caller` is injectable (MockCaller for hermetic tests; None -> honest empty). No
    item survives without a code-verified quote — the moat rejects fabrications."""
    out = MarketingIntelligence(brand_ref=brand_ref)
    signals = list(signals or [])
    if caller is None or not signals:
        return out
    try:
        group = _GROUP if mode == "own_brand" else "ads_competitor_intel"
        resp, _usage = caller(_system_prompt(mode), _corpus(signals), _RawResponse,
                              group_name=group)
    except Exception as exc:  # noqa: BLE001 — a caller failure degrades to empty, never fabricates
        log(f"[marketing_intel] caller failed ({type(exc).__name__}); returning empty")
        return out
    if resp is None:
        return out

    buckets = {"offer": out.offers, "usp": out.usps, "pain_point": out.pain_points,
               "message_angle": out.message_angles}
    seen: set = set()
    for item in getattr(resp, "items", None) or []:
        kind = getattr(item, "kind", None)
        bucket = buckets.get(kind)
        if bucket is None:
            continue
        signal = _match_signal(getattr(item, "quote", ""), signals)
        if signal is None:
            out.rejected += 1          # the moat: no verbatim quote in the snapshot -> dropped
            continue
        key = (kind, _norm(getattr(item, "text", "")))
        if key in seen:
            continue
        seen.add(key)
        bucket.append(_to_field(getattr(item, "text", ""), signal))
    log(f"[marketing_intel] offers={len(out.offers)} usps={len(out.usps)} "
        f"pain_points={len(out.pain_points)} angles={len(out.message_angles)} "
        f"rejected={out.rejected}")
    return out


def from_snapshot(brand_ref: str, caller: Any = None, *, snapshots_dir: Optional[str] = None,
                  limit: int = 200) -> MarketingIntelligence:
    """Ergonomic entry for Media Plan v2: read the owner-provided social SNAPSHOT for `brand_ref`
    (parse-only, PD-10) via the sanctioned SnapshotSocialProvider, then extract. Empty when no
    snapshot exists (honest degrade). No live IG scrape here — the snapshot is provided by the owner."""
    try:
        from social_intel.providers.snapshot_parser import SnapshotSocialProvider
        signals = SnapshotSocialProvider(snapshots_dir=snapshots_dir).fetch(brand_ref, limit=limit)
    except Exception:  # noqa: BLE001
        signals = []
    return extract_marketing_intel(signals, caller, brand_ref=brand_ref)
