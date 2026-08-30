# -*- coding: utf-8 -*-
"""What actually WORKS on a brand's social — the number the system was already collecting.

OWNER (2026-08-01): «عايزه اسكراب السوشيال ميديا، اكتر حاجه جايبه لايك كدا فاهمني».

MEASURED BEFORE WRITING THIS. `like_count` is declared in `social_intel/schemas.py`, populated by
every provider (`apify_facebook`, `ig_business`, `snapshot_parser`, `fixture`) — and read by ZERO
analysis code. `grep` for a sort on it across the whole repo returns nothing. The engagement was
being collected, hashed, validated and thrown away.

The nearest thing that existed, `competitor/social_gaps.py`, compares which PLATFORMS each side is
on. That is presence, not performance: knowing a competitor is on Instagram is a different fact
from knowing their best post outperformed their own median by six times and was a before/after.

RAW LIKES ARE NOT COMPARABLE ACROSS BRANDS, and pretending otherwise is the failure class this
repo keeps paying for — a true number answering a different question. A pharmacy with 400k
followers out-likes a clinic with 4k on every post it ever publishes, which says nothing about
whose CONTENT works. So:

  * posts are ranked WITHIN their own brand, never against another's
  * the cross-brand metric is LIFT — how far a brand's best beats its OWN median — because that
    is a property of the content, not of the audience size
  * a signal with no like count is UNRANKABLE, not a zero. It is excluded and counted, and the
    report says how many, so "we found nothing that works" can never be confused with "nothing
    was measured".

Deterministic: no model is called here. Ranking and arithmetic only.
"""
from __future__ import annotations

import logging
from typing import Any, Iterable, Optional

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

# A comment's likes measure the COMMENTER, not the brand's content. Only what the brand published
# counts toward "what works for this brand".
_PUBLISHED = ("post", "caption")

# Below this, a "top post" is one person's opinion rather than a signal. Kept low because a small
# local brand's real best post genuinely is single digits — the honest floor is "more than one".
MIN_LIKES_TO_RANK = 2

# How many posts a brand needs before a median means anything. Under this the lift is arithmetic
# on noise and the report says so rather than quoting a multiple.
MIN_POSTS_FOR_LIFT = 4


class TopPost(BaseModel):
    text: str = ""
    likes: int = 0
    url: str = ""
    posted_at: Optional[str] = None
    lang: str = ""


class BrandPerformance(BaseModel):
    """One brand's content performance, measured only against itself."""

    brand_ref: str = ""
    is_own_brand: bool = False
    ranked: int = 0                    # posts that carried a usable like count
    unranked: int = 0                  # posts with NO like count — measured, not assumed zero
    median_likes: float = 0.0
    top: list[TopPost] = Field(default_factory=list)

    @property
    def lift(self) -> Optional[float]:
        """How far the best post beats this brand's OWN median. None when there is too little to
        say — the comparable number across brands, because it is a property of the content rather
        than of the follower count."""
        if self.ranked < MIN_POSTS_FOR_LIFT or self.median_likes <= 0 or not self.top:
            return None
        return round(self.top[0].likes / self.median_likes, 2)

    @property
    def measured(self) -> bool:
        return self.ranked > 0


class PerformanceReport(BaseModel):
    own: Optional[BrandPerformance] = None
    peers: list[BrandPerformance] = Field(default_factory=list)
    total_signals: int = 0
    published_signals: int = 0
    reason: str = ""

    @property
    def checked(self) -> bool:
        """False when nothing was measurable — the third state, so an empty report is never read
        as 'this brand's content does not work'."""
        return bool((self.own and self.own.measured) or any(p.measured for p in self.peers))


def _median(values: list[int]) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    mid = len(s) // 2
    return float(s[mid]) if len(s) % 2 else (s[mid - 1] + s[mid]) / 2.0


def _as_post(sig: Any) -> Optional[TopPost]:
    at = getattr(sig, "posted_at", None)
    return TopPost(
        text=" ".join(str(getattr(sig, "text", "") or "").split())[:400],
        likes=int(getattr(sig, "like_count", 0) or 0),
        url=str(getattr(sig, "source_url", "") or ""),
        posted_at=at.isoformat() if hasattr(at, "isoformat") else (str(at) if at else None),
        lang=str(getattr(sig, "lang", "") or ""),
    )


def _brand(signals: list, ref: str, own: bool, *, top_n: int) -> BrandPerformance:
    ranked = [s for s in signals if getattr(s, "like_count", None) is not None]
    unranked = len(signals) - len(ranked)
    usable = [s for s in ranked if int(getattr(s, "like_count", 0) or 0) >= MIN_LIKES_TO_RANK]
    likes = [int(getattr(s, "like_count", 0) or 0) for s in ranked]
    top = sorted(usable, key=lambda s: int(getattr(s, "like_count", 0) or 0), reverse=True)[:top_n]
    return BrandPerformance(
        brand_ref=ref, is_own_brand=own,
        ranked=len(ranked), unranked=unranked,
        median_likes=_median(likes),
        top=[p for p in (_as_post(s) for s in top) if p is not None],
    )


def top_performers(signals: Iterable[Any], *, top_n: int = 5) -> PerformanceReport:
    """What each brand's best-performing published content actually is.

    `signals` are `social_intel.schemas.SocialSignal`s (or anything with the same attributes).
    Never raises; an unmeasurable input returns `checked=False` with the reason."""
    all_sigs = list(signals or [])
    published = [s for s in all_sigs
                 if str(getattr(s, "signal_kind", "") or "") in _PUBLISHED]
    if not published:
        return PerformanceReport(
            total_signals=len(all_sigs),
            reason=("no published posts in the signal set — only comments/replies, which measure "
                    "the commenter rather than the brand's content"))

    by_ref: dict = {}
    for s in published:
        key = (bool(getattr(s, "is_own_brand", False)), str(getattr(s, "brand_ref", "") or ""))
        by_ref.setdefault(key, []).append(s)

    own: Optional[BrandPerformance] = None
    peers: list[BrandPerformance] = []
    for (is_own, ref), group in by_ref.items():
        perf = _brand(group, ref, is_own, top_n=top_n)
        if is_own:
            # More than one own-brand ref would mean the ingestion mislabelled something; keep the
            # one with the most evidence rather than silently dropping either.
            if own is None or perf.ranked > own.ranked:
                own = perf
        else:
            peers.append(perf)
    peers.sort(key=lambda p: (p.lift or 0, p.ranked), reverse=True)

    rep = PerformanceReport(own=own, peers=peers, total_signals=len(all_sigs),
                            published_signals=len(published))
    if not rep.checked:
        rep.reason = (f"{len(published)} published post(s) carried no like count — engagement was "
                      "never measured, which is not the same as content that does not work")
    logger.info("[social] performance: own=%s peer(s)=%s from %s published signal(s)",
                (own.ranked if own else 0), len(peers), len(published))
    return rep


def creative_lessons(report: PerformanceReport, *, limit: int = 3) -> list[str]:
    """The report, as lines a creative brief can act on.

    This is the point of the whole module. Ranking posts and showing them on a dashboard tells a
    human something; feeding the winners into the brief is what makes the next reel learn from
    what already worked. Own-brand lessons lead — they are proof about THIS audience — and the
    peers follow as opportunities.
    """
    out: list[str] = []
    if not report.checked:
        return out
    if report.own and report.own.top:
        lift = report.own.lift
        head = (f"our best-performing post beats our own median by {lift}x"
                if lift else "our best-performing post so far")
        out.append(f"{head}: “{report.own.top[0].text[:160]}”")
    for peer in report.peers[:max(0, limit - len(out))]:
        if not peer.top:
            continue
        lift = peer.lift
        who = peer.brand_ref or "a competitor"
        head = (f"{who}'s top post outperforms their own median by {lift}x"
                if lift else f"{who}'s top post")
        out.append(f"{head}: “{peer.top[0].text[:160]}”")
    return out[:limit]
