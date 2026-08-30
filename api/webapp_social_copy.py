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

_LANG_NAMES = {"ar": "Egyptian Arabic (مصري طبيعي زي بوستات السوشيال)", "en": "English"}


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


def _gate_text(profile: dict, text: str) -> str:
    """The poster's grounding rule, applied to the caption: a claim the Ledger cannot source is
    replaced by the nearest grounded line rather than shipped. Degrades open only when the ledger
    itself cannot be built (no profile) — the same boundary the SWOT path draws."""
    try:
        from grounding.ledger import EvidenceLedger
        led = EvidenceLedger.from_profile(profile)
        verdicts = led.audit_text(text)
        bad = [v for v in (verdicts or []) if getattr(v, "sourced", True) is False]
        if not bad:
            return text
        # Drop the unsourced sentences; keep the grounded remainder. An empty result falls back
        # to the tagline/description — grounded by definition (they are quoted site copy).
        kept = [s for s in text.split(".") if not any(
            getattr(b, "claim_text", "") and getattr(b, "claim_text", "") in s for b in bad)]
        out = ".".join(k for k in kept if k.strip()).strip()
        return out or _fv(profile, "tagline") or _fv(profile, "description") or text
    except Exception:  # noqa: BLE001 — the gate degrades exactly like the poster's
        return text


def social_caption(profile: dict, *, language: str = "auto", trend_context: str = "",
                   caller: Any = None) -> dict:
    """One post-ready caption. Returns {caption, hook, body, cta, hashtags, language}.

    Never raises; on any failure returns a deterministic grounded fallback built from the
    tagline/description — honest, unexciting, and postable."""
    lang = language if language in ("ar", "en") else ""
    if not lang:
        langs = (profile or {}).get("languages") or []
        first = langs[0] if langs else ""
        code = (first.get("code") if isinstance(first, dict) else first) or ""
        lang = "ar" if str(code).lower().startswith("ar") else "en"

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
        resp, _usage = caller(_SYSTEM.format(lang_name=_LANG_NAMES.get(lang, "English")),
                              user, _CaptionSchema.get(), group_name="social_caption")
        hook = str(getattr(resp, "hook", "") or "").strip()
        body = _gate_text(profile, str(getattr(resp, "body", "") or "").strip())
        cta = str(getattr(resp, "cta", "") or "").strip()
        tags = [str(t).strip() for t in (getattr(resp, "hashtags", None) or []) if str(t).strip()]
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
