# -*- coding: utf-8 -*-
"""The critic's notes, applied — so the gate improves the film instead of only refusing it.

OWNER (2026-08-01), on why a hand-driven tool beats ours: «لما آخده لـGoogle Flow بنفس التول اللي
معايا طلع تحفة فنية». The model is the same. What is different is that a person WATCHES, forms a
note, and changes the thing the note is about — then looks again.

`reel/film_critic.py` closed half of that: somebody now watches the finished film and says what
is wrong with it, in fixes concrete enough to act on ("have her take the sachet from the
pharmacist"). But a verdict that only rejects burns a whole render to tell you something a
sentence could have changed. This module is the other half — the notes become a REVISED PLAN.

THREE RULES, each of them a way this could go wrong:

  * A NOTE CHANGES THE SCENE IT IS ABOUT, and nothing else. The critic numbers every fix against
    the scene list it was shown, so a note lands where it belongs rather than being pasted over
    the whole film. A fix numbered 0 is about the film as a whole and joins every scene's brief.
  * THE REVISION IS ADDITIVE, never a rewrite. The plan passed the coherence gate — its arc, its
    motif, its casting and its language are already correct. Discarding all of that to act on one
    note about the ending would trade a specific defect for an unknown one.
  * ONE PASS. A loop that re-renders until a model is happy is a loop that spends $1.60 a clip
    until something else stops it. The revised film is rendered ONCE and the better of the two is
    kept, judged by the same critic on the same axes.

Deterministic: no model is called here. The critic already did the judging; this is the edit.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)

# A note the director must obey verbatim, marked so a reader of the revised plan can see which
# lines came from the critic and which the director wrote.
_MARK = "DIRECTOR'S NOTE (apply this exactly):"


def _scene_notes(fixes: list, scenes_for: list, n_scenes: int) -> dict:
    """{scene_index (0-based): [note, ...]} — plus the whole-film notes under key -1.

    A fix whose scene number is out of range is treated as a whole-film note rather than dropped:
    a misnumbered note is still a real observation, and losing it silently is the failure this
    repo keeps finding."""
    out: dict = {}
    for i, fix in enumerate(fixes or []):
        text = str(fix or "").strip()
        if not text:
            continue
        raw = int(scenes_for[i]) if i < len(scenes_for or []) else 0
        idx = raw - 1 if 1 <= raw <= n_scenes else -1
        out.setdefault(idx, []).append(text)
    return out


def revise_plan(creative: Any, verdict: Any) -> Optional[Any]:
    """A copy of the plan with the critic's notes attached to the scenes they name.

    Returns None when there is nothing to act on — no fixes, or a verdict nobody produced. The
    caller must treat None as "do not re-render": spending a second render on no instruction is
    how a revision loop becomes an expensive no-op.
    """
    fixes = [f for f in (getattr(verdict, "fixes", None) or []) if str(f or "").strip()]
    if not fixes or not getattr(verdict, "checked", False):
        return None
    scenes = list(getattr(creative, "scenes", None) or [])
    if not scenes:
        return None

    notes = _scene_notes(fixes, getattr(verdict, "fix_scenes", None) or [], len(scenes))
    film_notes = notes.get(-1, [])
    if not any(k >= 0 for k in notes) and not film_notes:
        return None

    revised = []
    for i, sc in enumerate(scenes):
        mine = list(notes.get(i, [])) + list(film_notes)
        if not mine:
            revised.append(sc)
            continue
        prompt = str(getattr(sc, "veo_prompt", "") or "").rstrip()
        # APPENDED, NOT SUBSTITUTED. The scene description leads — a model reads the opening of a
        # prompt as the subject, and a note promoted to the front produces a beautiful shot of
        # the note instead of the scene.
        addition = "\n".join(f"{_MARK} {n}" for n in mine)
        revised.append(sc.model_copy(update={"veo_prompt": f"{prompt}\n{addition}"}))
    out = creative.model_copy(update={"scenes": revised})
    logger.info("[revision] %s note(s) applied across %s scene(s)",
                len(fixes), sum(1 for k in notes if k >= 0) or len(scenes))
    return out


def better_of(first: Any, second: Any) -> str:
    """'first' or 'second' — which film to keep, by the same critic on the same axes.

    A REVISION IS NOT AUTOMATICALLY AN IMPROVEMENT. The second render is a fresh roll of the same
    dice with one extra instruction: it can land the note and lose something the first one had.
    A tie keeps the FIRST, because the second cost money to discover it was no better and
    preferring it would make that spend look like progress.
    """
    def _rank(v) -> tuple:
        if v is None or not getattr(v, "checked", False):
            return (-1, -1)
        hard = sum(1 for a in ("product_causes_resolution", "problem_is_attractive",
                               "speaks_audience_language", "everything_belongs_to_brand")
                   if getattr(v, a, False))
        return (hard, int(getattr(v, "score", 0) or 0))

    return "second" if _rank(second) > _rank(first) else "first"
