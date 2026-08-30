# -*- coding: utf-8 -*-
"""Social presence as a COMPARISON — one gap per platform.

The owner named the SWOT's three evidence sources: Google Places reviews, social media, and
competitors. Two were connected. Social reached the SWOT ZERO times — `grep social_presence`
across `swot.py` and `brand_signals.py` returned nothing — even though it is measurably the
WIDEST source the system collects: 18 of 22 stored profiles carry social accounts (82%), against
5 of 17 runs with any Places review (29%). Places reviews need a registered branch; most of this
product's clients sell online.

The only thing called "social" in the SWOT was `social_count`, a count of links on a homepage —
which is a website mechanic, not a market position, and now lives in `site_readiness`.

WHY THIS IS A GAP BUILDER AND NOT A GENERATOR. "Which platforms is each side on" is precisely what
`DimensionGap` already models, so every platform becomes one gap and `swot_priority.score_gap`
scores it with arithmetic that is already tested: prevalence when the subject is behind, RARITY
when it is ahead, and Kotler's removal rule for anything weak on both axes. No new judgement, no
second scoring system to keep in step with the first.

Universal (D-8): platforms are DISCOVERED from what the subject and the peers actually publish.
Nothing here enumerates a platform list, so a network that did not exist last year works the day it
appears in someone's footer.
"""
from __future__ import annotations

from typing import Any

from .matrix import Dimension, DimensionGap


def _subject_platforms(profile: Any) -> set:
    """The brand's own platforms, from its evidenced `social_presence`.

    Counted as a SET of platforms, not a count of entries: Coca-Cola stores 17 social entries
    across 4 platforms (regional accounts), and "is this brand on Instagram" is one fact however
    many regional handles it runs."""
    p = profile or {}
    if isinstance(p, dict) and isinstance(p.get("profile"), dict):
        p = p["profile"]
    out = set()
    for item in (p.get("social_presence") or []):
        name = item.get("platform") if isinstance(item, dict) else getattr(item, "platform", None)
        name = str(name or "").strip().lower()
        if name and name != "unknown":
            out.add(name)
    return out


def _peer_platforms(matrix: Any) -> dict:
    """{peer name: set(platforms)} for peers whose site was actually READ.

    A peer whose dims are missing (a JS-only shell, a failed fetch) is skipped entirely rather than
    recorded as having no accounts — reading a failed fetch as "they are on nothing" would invent a
    gap out of an absence of evidence, which is the false-zero this codebase already refuses in
    `lite_peer_dims`."""
    out = {}
    for col in (getattr(matrix, "competitors", None) or []):
        values = getattr(col, "values", None) or {}
        if "social_platforms" not in values:
            continue
        plats = values.get("social_platforms")
        if plats is None:                       # explicitly UNKNOWN
            continue
        out[getattr(col, "name", "")] = {str(x).strip().lower() for x in plats if str(x).strip()}
    return out


def _verdict(subject_has: bool, peer_values: dict) -> str:
    present = sum(1 for v in peer_values.values() if v)
    if subject_has and present < len(peer_values):
        return "ahead"
    if not subject_has and present > 0:
        return "behind"
    return "par"


def _detail(platform: str, subject_has: bool, peer_values: dict) -> str:
    """The client-facing line. NO bare numbers and NO superlatives, on purpose.

    MEASURED: the Evidence Ledger drops a line carrying an unsourced 2+ digit number
    ("Active social presence (17 channels)") or a superlative ("Leading social presence"). The
    counts are real and belong in the scored basis, which `score_gap` writes from the same peer
    values — so nothing is lost by keeping them out of this sentence."""
    total = len(peer_values)
    present = sum(1 for v in peer_values.values() if v)
    if not subject_has:
        if present == total:
            return f"every measured competitor publishes on {platform}; you do not"
        return f"some competitors publish on {platform}; you do not"
    if present == 0:
        return f"you publish on {platform} and no measured competitor does"
    return f"you publish on {platform}, as do some competitors"


def social_gaps(profile: Any, matrix: Any) -> list:
    """One `DimensionGap` per platform seen on either side. Empty when nothing was compared."""
    peers = _peer_platforms(matrix)
    if not peers:
        # No peer was readable: there is no comparison, and a strength or weakness only exists by
        # comparison. Saying nothing is the honest output.
        return []

    mine = _subject_platforms(profile)
    universe = set(mine)
    for plats in peers.values():
        universe |= plats

    gaps = []
    for platform in sorted(universe):
        subject_has = platform in mine
        peer_values = {name: (platform in plats) for name, plats in peers.items()}
        gaps.append(DimensionGap(
            dimension=Dimension(f"social_{platform}", f"{platform} presence",
                                "scraped", "bool", "higher_better"),
            subject_value=subject_has,
            competitor_values=peer_values,
            verdict=_verdict(subject_has, peer_values),
            detail=_detail(platform, subject_has, peer_values),
        ))
    return gaps
