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

# HOW MANY PEERS IT TOOK TO SAY THIS. MEASURED DEFECT: one peer who had a capability produced
# `rank=1.00 importance=high focus=True`, while three peers of whom one had it produced
# `rank=0.29 low False` — weaker evidence shouting louder. That is the same shape as the
# `score_size_similarity` bug this project already paid for (a "peer median" computed from a pool
# of one is the subject's own value, so the ratio was a guaranteed 1.0).
#
# Two dampeners, both applied to a MEASURED prevalence rather than replacing it:
#   * a single observation cannot call a dimension table stakes, so importance is capped below
#     `high` — which also makes `focus` (high AND behind) unreachable on one peer;
#   * rank is scaled by how much of the pool was actually there.
_IMPORTANCE_CAP_BY_PEERS = {1: "medium"}          # 2+ peers: uncapped
_EVIDENCE_WEIGHT = {0: 0.0, 1: 0.6, 2: 0.85}      # 3+ peers: 1.0
_RANK_ORDER = ["unknown", "low", "medium", "high"]


def _cap_importance(level: str, peers: int) -> str:
    cap = _IMPORTANCE_CAP_BY_PEERS.get(peers)
    if not cap or level == "unknown":
        return level
    return cap if _RANK_ORDER.index(level) > _RANK_ORDER.index(cap) else level


def _thin_pool_note(peers: int) -> str:
    return (f"مقيس على منافس واحد فقط — إشارة استرشادية لا حكم."
            if peers == 1 else "")

# The epistemic ladder already computed in swot.py. A self-claim is the paper's "factors represent
# opinions not fact" — the subject's own marketing copy about itself — so it is capped below
# anything that survived a comparison, no matter how confident its wording.
_CLAIM_WEIGHT = {"validated": 0.8, "directional_not_validated": 0.45,
                 "internally_supported": 0.2}

_NO_PEERS_BASIS = ("لا توجد مقارنة: لم يُقَس أي منافس على هذا البُعد، "
                   "والقوة أو الضعف لا يكونان إلا بالمقارنة.")
_SELF_CLAIM_BASIS = ("ادعاء البراند عن نفسه (من موقعه)، لم يُقارَن بأحد — "
                     "يُعرَض كوعد معلَن لا كميزة مُثبتة.")
# A reading taken OFF the site, not a claim the brand makes. "No CTA on the page" is a measurement;
# calling it the brand's own claim is a true sentence about a different kind of item.
_SITE_OBSERVATION_BASIS = ("قراءة مباشرة من موقعك، لم تُقارَن بأي منافس — "
                           "معلومة عن الموقع لا حكم على وضعك في السوق.")


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


def _is_magnitude(gap: Any) -> bool:
    """Is this dimension a QUANTITY rather than a yes/no?

    `Dimension.kind` already carries it ("bool" | "count" | "numeric" | "categorical"), so the
    scorer reads the shape the matrix declared instead of guessing from the value."""
    kind = str(getattr(getattr(gap, "dimension", None), "kind", "") or "")
    if kind in ("count", "numeric"):
        return True
    if kind:
        return False
    # No declared kind (a synthetic gap): fall back to the values themselves.
    vals = [v for v in _known(getattr(gap, "competitor_values", {}) or {})]
    vals.append(getattr(gap, "subject_value", None))
    return any(isinstance(v, (int, float)) and not isinstance(v, bool) for v in vals)


def _relative_delta(gap: Any) -> Optional[tuple[float, float, float]]:
    """(signed relative delta, subject, peer median) — or None when it cannot be measured."""
    known = [n for n in (_numeric(v) for v in _known(getattr(gap, "competitor_values", {}) or {}))
             if n is not None]
    subject = _numeric(getattr(gap, "subject_value", None))
    if not known or subject is None:
        return None
    peer_median = median(known)
    denom = peer_median if peer_median > 0 else 1.0
    return (subject - peer_median) / denom, subject, peer_median


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

    RARITY IS NOT THE SAME QUESTION ON A COUNT. MEASURED: with the subject on 50 and peers on
    {1, 2}, the boolean reading of rarity said "all measured competitors have Trust too" — true,
    and an answer to a different question than the one asked, which cost the client a 25x lead to
    Kotler's removal rule. On a quantity, being ahead is a matter of BY HOW MUCH, so the lead is
    measured against the peer median on the same scale the impact axis uses.
    """
    known = _known(getattr(gap, "competitor_values", {}) or {})
    if not known:
        return "unknown", _NO_PEERS_BASIS, 0
    present = sum(1 for v in known if _is_present(v))
    fraction = present / len(known)
    label = getattr(getattr(gap, "dimension", None), "label", "this dimension")

    if ahead and _is_magnitude(gap):
        measured = _relative_delta(gap)
        if measured is not None:
            lead, subject, peer_median = measured
            basis = (f"{subject:g} مقابل وسيط المنافسين {peer_median:g} على «{label}» — "
                     f"تفوّق مقيس.")
            return _level(max(0.0, lead), _HIGH_GAP, _MEDIUM_GAP), basis, len(known)

    if ahead:
        basis = (f"{len(known) - present} من {len(known)} منافس مقيس ليس عندهم «{label}» — "
                 "تميّز نادر." if present < len(known)
                 else f"كل المنافسين المقيسين ({len(known)}) عندهم «{label}» أيضاً.")
        return _level(1.0 - fraction, _HIGH_PREVALENCE, _MEDIUM_PREVALENCE), basis, len(known)
    basis = f"{present} من {len(known)} منافس مقيس عندهم «{label}»."
    return _level(fraction, _HIGH_PREVALENCE, _MEDIUM_PREVALENCE), basis, len(known)


def _impact_from_gap_size(gap: Any) -> tuple[str, str]:
    """How big the distance is against the peer median — the size of what is at stake.

    SYMMETRIC ON PURPOSE. The first version clamped the delta at zero, so a subject standing far
    ahead scored `impact=low` — indistinguishable from a subject standing exactly level. A lead of
    25x and a dead heat are not the same amount at stake, and the removal rule reads this axis."""
    measured = _relative_delta(gap)
    if measured is None:
        return "unknown", ""
    delta, subject, peer_median = measured
    if peer_median <= 0:
        # No scale to be relative to. "1 against a median of 0" is a lead, but its SIZE cannot be
        # expressed as a fraction of nothing, and D-8 forbids an absolute yardstick that would mean
        # different things for CTAs and for review counts. Caught by an existing test when the
        # symmetric delta briefly reported this case as `high`. The rarity axis still carries it.
        return "unknown", ""
    size = _level(abs(delta), _HIGH_GAP, _MEDIUM_GAP)
    if abs(delta) <= 0:
        return "low", ""
    word = "الفجوة" if delta < 0 else "الفارق لصالحك"
    return size, f"{word}: {subject:g} مقابل وسيط المنافسين {peer_median:g}."


_VERDICT_TO_PERFORMANCE = {"ahead": "ahead", "behind": "behind", "par": "parity",
                           "parity": "parity"}


def _whitespace_priority(gap: Any, peers: int) -> Priority:
    """An unoccupied dimension — nobody measured offers it.

    MEASURED DEFECT: `whitespace` was scored on the same two axes as everything else, and both
    are structurally zero for it — prevalence is 0 because nobody has it, and the peer median is 0
    so the gap is 0 — so Kotler's removal rule fired on EVERY whitespace opportunity. The matrix
    that is the whole reason this module exists could never once fire on a measured opportunity.

    What is genuinely known here is the supply side (nobody offers it) and not the demand side
    (whether anyone wants it), so the basis says both — the unmeasured half out loud."""
    label = getattr(getattr(gap, "dimension", None), "label", "this dimension")
    basis = (f"لا أحد من المنافسين المقيسين ({peers}) يقدّم «{label}» — مساحة غير مشغولة. "
             "حجم الطلب عليها غير مقيس.")
    note = _thin_pool_note(peers)
    return Priority(importance="medium", performance="unknown", impact="unknown",
                    probability="unknown",
                    basis="  ".join(p for p in (basis, note) if p),
                    rank=round(0.65 * _LEVEL_WEIGHT["medium"] * _EVIDENCE_WEIGHT.get(peers, 1.0),
                               4),
                    focus=False, drop=False, peers_measured=peers)


def score_gap(gap: Any) -> Priority:
    """Score one measured dimension gap on both of the paper's matrices."""
    verdict = str(getattr(gap, "verdict", ""))
    performance = _VERDICT_TO_PERFORMANCE.get(verdict, "unknown")
    peer_count = len(_known(getattr(gap, "competitor_values", {}) or {}))
    if verdict == "whitespace" and peer_count:
        return _whitespace_priority(gap, peer_count)

    importance, basis, peers = _importance_from_prevalence(gap, ahead=performance == "ahead")
    importance = _cap_importance(importance, peers)
    impact, impact_basis = _impact_from_gap_size(gap)

    # `probability` IS NOT `importance`. It used to be assigned from it outright, so the client was
    # shown two axes of which one was a copy — a declared measurement that measures nothing, which
    # is worse than an absent one. Probability is about whether something WILL occur; a comparative
    # gap is an observation of the present, and nothing here forecasts. It stays unknown until a
    # generator with a genuine occurrence signal (a trend, an active ad campaign) fills it in.
    probability = "unknown"

    parts = [p for p in (basis, impact_basis, _thin_pool_note(peers)) if p]
    rank = round(min(1.0, 0.65 * _LEVEL_WEIGHT[importance] + 0.35 * _LEVEL_WEIGHT[impact]), 4)
    if performance == "behind":
        rank = round(min(1.0, rank * 1.15), 4)          # a shortfall outranks a matching parity
    rank = round(rank * _EVIDENCE_WEIGHT.get(peers, 1.0), 4)

    focus = importance == "high" and performance == "behind"
    # Kotler's removal rule — only ever applied to something that WAS measured. An unknown score
    # means "not compared", which is a disclosure, not a licence to delete the factor.
    drop = (peers > 0 and importance == "low"
            and impact in ("low", "unknown") and performance != "behind")

    return Priority(importance=importance, performance=performance, impact=impact,
                    probability=probability, basis="  ".join(parts), rank=rank,
                    focus=focus, drop=drop, peers_measured=peers)


def score_opinion_item(*, claim_strength: str = "internally_supported",
                       basis: str = "", kind: str = "self_claim") -> Priority:
    """Score a factor that came from a CLAIM rather than a measured comparison.

    The brand's own value propositions are mined into Strengths by `brand_signals`. They are the
    paper's "factors represent opinions not fact", and the opinion is the subject's own about
    itself, so they are capped below anything that survived a comparison. They are not deleted —
    a promise a brand makes is real information about its positioning — but they can never sit at
    the top of the list as if they had been checked.

    `kind` DISTINGUISHES THE TWO REASONS AN ITEM CAN BE UNCOMPARED, because they are not the same
    statement. MEASURED by running the page's own renderer over the shipped nanocare payload: all
    four site-readiness items came out reading «ادعاء البراند عن نفسه (من موقعه)» under titles like
    "No clear call-to-action on the page". A missing CTA is something the crawler READ off the
    site — no brand advertises its own missing CTA — so that sentence was true of a different kind
    of item entirely: failure class #6, a true statement answering a different question. The score
    is identical either way; only the sentence the client reads changes."""
    weight = _CLAIM_WEIGHT.get(str(claim_strength or ""), 0.2)
    if claim_strength == "validated":
        importance = "medium"
    elif claim_strength == "directional_not_validated":
        importance = "low"
    else:
        importance = "low"
    if basis:
        written = basis
    elif kind == "site_observation":
        written = _SITE_OBSERVATION_BASIS
    elif claim_strength == "internally_supported":
        written = _SELF_CLAIM_BASIS
    else:
        written = ""
    return Priority(importance=importance, performance="unknown", impact="unknown",
                    probability="unknown", basis=written,
                    rank=round(weight * 0.6, 4), focus=False, drop=False, peers_measured=0)
