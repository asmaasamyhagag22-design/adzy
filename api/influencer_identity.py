# -*- coding: utf-8 -*-
"""Who the brand's AI influencer is — described once, then locked.

Two halves, both from the owner's spec:

  * She describes the person → we render what she described.
  * "طول ما هو محتار — يعمل حاجة جينرال" → while she has not decided, produce someone sensible
    from what the brand ITSELF measures, rather than nothing (or a default face).

D-8 MATTERS MORE HERE THAN ANYWHERE. An influencer is a PERSON: a hardcoded market does not skew a
statistic, it casts the wrong human being into a client's advertising. The reel currently has
exactly that defect — `reel/render3/prompts.py` writes "Egyptian" into every character block and
`CharacterSheet` has no market field — so a Qatari café's protagonist is Egyptian.

The fix already exists in this codebase and was simply never used here: `poster.locale.brand_locale`
derives a casting instruction from the brand's MEASURED country and explicitly refuses a generic
regional stand-in ("NOT a generic 'Arab' or Gulf/Khaleeji look, NOT Western/European casting").
Both this module and the reel now read from that one measured signal instead of two tables.

Pure text assembly: no model call, no network, nothing to meter.
"""
from __future__ import annotations

from typing import Any

# What an untouched dropdown sends. These are the ABSENCE of a choice, not a description of a face,
# and letting them through produced personas like "any, none, brand persona".
_UNSET = {"", "none", "any", "unspecified", "default", "-"}

# Order matters: this is read as a sentence about a person, so age and gender lead.
_FEATURE_ORDER = ("age", "gender", "skin", "hair_length", "hair_color", "hair_style", "expression")


def _clean(value: Any) -> str:
    text = str(value or "").strip()
    return "" if text.lower() in _UNSET else text.replace("_", " ")


def _yes(value: Any) -> bool:
    return str(value or "").strip().lower() in ("yes", "true", "1")


def persona_from_owner(cfg: dict) -> str:
    """The person the owner described. Empty string when she has described nothing — an honest
    "undecided", which the caller turns into `persona_from_brand`."""
    bits = [c for c in (_clean(cfg.get(k)) for k in _FEATURE_ORDER) if c]
    if _yes(cfg.get("beard")):
        bits.append("with a beard")
    if _yes(cfg.get("glasses")):
        bits.append("wearing glasses")
    named = _clean(cfg.get("persona"))
    described = ", ".join(bits)
    out = (f"{named} — {described}" if named and described else (named or described))
    # HER NOTE INFLUENCES THE RENDER (owner, 2026-07-26: «يكتب ملحوظه تأثر»). Appended to the
    # persona sentence, which is the exact text `_render_portrait` puts in the image prompt — so
    # a note that nothing consumed would be the same lie as the sfx cues were.
    note = _clean(cfg.get("note"))
    if note:
        out = f"{out}. Note from the brand owner: {note}" if out \
            else f"Note from the brand owner: {note}"
    return out


def _fv(profile: Any, key: str) -> str:
    v = profile.get(key) if isinstance(profile, dict) else None
    if isinstance(v, dict):
        v = v.get("value")
    return str(v or "").strip()


def persona_from_brand(profile: dict) -> str:
    """Someone plausible for THIS brand, built only from measured fields.

    Every ingredient is evidence the profile already carries: the country the brand actually
    operates in, who it says it talks to, how it speaks, and what it sells. Nothing is asserted
    that was not measured — a brand with no known country gets a person with no stated nationality
    rather than a default one."""
    from poster.locale import brand_locale

    country, casting = brand_locale(profile or {})
    parts: list[str] = []
    if country:
        parts.append(f"a person who genuinely belongs in {country}")
    else:
        parts.append("a person who genuinely belongs in this brand's own market")

    audience = _fv(profile, "audience_type")
    if audience:
        parts.append(f"someone this brand's {audience} audience would recognise as one of them")
    tone = _fv(profile, "tone_of_voice")
    if tone:
        parts.append(f"presence matching the brand's {tone} tone")
    market = _fv(profile, "market_category")
    if market:
        parts.append(f"credible as a real customer of {market}")

    text = "; ".join(parts)
    # The casting instruction is the part that actually stops a generic regional stand-in, and it
    # is derived from the measured country — so it travels with the persona.
    return f"{text}\n{casting}" if casting else text


def identity_lock_text(persona: str, *, market: str = "") -> str:
    """The sentence that stops the model re-sampling a new face on the next render.

    Same shape as `reel/render3/prompts.character_block` — which is the wording that measurably
    holds an identity across shots — minus its hardcoded nationality. The market comes in as an
    argument so one brand's person is never another brand's person."""
    who = str(persona or "").strip() or "the brand's presenter"
    belongs = f", who belongs in {market}" if str(market or "").strip() else ""
    return (
        f"PRESENTER — the SAME person as in the attached reference image; identity must match it "
        f"exactly (face, hair, build): {who}{belongs}.\n"
        f"Do not change their face, their hair, or their identity in any way."
    )
