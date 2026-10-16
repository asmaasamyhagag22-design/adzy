# -*- coding: utf-8 -*-
"""Does this sentence MEAN anything? The gate nobody had written.

OWNER (2026-07-26), reading a shipped headline: «هو إيه "وقّفي البقعة"؟ مش فاهمة أصلاً».

Count what already judged that line and let it through: the language lock (it is Arabic ✓), the
Evidence Ledger (it claims nothing falsifiable ✓), the therapeutic-claim softener (no medical
verb ✓), the contrast gate (4.6:1 ✓), the stray-text gate (we authored it ✓), the art critic (it
liked the composition ✓). Six gates, and not one of them asks the only question a reader asks.

«وقّفي البقعة في مكانها» is grammatical, grounded, safe, legible — and meaningless. So this gate
asks a native speaker of the copy's own language, per field, whether the line is something a
person would actually say, and takes a concrete rewrite when it is not.

TRI-STATE: no judge -> `checked=False, passed=None`. A gate that cannot run never reports a pass.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_SYSTEM = (
    "You are a NATIVE speaker of {lang} reading advertising copy for the first time. For EACH "
    "field, answer one question honestly: would a real person say this, and does it MEAN "
    "something clear?\n"
    "natural = false when the line is: word-salad or a literal translation from another "
    "language; a metaphor that does not work in this language; grammatically odd; or simply "
    "confusing about what the product does. Being short, bold or playful is FINE — being "
    "meaningless is not.\n"
    "When natural = false, write `rewrite`: the SAME message said the way a native speaker "
    "would, same length or shorter, same language, no new facts, no claims that were not "
    "already there. When natural = true, leave `rewrite` empty."
)


@dataclass
class SenseVerdict:
    passed: Optional[bool] = None
    checked: bool = False
    failed: List[str] = field(default_factory=list)
    rewrites: Dict[str, str] = field(default_factory=dict)
    notes: Dict[str, str] = field(default_factory=dict)


class _Schema:
    _model = None

    @classmethod
    def get(cls):
        if cls._model is None:
            from pydantic import BaseModel

            class FieldVerdict(BaseModel):
                field: str
                natural: bool
                issue: str = ""
                rewrite: str = ""

            class SenseResponse(BaseModel):
                fields: list[FieldVerdict]

            cls._model = SenseResponse
        return cls._model


def sense_check(copy_fields: dict, *, language: str = "ar", profile: Any = None,
                caller: Any = None) -> SenseVerdict:
    """Judge each non-empty copy field for MEANING. Never raises."""
    v = SenseVerdict()
    fields = {k: str(val).strip() for k, val in (copy_fields or {}).items()
              if str(val or "").strip()}
    if not fields:
        return v                                   # nothing to judge -> could not verify
    if caller is None:
        try:
            from business_profile.llm import default_caller
            caller = default_caller(strong=False)
        except Exception:  # noqa: BLE001
            caller = None
    if caller is None:
        return v

    code = str(language or "").lower()[:2]
    if code == "ar":
        try:
            from poster.locale import arabic_dialect_for
            lang = arabic_dialect_for(profile or {})
        except Exception:  # noqa: BLE001 — keep this best-effort judge non-raising
            lang = "natural conversational Arabic without assuming a country-specific dialect"
    elif code == "en":
        lang = "natural English"
    else:
        lang = "the copy's own language"
    user = "\n".join(f"{k}: {val}" for k, val in fields.items())
    try:
        resp, _u = caller(_SYSTEM.format(lang=lang), user, _Schema.get(),
                          group_name="copy_sense")
    except Exception:  # noqa: BLE001
        logger.warning("copy sense check unavailable")
        return v

    def _attr(item, key, default=""):
        """Judges come back as pydantic objects in production and as plain dicts from stubs."""
        if isinstance(item, dict):
            return item.get(key, default)
        return getattr(item, key, default)

    for item in (getattr(resp, "fields", None) or []):
        name = str(_attr(item, "field") or "").strip()
        if name not in fields:
            continue
        if bool(_attr(item, "natural", True)):
            continue
        v.failed.append(name)
        v.notes[name] = str(_attr(item, "issue") or "")
        rewrite = str(_attr(item, "rewrite") or "").strip()
        if rewrite and rewrite != fields[name]:
            v.rewrites[name] = rewrite

    v.checked = True
    v.passed = not v.failed
    if v.failed:
        logger.info("[copy-sense] rewrote %s", ", ".join(v.failed))
    return v
