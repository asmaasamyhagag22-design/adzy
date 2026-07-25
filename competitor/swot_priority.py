# -*- coding: utf-8 -*-
"""Priority scoring for SWOT factors — the missing half of the analysis.

SOURCE: Pickton & Wright (1998), "What's SWOT in strategic analysis?", Strategic Change 7, 101-109.
Their argument, and the reason this module exists:

    "It tends to persuade companies to compile lists rather than think about what is really
    important to their business. It also presents the resulting lists uncritically, without clear
    prioritization; so that, for example, weak opportunities may appear to balance strong threats."
                                                                    (Mercer 1992, quoted p.104-105)

and, on why an un-compared factor is not a factor at all:

    "strengths and weaknesses are only such in comparison to the competition and opportunities and
    threats only arise out of the collective actions or inaction within the marketplace ... They
    are, thus, relative and not absolute."                                              (p.103)

The paper prescribes two devices, both implemented here:

  * KOTLER'S OPPORTUNITY AND THREAT MATRICES — score each factor on probability of occurrence and
    likely impact. A factor scoring low on both "could even be removed entirely from the SWOT
    analysis due to their lack of relevance" (p.106). That is `Priority.drop`.
  * THE PERFORMANCE-IMPORTANCE MATRIX — score strengths and weaknesses on importance and on
    performance "relative to the firm's competitors", and attend to what is important and where
    performance is low (p.106). That is `Priority.focus`.

EVERY SCORE HERE IS MEASURED, never asserted. Importance is the PREVALENCE of a dimension across
the peer set that is already in the comparison matrix; impact is the SIZE of the subject's gap
against the peer median. Both come from `DimensionGap.competitor_values`, which the matrix already
computes. Where there are no peers, every axis is `unknown`, `focus` is False, and `basis` says so
out loud — the honest answer to "how does this compare?" when nothing was compared. This mirrors
the `claim_strength` ladder in swot.py: computed deterministically, never by the LLM.

Universal (D-8): keyed only off the generic dimension shape (bool/count/numeric) and the measured
peer values. No vertical, country, language or brand appears in any decision.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from statistics import median
from typing import Any, Optional

# Prevalence thresholds for "how much does this market care about this dimension". Config with a
# universal default, not logic: a dimension two-thirds of the measured peers have is table stakes;
# one a third have is emerging; below that it is a niche choice.
_HIGH_PREVALENCE = 0.66
_MEDIUM_PREVALENCE = 0.34

# Gap size (subject vs peer median, as a fraction of that median) for the impact axis.
_HIGH_GAP = 0.60
_MEDIUM_GAP = 0.25

_LEVEL_WEIGHT = {"high": 1.0, "medium": 0.6, "low": 0.25, "unknown": 0.0}

# The epistemic ladder already computed in swot.py. A self-claim is the paper's "factors represent
# opinions not fact" — the subject's own marketing copy about itself — so it is capped below
# anything that survived a comparison, no matter how confident its wording.
_CLAIM_WEIGHT = {"validated": 0.8, "directional_not_validated": 0.45,
                 "internally_supported": 0.2}

_NO_PEERS_BASIS = ("لا توجد مقارنة: لم يُقَس أي منافس على هذا البُعد، "
                   "والقوة أو الضعف لا يكونان إلا بالمقارنة.")
_SELF_CLAIM_BASIS = ("ادعاء البراند عن نفسه (من موقعه)، لم يُقارَن بأحد — "
                     "يُعرَض كوعد معلَن لا كميزة مُثبتة.")


@dataclass
class Priority:
    """Where one factor sits on the paper's two matrices.

    `importance`/`performance` are the Performance-Importance axes (strengths and weaknesses);
    `impact`/`probability` are the Opportunity/Threat axes. A factor carries whichever pair its
    quadrant uses; the other stays "unknown" rather than being faked.
    """
    importance: str = "unknown"     # high | medium | low | unknown
    performance: str = "unknown"    # ahead | parity | behind | unknown
    impact: str = "unknown"         # high | medium | low | unknown
    probability: str = "unknown"    # high | medium | low | unknown
    basis: str = ""                 # the measurement, in words, for the client to check
    rank: float = 0.0               # 0..1, sortable
    focus: bool = False             # important AND behind — "pay particular attention"
    drop: bool = False              # low on both axes — the paper says remove it
    peers_measured: int = 0


def _known(values: dict) -> list:
    return [v for v in (values or {}).values() if v is not None]


def _is_present(v: Any) -> bool:
    """Does this peer 'have' the dimension? Universal across bool and count/numeric values."""
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return v > 0
    return bool(str(v or "").strip())


def _numeric(v: Any) -> Optional[float]:
    if isinstance(v, bool):
        return 1.0 if v else 0.0
    if isinstance(v, (int, float)):
        return float(v)
    return None


def _level(fraction: float, high: float, medium: float) -> str:
    if fraction >= high:
        return "high"
    if fraction >= medium:
        return "medium"
    return "low"


def _importance_from_prevalence(gap: Any, *, ahead: bool) -> tuple[str, str, int]:
    """How much this dimension matters here — measured against the peer set.

    PREVALENCE AND RARITY ARE OPPOSITE SIGNALS, and which one applies depends on where the subject
    stands. Caught by an existing test after the first version used prevalence for everything and
    silently deleted "You have WhatsApp; Peer does not":

      * BEHIND or level — importance is PREVALENCE. Missing what most of the market offers is table
        stakes; missing what nobody offers costs nothing.
      * AHEAD — importance is RARITY. Being the only one with a capability is precisely what makes
        it a competitive advantage, which the paper names as the firm's task; being ahead on
        something everyone already has is barely a strength at all.
    """
    known = _known(getattr(gap, "competitor_values", {}) or {})
    if not known:
        return "unknown", _NO_PEERS_BASIS, 0
    present = sum(1 for v in known if _is_present(v))
    fraction = present / len(known)
    label = getattr(getattr(gap, "dimension", None), "label", "this dimension")
    if ahead:
        basis = (f"{len(known) - present} من {len(known)} منافس مقيس ليس عندهم «{label}» — "
                 "تميّز نادر." if present < len(known)
                 else f"كل المنافسين المقيسين ({len(known)}) عندهم «{label}» أيضاً.")
        return _level(1.0 - fraction, _HIGH_PREVALENCE, _MEDIUM_PREVALENCE), basis, len(known)
    basis = f"{present} من {len(known)} منافس مقيس عندهم «{label}»."
    return _level(fraction, _HIGH_PREVALENCE, _MEDIUM_PREVALENCE), basis, len(known)


def _impact_from_gap_size(gap: Any) -> tuple[str, str]:
    """How big the shortfall is against the peer median — the size of what is at stake."""
    known = [n for n in (_numeric(v) for v in _known(getattr(gap, "competitor_values", {}) or {}))
             if n is not None]
    subject = _numeric(getattr(gap, "subject_value", None))
    if not known or subject is None:
        return "unknown", ""
    peer_median = median(known)
    if peer_median <= 0:
        return ("low", "") if subject <= 0 else ("low", "")
    shortfall = max(0.0, (peer_median - subject) / peer_median)
    label = _level(shortfall, _HIGH_GAP, _MEDIUM_GAP)
    if shortfall <= 0:
        return "low", ""
    return label, f"الفجوة: {subject:g} مقابل وسيط المنافسين {peer_median:g}."


_VERDICT_TO_PERFORMANCE = {"ahead": "ahead", "behind": "behind", "par": "parity",
                           "parity": "parity"}


def score_gap(gap: Any) -> Priority:
    """Score one measured dimension gap on both of the paper's matrices."""
    performance = _VERDICT_TO_PERFORMANCE.get(str(getattr(gap, "verdict", "")), "unknown")
    importance, basis, peers = _importance_from_prevalence(gap, ahead=performance == "ahead")
    impact, impact_basis = _impact_from_gap_size(gap)

    # A threat/opportunity that most of the market has already acted on is not a forecast; its
    # probability IS its prevalence. Stated on the same measured footing as importance.
    probability = importance

    parts = [p for p in (basis, impact_basis) if p]
    rank = round(min(1.0, 0.65 * _LEVEL_WEIGHT[importance] + 0.35 * _LEVEL_WEIGHT[impact]), 4)
    if performance == "behind":
        rank = round(min(1.0, rank * 1.15), 4)          # a shortfall outranks a matching parity

    focus = importance == "high" and performance == "behind"
    # Kotler's removal rule — only ever applied to something that WAS measured. An unknown score
    # means "not compared", which is a disclosure, not a licence to delete the factor.
    drop = (peers > 0 and importance == "low"
            and impact in ("low", "unknown") and performance != "behind")

    return Priority(importance=importance, performance=performance, impact=impact,
                    probability=probability, basis="  ".join(parts), rank=rank,
                    focus=focus, drop=drop, peers_measured=peers)


def score_opinion_item(*, claim_strength: str = "internally_supported",
                       basis: str = "") -> Priority:
    """Score a factor that came from a CLAIM rather than a measured comparison.

    The brand's own value propositions are mined into Strengths by `brand_signals`. They are the
    paper's "factors represent opinions not fact", and the opinion is the subject's own about
    itself, so they are capped below anything that survived a comparison. They are not deleted —
    a promise a brand makes is real information about its positioning — but they can never sit at
    the top of the list as if they had been checked."""
    weight = _CLAIM_WEIGHT.get(str(claim_strength or ""), 0.2)
    if claim_strength == "validated":
        importance = "medium"
    elif claim_strength == "directional_not_validated":
        importance = "low"
    else:
        importance = "low"
    return Priority(importance=importance, performance="unknown", impact="unknown",
                    probability="unknown",
                    basis=basis or (_SELF_CLAIM_BASIS if claim_strength == "internally_supported"
                                    else ""),
                    rank=round(weight * 0.6, 4), focus=False, drop=False, peers_measured=0)
