# -*- coding: utf-8 -*-
"""نضج البراند — the maturity signal that unlocks the FUNNEL plan (owner ruling 2026-07-20:
automatic-by-evidence). Deterministic CODE over evidence that already exists — social presence
from the scraped profile, engagement from the saved IG snapshot (the autopull's own artifact),
customer-voice counts, trust signals. NO new scraping, NO LLM.

The verdict is honest three-state: a NASCENT call needs ≥2 supporting signals AND no counter-
evidence; ESTABLISHED needs ≥2 of its own; anything thinner is UNKNOWN — and unknown keeps
today's single-objective behavior. Every verdict ships its readable signals ("أب القرار")."""
from __future__ import annotations

from typing import Any, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

# Declared thresholds (env-free on purpose — these are editorial judgments, reviewed here).
_LOW_ENGAGEMENT = 20       # avg (likes+comments)/post below this reads as a nascent audience
_HIGH_ENGAGEMENT = 100
_FEW_REVIEWS = 0
_MANY_REVIEWS = 10


class MaturityVerdict(BaseModel):
    model_config = ConfigDict(extra="forbid")
    level: Literal["nascent", "established", "unknown"] = "unknown"
    signals: List[str] = Field(default_factory=list)
    disclosure: str = ""


def _avg_engagement(ig_snapshot: Optional[dict]) -> Optional[float]:
    items = (ig_snapshot or {}).get("items") or []
    vals = []
    for it in items:
        if isinstance(it, dict) and (it.get("likesCount") is not None
                                     or it.get("commentsCount") is not None):
            vals.append(int(it.get("likesCount") or 0) + int(it.get("commentsCount") or 0))
    return (sum(vals) / len(vals)) if vals else None


def brand_maturity(profile: dict, *, ig_snapshot: Optional[dict] = None,
                   n_reviews: Optional[int] = None) -> MaturityVerdict:
    nascent: List[str] = []
    established: List[str] = []

    platforms = [s for s in (profile or {}).get("social_presence") or []
                 if isinstance(s, dict) and s.get("platform")]
    if len(platforms) == 0:
        nascent.append("حضور سوشيال: لا منصات مكتشفة على الموقع")
    elif len(platforms) >= 3:
        established.append(f"حضور سوشيال: {len(platforms)} منصات")

    avg = _avg_engagement(ig_snapshot)
    if avg is not None:
        if avg < _LOW_ENGAGEMENT:
            nascent.append(f"تفاعل إنستجرام هزيل: متوسط {avg:.0f} تفاعل/بوست")
        elif avg >= _HIGH_ENGAGEMENT:
            established.append(f"تفاعل إنستجرام قوي: متوسط {avg:.0f} تفاعل/بوست")

    if n_reviews is not None:
        if n_reviews <= _FEW_REVIEWS:
            nascent.append("لا مراجعات عملاء متاحة")
        elif n_reviews >= _MANY_REVIEWS:
            established.append(f"مراجعات عملاء: {n_reviews}")

    trust = [t for t in (profile or {}).get("trust_signals") or [] if t]
    if len(trust) >= 2:
        established.append(f"إشارات ثقة موثقة: {len(trust)}")

    if len(established) >= 2 and len(established) > len(nascent):
        level = "established"
        signals = established
        disclosure = "براند راسخ — " + " · ".join(established)
    elif len(nascent) >= 2 and not established:
        level = "nascent"
        signals = nascent
        disclosure = ("براند ناشئ (يحتاج بناء وعي قبل التحويل) — " + " · ".join(nascent))
    else:
        level = "unknown"
        signals = nascent + established
        disclosure = ("أدلة النضج غير كافية/متعارضة — خطة اليوم القياسية بهدف واحد"
                      + ((" — " + " · ".join(signals)) if signals else ""))
    return MaturityVerdict(level=level, signals=signals, disclosure=disclosure)
