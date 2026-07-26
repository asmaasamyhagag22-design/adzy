# -*- coding: utf-8 -*-
"""No vacuous passes. A gate that measured nothing says `null`, and the artifact gets ONE verdict.

DIRECTIVE §6, and the defect that made the whole audit unreliable rather than merely incomplete:

    scene   frames with a detected face      audit said
    S0      4 / 60  (7%)                     faces_intact_across_motion: true
    S1      0 / 65                           true
    S4      0 / 65                           true
    S6      0 / 65                           true
    S7      2 / 83  (2%)                     true

«`true` meaning "I verified this" and `true` meaning "there was nothing to verify" are
indistinguishable. That makes the field worthless.»

The existing `SceneQAVerdict` / `SeedVerdict` already got the TRI-STATE right — `overall_pass`
is `Optional[bool]` and `checked` says whether anyone looked. What they never carried is the
MEASUREMENT: no `observed`, no `threshold`, no count of what was actually inspected. So this
module WRAPS them rather than replacing them: `gate(...)` takes a verdict and the numbers behind
it, and refuses to let a pass through without them.

`artifact_verdict` closes the other half: the audit reported `copy_passes: true` beside four
failing scenes, a morphing product and `fallback_used: true`, with no combined decision anywhere.
A reel whose product turns into a different object must not be able to leave under any
true-valued field.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)

METHODS = ("code", "vlm", "hitl")

# Gates where a model's opinion alone may not grant a pass: rights, safety and claims. A vision
# model saying "looks clean" is evidence, not proof, and these are the ones that cost money or
# reputation when they are wrong.
SAFETY_GATES = {
    "third_party_mark", "invented_product", "garbled_text", "claims", "trademark",
    "music_rights", "disclosure", "product_faithful",
}


def gate(name: str, *, value: Optional[bool], method: str = "code", measure: str = "",
         threshold: Any = None, observed: Any = None, units_checked: Optional[dict] = None,
         evidence_count: int = 0, reason: str = "", safety: Optional[bool] = None) -> dict:
    """One gate result, in the shape the directive requires.

    Two rules are enforced HERE so no caller can forget them:

      1. A PASS WITHOUT AN OBSERVATION IS NOT A PASS. `value=True` with `observed=None` becomes
         `None` — this is the exact shape that reported "faces intact" on scenes containing no
         faces at all.
      2. A VLM VERDICT ALONE CANNOT PASS A SAFETY GATE. It can fail one (a model that spots a
         problem has found evidence), but a clean opinion is not proof.
    """
    units = dict(units_checked or {})
    meth = str(method or "code").lower()
    if meth not in METHODS:
        meth = "code"
    is_safety = SAFETY_GATES.__contains__(name) if safety is None else bool(safety)

    out_value: Optional[bool] = value
    why = reason

    if value is True and observed is None:
        out_value = None
        why = why or ("nothing was measured — a pass with no observation is not a pass "
                      f"(units checked: {units or 'none'})")
    elif value is True and is_safety and meth == "vlm":
        out_value = None
        why = why or ("vlm judgement alone cannot pass a safety-relevant gate; it needs a code "
                      "measurement or a human")

    if out_value is None and not why:
        why = "not measurable"

    result = {
        "gate": str(name),
        "value": out_value,
        "reason": why,
        "method": meth,
        "measure": str(measure or ""),
        "threshold": threshold,
        "observed": observed,
        "units_checked": units,
        "evidence_count": int(evidence_count or 0),
    }
    if out_value is None:
        logger.info("[gate] %s -> null (%s)", name, why)
    return result


def artifact_verdict(gates, *, fallback_used: bool = False) -> dict:
    """ONE blocking decision for the whole artifact.

    `False` when any gate failed — a single failure blocks, regardless of how many others
    passed. `None` when nothing could be verified at all: an artifact whose every gate is
    unmeasurable is UNVERIFIED, never approved. `True` only when at least one gate really
    passed and none failed.

    `fallback_used` is carried through rather than folded in: it is not a failure by itself, but
    a consumer that sees a pass beside a degraded path deserves to know which path ran."""
    rows = [g for g in (gates or []) if isinstance(g, dict)]
    failed = [g["gate"] for g in rows if g.get("value") is False]
    passed = [g["gate"] for g in rows if g.get("value") is True]
    nulls = [g["gate"] for g in rows if g.get("value") is None]

    if failed:
        value: Optional[bool] = False
        reason = f"{len(failed)} gate(s) failed: " + ", ".join(failed[:6])
    elif passed:
        value = True
        reason = f"{len(passed)} gate(s) passed, {len(nulls)} unverified"
    else:
        value = None
        reason = "nothing could be verified"

    return {
        "gate": "artifact_verdict",
        "value": value,
        "reason": reason,
        "method": "code",
        "measure": "conjunction",
        "threshold": "no failing gate",
        "observed": {"passed": len(passed), "failed": len(failed), "unverified": len(nulls)},
        "units_checked": {"gates": len(rows)},
        "evidence_count": sum(int(g.get("evidence_count") or 0) for g in rows),
        "blocking": failed,
        "unverified": nulls,
        "fallback_used": bool(fallback_used),
    }
