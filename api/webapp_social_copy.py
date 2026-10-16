# -*- coding: utf-8 -*-
"""Social captions — the words the owner actually posts with.

OWNER (2026-07-25): «عايزة فوق الريل والبوست يبقى مكتوب كلام زي السوشيال ميديا بالظبط لأن خلاص
هبدأ أبوست، ويبقى في اوبشن للكونتنت بس من غير ريل ولا صورة».

Before this module the product's entire "social copy" was one naive hashtag built from the brand
name in the calendar. A caption is a different craft from a poster headline: a scroll-stopping
hook line, a short body, one CTA, and hashtags — in the language the owner chose.

GROUNDING IS UNCHANGED BY THE SURFACE. A caption is still COPY: every line runs through the same
EvidenceLedger gate the poster's copy uses, so an unsourced superlative ("الأفضل في مصر") falls to
its grounded fallback instead of shipping just because the destination is Instagram rather than a
poster. When the gate is unenforceable (no judge — the documented lenient mode) that is already
disclosed at the SWOT layer; nothing here claims more than the poster path does.

Content-only mode is the same generator with NO renderer in the call path at all — zero image
spend, by construction rather than by flag-checking.
"""
from __future__ import annotations

import logging
import re
from typing import Any, Optional

logger = logging.getLogger(__name__)

_SYSTEM = (
    "You are a senior social media copywriter. Write ONE post caption for the brand below, in "
    "{lang_name}. STRUCTURE: a scroll-stopping HOOK line (<= 8 words, may use one emoji), a BODY "
    "of 1-2 short sentences grounded ONLY in the evidence provided, ONE call-to-action line, and "
    "3-6 HASHTAGS mixing the brand and its category (in the caption's language; no spaces inside "
    "a tag). VOICE: talk like the brand's own social account — direct, warm, zero corporate "
    "filler. NEVER invent offers, prices, percentages or claims that are not in the evidence."
)

def _fv(profile: dict, key: str) -> str:
    v = (profile or {}).get(key)
    if isinstance(v, dict):
        v = v.get("value")
    return str(v or "").strip()


def _evidence_lines(profile: dict) -> str:
    """The grounded facts the caption may use — the same fields the poster concept trusts."""
    lines = []
    for key in ("name", "tagline", "description", "category"):
        val = _fv(profile, key)
        if val:
            lines.append(f"{key}: {val}")
    vps = (profile or {}).get("value_propositions") or []
    for vp in vps[:4]:
        val = vp.get("value") if isinstance(vp, dict) else vp
        if val:
            lines.append(f"value: {val}")
    offerings = (profile or {}).get("offerings") or []
    names = []
    for o in offerings[:6]:
        n = (o.get("name") if isinstance(o, dict) else o) or ""
        n = n.get("value") if isinstance(n, dict) else n
        if n:
            names.append(str(n))
    if names:
        lines.append("offerings: " + ", ".join(names))
    return "\n".join(lines)


class _CaptionSchema:
    """Pydantic response model, built lazily so importing this module needs nothing heavy."""

    _model = None

    @classmethod
    def get(cls):
        if cls._model is None:
            from pydantic import BaseModel

            class Caption(BaseModel):
                hook: str
                body: str
                cta: str
                hashtags: list[str]

            cls._model = Caption
        return cls._model


_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?؟])\s+|[\r\n]+")
_CAMEL_CASE_BOUNDARY_RE = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")


def _norm_role_text(text: str) -> str:
    """Compare two roles by their proposition, ignoring only cosmetic differences.

    Whitespace and case ONLY. This deliberately does not try to decide that two differently-worded
    sentences mean the same thing — that needs real semantics, and a keyword list pretending to
    have them is exactly what this codebase refuses to grow.
    """
    return " ".join(str(text or "").split()).casefold()


def _claim_role(text: str, taken: Optional[set]) -> str:
    """Let a proposition fill ONE role. A later role that lands on the same words gets nothing.

    Empty is the honest answer there: the caption's structure promises a hook, a body and a CTA,
    and printing one sentence three times does not deliver three of them. Inventing a different
    claim to fill the gap would be worse still — it is the one thing the Ledger exists to prevent.
    """
    if taken is None:
        return text
    key = _norm_role_text(text)
    if not key:
        return text
    if key in taken:
        return ""
    taken.add(key)
    return text


def _gate_text(profile: dict, text: str, *, use_fallback: bool = True,
               taken: Optional[set] = None) -> str:
    """The poster's grounding rule, applied to the caption: a claim the Ledger cannot source is
    replaced by trusted profile copy rather than shipped. Hashtags opt out of textual fallback and
    are dropped when unsupported, because turning a tagline into a tag would change its meaning.

    `taken` carries the propositions earlier ROLES in this caption already used. There is only one
    tagline, so when the gate rejects the generated hook, body and CTA, all three used to fall back
    to it and the caption shipped as one sentence printed three times. Passing a shared set makes
    the fallback a resource that is consumed, not a default that is copied. Callers that are not
    filling a role (hashtags) pass nothing and are unaffected.
    """
    fallback = (_fv(profile, "tagline") or _fv(profile, "description")) if use_fallback else ""
    try:
        from grounding.ledger import EvidenceLedger
        led = EvidenceLedger.from_profile(profile)
        kept: list[str] = []
        for sentence in _SENTENCE_SPLIT_RE.split(str(text or "")):
            sentence = sentence.strip()
            if not sentence:
                continue
            # ClaimVerdict exposes the structured Claim as `.claim`; there is no `claim_text`.
            unsourced_claims = [verdict.claim for verdict in led.audit_text(sentence)
                                 if not verdict.sourced]
            if not unsourced_claims:
                kept.append(sentence)
        return _claim_role(" ".join(kept).strip() or fallback, taken)
    except Exception:  # noqa: BLE001 — the gate degrades exactly like the poster's
        # Never re-emit unverified model copy because the gate itself failed. Trusted profile copy
        # is the existing caption fallback; an absent fallback becomes honest omission.
        return _claim_role(fallback, taken)


def _gate_hashtag(profile: dict, tag: str) -> str:
    """Audit the words hidden by hashtag separators without changing an accepted tag."""
    audit_text = _CAMEL_CASE_BOUNDARY_RE.sub(" ", tag).replace("_", " ")
    return tag if _gate_text(profile, audit_text, use_fallback=False) else ""


def social_caption(profile: dict, *, language: str = "auto", trend_context: str = "",
                   caller: Any = None) -> dict:
    """One post-ready caption. Returns {caption, hook, body, cta, hashtags, language}.

    Never raises; on any failure returns a deterministic grounded fallback built from the
    tagline/description — honest, unexciting, and postable."""
    lang = language if language in ("ar", "en") else ""
    if not lang:
        from poster.locale import content_language_of
        lang = "ar" if content_language_of(profile) == "ar" else "en"

    if caller is None:
        try:
            from business_profile.llm import default_caller
            caller = default_caller(strong=False)
        except Exception:  # noqa: BLE001
            caller = None

    name = _fv(profile, "name")
    fallback_tag = "#" + name.replace(" ", "") if name else ""
    if caller is None:
        base = _fv(profile, "tagline") or _fv(profile, "description")
        return {"caption": base, "hook": "", "body": base, "cta": "",
                "hashtags": [t for t in [fallback_tag] if t], "language": lang}

    user = "BRAND EVIDENCE (use ONLY this):\n" + _evidence_lines(profile)
    if str(trend_context or "").strip():
        user += ("\n\nCURRENT MARKET SIGNAL (may shape the angle, never adds facts):\n"
                 + str(trend_context).strip())
    try:
        from poster.locale import arabic_dialect_for
        lang_name = arabic_dialect_for(profile) if lang == "ar" else "English"
        resp, _usage = caller(_SYSTEM.format(lang_name=lang_name),
                              user, _CaptionSchema.get(), group_name="social_caption")
        # One shared budget across the three roles, consumed in structural order. The tagline is a
        # single proposition; whichever role reaches it first is the one that gets it.
        taken: set[str] = set()
        hook = _gate_text(profile, str(getattr(resp, "hook", "") or "").strip(), taken=taken)
        body = _gate_text(profile, str(getattr(resp, "body", "") or "").strip(), taken=taken)
        cta = _gate_text(profile, str(getattr(resp, "cta", "") or "").strip(), taken=taken)
        tags = [_gate_hashtag(profile, str(t).strip())
                for t in (getattr(resp, "hashtags", None) or []) if str(t).strip()]
        tags = [t for t in tags if t]
        tags = [t if t.startswith("#") else "#" + t.replace(" ", "_") for t in tags][:6]
        if fallback_tag and fallback_tag not in tags:
            tags = [fallback_tag] + tags[:5]
        caption = "\n\n".join(p for p in (hook, body, cta, " ".join(tags)) if p)
        return {"caption": caption, "hook": hook, "body": body, "cta": cta,
                "hashtags": tags, "language": lang}
    except Exception:  # noqa: BLE001 — a caption failure must never break a render
        logger.exception("social caption generation failed; grounded fallback")
        base = _fv(profile, "tagline") or _fv(profile, "description")
        return {"caption": base, "hook": "", "body": base, "cta": "",
                "hashtags": [t for t in [fallback_tag] if t], "language": lang}


def content_only(profile: dict, *, language: str = "auto", trend_context: str = "",
                 caller: Any = None) -> dict:
    """«الكونتنت بس من غير ريل ولا صورة» — the caption alone.

    The same generator with no renderer anywhere in the call path: zero image spend by
    construction. The response shape deliberately carries no image_b64, so the frontend renders
    a text card instead of an empty frame."""
    out = social_caption(profile, language=language, trend_context=trend_context, caller=caller)
    out["content_only"] = True
    return out
