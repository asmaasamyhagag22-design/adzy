# -*- coding: utf-8 -*-
"""ش2 — deterministic competitor-ad FACTS (code, never AI): duration/Winner status, variation
collation + Fatigue-Iterating detection, and a deterministic CTA ladder. Pure functions; `today`
is injectable so everything is hermetically testable."""
from __future__ import annotations

import re
import unicodedata
from datetime import date
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict

from .config import COLLATION_PREFIX_CHARS, fatigue_min_variations, fatigue_window_days, winner_days


def normalize_ad_text(text: str) -> str:
    """NFKC + casefold + whitespace-collapse — the declared (not 'smart') normalization."""
    s = unicodedata.normalize("NFKC", str(text or ""))
    return re.sub(r"\s+", " ", s).strip().casefold()


def collation_key(ad) -> Optional[tuple]:
    """(page_name, first-120-normalized-chars) — the variation-collation key. None for an EMPTY
    normalized text: five blank-text ads must never collate into one giant fake fatigue group."""
    norm = normalize_ad_text(getattr(ad, "ad_text", ""))
    if not norm:
        return None
    return (str(getattr(ad, "page_name", "")).casefold(), norm[:COLLATION_PREFIX_CHARS])


def fatigue_groups(ads) -> dict:
    """Group ads by collation key. Ads with no key (empty text) are dropped, not grouped."""
    out: dict = {}
    for ad in (ads or []):
        key = collation_key(ad)
        if key is None:
            continue
        out.setdefault(key, []).append(ad)
    return out


def is_fatigue_group(group: list, *, window_days: Optional[int] = None,
                     min_variations: Optional[int] = None) -> bool:
    """Fatigue/Iterating = ≥N variations of the same collated ad whose start dates fall inside ONE
    sliding window (default 3-in-7d). Ads without started_at never count toward a window."""
    w = window_days if window_days is not None else fatigue_window_days()
    n = min_variations if min_variations is not None else fatigue_min_variations()
    starts = sorted(ad.started_at for ad in group if getattr(ad, "started_at", None))
    if len(starts) < n:
        return False
    for i in range(len(starts) - n + 1):
        if (starts[i + n - 1] - starts[i]).days <= w:
            return True
    return False


# -------------------------------------------------------------------------------------
# Deterministic CTA — a ladder: the LINK fields first (what the button actually opens),
# then Arabic/English regex over the body text. "" = honest unknown, never a guess.
# (The public ads_archive API exposes no cta_type; this is text analysis, declared as such.)
# -------------------------------------------------------------------------------------
_CTA_LINK = [
    ("whatsapp", re.compile(r"wa\.me|whatsapp|واتساب", re.I)),
    ("book", re.compile(r"\bbook|reserv|appointment|احجز|حجز|موعد", re.I)),
    ("call", re.compile(r"\bcall|tel:|اتصل|كلمنا|اتصال", re.I)),
    ("order", re.compile(r"\bshop|order|buy|cart|تسوق|اطلب|اشتري", re.I)),
    ("signup", re.compile(r"sign\s?up|register|subscribe|سجل|اشترك|قدم", re.I)),
]
_CTA_BODY = [
    ("whatsapp", re.compile(r"واتساب|whatsapp|wa\.me", re.I)),
    ("book", re.compile(r"احجز|حجز|book|موعد|appointment", re.I)),
    ("call", re.compile(r"اتصل|كلمنا|call us|اتصال", re.I)),
    ("order", re.compile(r"اطلب|تسوق|اشتري|shop|order|buy", re.I)),
    ("signup", re.compile(r"سجل|اشترك|قدم|sign\s?up|register|subscribe", re.I)),
]


def extract_cta(ad_text: str, link_captions: list, link_titles: list) -> str:
    """The ad's call-to-action, deterministically. Link fields outrank the body copy (the body may
    SAY 'call' while the button opens WhatsApp — the destination is the truth)."""
    link_blob = " ".join(str(x) for x in (list(link_captions or []) + list(link_titles or [])))
    if link_blob.strip():
        for name, rx in _CTA_LINK:
            if rx.search(link_blob):
                return name
    body = str(ad_text or "")
    for name, rx in _CTA_BODY:
        if rx.search(body):
            return name
    return ""


class AdFacts(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ad_id: str
    duration_days: int = 0
    status: Literal["winner", "fatigue_iterating", "normal"] = "normal"
    cta: str = ""
    format: str = ""


def ad_facts(ad, *, today: date, group_is_fatigue: bool = False) -> AdFacts:
    """One ad's deterministic facts. Winner requires ACTIVE + ≥winner_days; fatigue is decided at
    the GROUP level (the caller passes group_is_fatigue) and outranks normal but not winner."""
    days = 0
    if getattr(ad, "started_at", None):
        days = max(0, (today - ad.started_at).days)
    if getattr(ad, "is_active", False) and days >= winner_days():
        status = "winner"
    elif group_is_fatigue:
        status = "fatigue_iterating"
    else:
        status = "normal"
    return AdFacts(ad_id=str(getattr(ad, "ad_id", "")), duration_days=days, status=status,
                   cta=extract_cta(getattr(ad, "ad_text", ""),
                                   getattr(ad, "link_captions", []) or [],
                                   getattr(ad, "link_titles", []) or []),
                   format=str(getattr(ad, "creative_format", "") or ""))


def presence_facts(ads, *, today: date) -> dict:
    """The additive AdPresence facts over one competitor's ACTIVE ads: winner count (30d tier),
    fatigue-group count, and the dominant CTA/format ('' when nothing counted)."""
    active = [a for a in (ads or []) if getattr(a, "is_active", False)]
    groups = fatigue_groups(active)
    fatigued = {k for k, g in groups.items() if is_fatigue_group(g)}
    winners = 0
    cta_counts: dict = {}
    fmt_counts: dict = {}
    for ad in active:
        f = ad_facts(ad, today=today, group_is_fatigue=collation_key(ad) in fatigued)
        if f.status == "winner":
            winners += 1
        if f.cta:
            cta_counts[f.cta] = cta_counts.get(f.cta, 0) + 1
        if f.format:
            fmt_counts[f.format] = fmt_counts.get(f.format, 0) + 1
    top = lambda d: max(d, key=d.get) if d else ""  # noqa: E731
    return {"winners_30d": winners, "fatigue_groups": len(fatigued),
            "top_cta": top(cta_counts), "top_format": top(fmt_counts)}
