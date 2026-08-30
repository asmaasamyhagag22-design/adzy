"""TOWS synthesis — turn a cited SWOT into ACTIONABLE strategies + a priority plan.

Adopted from the team's business-intelligence-platform strategy layer (TOWS matrix +
priority action plan + strategic posture), REBUILT on this repo's grounding discipline
— their version's grounding was prompt-only (no verification), so only the strategic-
synthesis THINKING is taken, not their trust model:

  - ANCHORS: every strategy names the SWOT item ids it pairs (S1/W2/O1/T3...). In the
    deterministic path anchors are valid BY CONSTRUCTION; an LLM-proposed strategy whose
    anchors aren't all real item ids is DROPPED (their tows_builder anchor-validation
    pattern, kept).
  - TWO TRUTH DOMAINS: a strategy RECOMMENDATION is judgment (like a poster design spec),
    but any FACTUAL claim inside LLM-crafted text must pass the Evidence Ledger. A
    strategy whose text carries an unsourced falsifiable claim falls back to the
    deterministic template text built VERBATIM from its cited SWOT items — the pairing
    survives, the fabrication doesn't.
  - GRACEFUL: no caller / LLM error -> deterministic template strategies. A standalone
    SWOT (no O/T) still yields a useful priority plan from its S/W items. Never raises.

Mapping:
    SO — use a Strength to pursue an Opportunity      (leverage)
    ST — use a Strength to counter a Threat           (defense)
    WO — fix a Weakness to unlock an Opportunity      (improvement)
    WT — mitigate where a Weakness meets a Threat     (contingency)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from pydantic import BaseModel

from .swot import SWOT, SWOTItem


# ---------------------------------------------------------------------------
# result types
# ---------------------------------------------------------------------------

@dataclass
class TowsStrategy:
    type: str                 # "SO" | "ST" | "WO" | "WT"
    title: str
    description: str
    anchors: List[str]        # SWOT item ids — always resolve to real items
    claim_strength: str       # weakest strength among the anchored items
    source: str = "rules"     # "rules" | "llm" | "llm_text_replaced" (Ledger caught a claim)


@dataclass
class PriorityAction:
    rank: int
    action: str
    horizon: str              # "now" | "next" | "later"
    anchors: List[str]
    rationale: str = ""


@dataclass
class TowsResult:
    strategies: List[TowsStrategy] = field(default_factory=list)
    priority_actions: List[PriorityAction] = field(default_factory=list)
    posture: str = "balanced"   # leverage | defense | improvement | contingency | balanced
    notes: List[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# item ids
# ---------------------------------------------------------------------------

_STRENGTH_ORDER = {"validated": 0, "directional_not_validated": 1, "internally_supported": 2}


def swot_item_ids(swot: SWOT) -> Dict[str, SWOTItem]:
    """Ids for the SWOT's items: S1..Sn / W1..Wn / O1..On / T1..Tn.

    The id now travels ON the item. It used to be derived from LIST POSITION, which made every
    TOWS strategy and priority action an index into a list nobody was allowed to reorder — and
    ordering the quadrants by priority is exactly what the analysis needs to do. A positional id
    would have re-pointed each strategy at a different factor with no error anywhere.

    Items produced before ids existed (or by a path not yet taught to assign one) still fall back
    to position, so a stored SWOT from an earlier run keeps working."""
    ids: Dict[str, SWOTItem] = {}
    for prefix, items in (("S", swot.strengths), ("W", swot.weaknesses),
                          ("O", swot.opportunities), ("T", swot.threats)):
        for i, item in enumerate(items, 1):
            ids[getattr(item, "id", "") or f"{prefix}{i}"] = item
    return ids


def _weakest(ids: Dict[str, SWOTItem], anchors: List[str]) -> str:
    strengths = [getattr(ids[a], "claim_strength", "internally_supported")
                 for a in anchors if a in ids]
    if not strengths:
        return "internally_supported"
    return max(strengths, key=lambda s: _STRENGTH_ORDER.get(s, 2))


def _clip(text: str, n: int = 90) -> str:
    text = (text or "").strip()
    return text if len(text) <= n else text[: n - 1].rstrip() + "…"


# ---------------------------------------------------------------------------
# deterministic pairing (grounded by construction — verbatim cited item texts)
# ---------------------------------------------------------------------------

_PAIRINGS: List[Tuple[str, str, str, str]] = [
    # (type, first prefix, second prefix, template)
    ("SO", "S", "O", "Use your strength — {a} — to pursue the opportunity: {b}"),
    ("ST", "S", "T", "Use your strength — {a} — to counter the threat: {b}"),
    ("WO", "W", "O", "Fix the weakness — {a} — to unlock the opportunity: {b}"),
    ("WT", "W", "T", "Mitigate the exposure where your weakness — {a} — meets the threat: {b}"),
]

_TYPE_TITLES = {"SO": "Leverage", "ST": "Defend", "WO": "Improve", "WT": "Protect"}
_MAX_PER_TYPE = {"SO": 3, "ST": 2, "WO": 2, "WT": 2}


def _quadrant(swot: SWOT, prefix: str) -> List[Tuple[str, SWOTItem]]:
    """(id, item) pairs — reading the id OFF the item, exactly as `swot_item_ids` does.

    `swot_item_ids` was taught to prefer the stored id; this function, which is what actually
    supplies the anchors for every strategy and every priority action, was left deriving them from
    `enumerate`. So the two disagreed the moment a quadrant was reordered by priority — and
    ordering by priority is the entire point of the prioritisation pass. The visible symptom: step
    1 of the plan cited the anchor that had been in position 1 BEFORE sorting, which after sorting
    was a different factor. Same fallback as `swot_item_ids` for items stored before ids existed."""
    items = {"S": swot.strengths, "W": swot.weaknesses,
             "O": swot.opportunities, "T": swot.threats}[prefix]
    return [(getattr(it, "id", "") or f"{prefix}{i}", it) for i, it in enumerate(items, 1)]


def _rules_strategies(swot: SWOT, ids: Dict[str, SWOTItem]) -> List[TowsStrategy]:
    out: List[TowsStrategy] = []
    for stype, p1, p2, template in _PAIRINGS:
        firsts, seconds = _quadrant(swot, p1), _quadrant(swot, p2)
        cap = _MAX_PER_TYPE[stype]
        for i in range(min(cap, len(firsts), len(seconds))):
            (aid, aitem), (bid, bitem) = firsts[i], seconds[i % len(seconds)]
            out.append(TowsStrategy(
                type=stype,
                title=f"{_TYPE_TITLES[stype]}: {_clip(aitem.text, 60)}",
                description=template.format(a=aitem.text, b=bitem.text),
                anchors=[aid, bid],
                claim_strength=_weakest(ids, [aid, bid]),
                source="rules",
            ))
    return out


def _rules_template_for(ids: Dict[str, SWOTItem], stype: str, anchors: List[str]) -> str:
    """The grounded fallback text for an LLM pairing whose crafted text failed the gate."""
    template = next(t for s, _p1, _p2, t in _PAIRINGS if s == stype)
    texts = [ids[a].text for a in anchors if a in ids]
    a = texts[0] if texts else ""
    b = texts[1] if len(texts) > 1 else a
    return template.format(a=a, b=b)


# ---------------------------------------------------------------------------
# optional LLM crafting (validated anchors + Ledger-gated text)
# ---------------------------------------------------------------------------

class _LLMStrategy(BaseModel):
    type: str                 # SO | ST | WO | WT
    title: str
    description: str
    anchors: list[str]


class _TowsResponse(BaseModel):
    strategies: list[_LLMStrategy]


def _items_block(ids: Dict[str, SWOTItem]) -> str:
    lines = []
    for iid, item in ids.items():
        lines.append(f"[{iid}] {item.text} (cites: {'; '.join(item.citation)})")
    return "\n".join(lines)


def _llm_strategies(swot: SWOT, ids: Dict[str, SWOTItem], caller) -> List[_LLMStrategy]:
    system = (
        "You are a senior brand strategist building a TOWS matrix. Pair the labelled SWOT "
        "items into concrete strategies: SO (strength->opportunity), ST (strength vs threat), "
        "WO (fix weakness to unlock opportunity), WT (mitigate weakness+threat). Rules: "
        "every strategy MUST cite the exact item ids it pairs in `anchors` (only ids from the "
        "list); do NOT invent facts, numbers, rankings, awards or claims not present in the "
        "items; keep titles short and actionable; at most 3 SO, 2 ST, 2 WO, 2 WT."
    )
    user = "SWOT items:\n" + _items_block(ids) + "\n\nReturn the TOWS strategies."
    resp, _usage = caller(system, user, _TowsResponse, group_name="tows_matrix")
    return list(resp.strategies)


def _text_is_grounded(text: str, ledger: Any) -> bool:
    """True iff the text carries NO unsourced falsifiable claim (same predicate as the
    strategy-calendar gate). Pure recommendation language passes untouched.

    Fails CLOSED: a ledger error returns False so the caller swaps in the grounded
    deterministic template (which cites real SWOT item texts) rather than shipping the
    LLM's UNVERIFIED strategy text. The pairing is kept either way — no strategy is lost —
    so failing closed costs only some LLM phrasing, never a fabricated claim through the moat.
    (When no ledger is supplied at all, gating is off by design and the text passes.)"""
    if ledger is None:
        return True
    try:
        return all(v.sourced for v in ledger.audit_text(text or ""))
    except Exception:  # noqa: BLE001 — on a gate error, fall back to the grounded template
        return False


# ---------------------------------------------------------------------------
# priority actions + posture
# ---------------------------------------------------------------------------

_TYPE_RANK = {"SO": 0, "ST": 1, "WO": 2, "WT": 3}
_TYPE_HORIZON = {"SO": "now", "ST": "next", "WO": "next", "WT": "later"}


def _priority_actions(strategies: List[TowsStrategy], swot: SWOT,
                      ids: Dict[str, SWOTItem], cap: int = 8) -> List[PriorityAction]:
    ordered = sorted(
        strategies,
        key=lambda s: (_TYPE_RANK.get(s.type, 9), _STRENGTH_ORDER.get(s.claim_strength, 2)),
    )
    actions = [
        PriorityAction(rank=i + 1, action=s.title, horizon=_TYPE_HORIZON.get(s.type, "next"),
                       anchors=list(s.anchors), rationale=s.description)
        for i, s in enumerate(ordered[:cap])
    ]
    if actions:
        return actions
    # Standalone degrade: no O/T -> no pairs. Still emit a useful, grounded plan
    # directly from the subject's own S/W items (fix gaps, double down on strengths).
    fallback: List[PriorityAction] = []
    for wid, w in _quadrant(swot, "W")[: cap // 2]:
        fallback.append(PriorityAction(
            rank=len(fallback) + 1, action=f"Fix: {_clip(w.text, 70)}", horizon="now",
            anchors=[wid], rationale=w.evidence))
    for sid, s in _quadrant(swot, "S")[: cap - len(fallback)]:
        fallback.append(PriorityAction(
            rank=len(fallback) + 1, action=f"Double down: {_clip(s.text, 70)}", horizon="next",
            anchors=[sid], rationale=s.evidence))
    return fallback


def _posture(strategies: List[TowsStrategy], swot: SWOT) -> str:
    if not strategies:
        return "improvement" if swot.weaknesses else "balanced"
    counts: Dict[str, int] = {}
    for s in strategies:
        counts[s.type] = counts.get(s.type, 0) + 1
    top = max(counts.values())
    leaders = [t for t, c in counts.items() if c == top]
    if len(leaders) != 1:
        return "balanced"
    return {"SO": "leverage", "ST": "defense", "WO": "improvement",
            "WT": "contingency"}[leaders[0]]


# ---------------------------------------------------------------------------
# public API
# ---------------------------------------------------------------------------

def build_tows(swot: SWOT, *, caller: Any = None, profile: Any = None) -> TowsResult:
    """SWOT -> TOWS strategies + ranked priority actions + posture.

    `caller` (optional, the shared Caller protocol) crafts strategy text; without it —
    or on any LLM/validation failure — the deterministic templates (verbatim cited item
    texts) are used. `profile` (optional, dict) builds an Evidence Ledger to gate the
    LLM text: an unsourced falsifiable claim swaps that strategy's text back to the
    grounded template (recorded in notes). Never raises.
    """
    result = TowsResult()
    ids = swot_item_ids(swot)
    if not ids:
        result.notes.append("SWOT has no items — nothing to synthesize.")
        return result

    ledger = None
    if profile is not None:
        try:
            from grounding import EvidenceLedger
            # P-24: wire the subject judge (from the caller build_tows already receives) so a
            # disjoint-subject superlative is rejected, not leniently accepted. caller=None ->
            # make_subject_judge(None) -> None -> the ledger's documented lenient mode (unchanged).
            _judge = None
            try:
                from grounding.subject_judge import make_subject_judge
                _judge = make_subject_judge(caller)
            except Exception:  # noqa: BLE001
                _judge = None
            ledger = EvidenceLedger.from_profile(profile, subject_judge=_judge)
        except Exception:  # noqa: BLE001
            ledger = None

    strategies: List[TowsStrategy] = []
    if caller is not None:
        try:
            counts: Dict[str, int] = {}
            for raw in _llm_strategies(swot, ids, caller):
                stype = (raw.type or "").strip().upper()
                if stype not in _TYPE_RANK:
                    continue
                anchors = [a.strip() for a in (raw.anchors or []) if a and a.strip()]
                # Anchor validation (their tows_builder pattern): every cited id must be
                # a REAL item; a strategy pointing at an invented item is dropped.
                if not anchors or any(a not in ids for a in anchors):
                    result.notes.append(
                        f"dropped an LLM strategy with unknown anchors: {anchors or '[]'}")
                    continue
                if counts.get(stype, 0) >= _MAX_PER_TYPE[stype]:
                    continue
                counts[stype] = counts.get(stype, 0) + 1
                title, desc, source = raw.title.strip(), raw.description.strip(), "llm"
                # Ledger gate: fabricated factual claims never ship — the pairing keeps
                # its grounded template text instead.
                if not (_text_is_grounded(title, ledger) and _text_is_grounded(desc, ledger)):
                    desc = _rules_template_for(ids, stype, anchors)
                    title = f"{_TYPE_TITLES[stype]}: {_clip(ids[anchors[0]].text, 60)}"
                    source = "llm_text_replaced"
                    result.notes.append(
                        "an LLM strategy carried an unsourced claim — text replaced with "
                        "the grounded template (pairing kept)")
                strategies.append(TowsStrategy(
                    type=stype, title=title, description=desc, anchors=anchors,
                    claim_strength=_weakest(ids, anchors), source=source))
        except Exception:  # noqa: BLE001 — LLM failure -> deterministic fallback
            strategies = []

    if not strategies:
        strategies = _rules_strategies(swot, ids)
        if caller is not None and not strategies:
            pass  # standalone SWOT (no O/T): priority actions below still cover S/W

    result.strategies = strategies
    result.priority_actions = _priority_actions(strategies, swot, ids)
    result.posture = _posture(strategies, swot)
    if not strategies:
        result.notes.append(
            "no TOWS pairs possible (Opportunities/Threats empty) — priority actions "
            "derive from the subject's own strengths/weaknesses instead.")
    return result
