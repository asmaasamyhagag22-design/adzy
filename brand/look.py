# -*- coding: utf-8 -*-
"""ONE camera and ONE light for every shot of one brand — pinned before generation.

OWNER (2026-07-26): «لازم تطلبي من الأيجنت يثبت كلمات وصف الإضاءة والألوان في كل البرومبتات اللي
بيبعتها لموديل التوليد… تثبيت العبارة دي بيجبر الـAI إنه يولد المشاهد كلها بنفس عدسة الكاميرا
ونفس جو الإضاءة تقريباً».

MEASURED on abuauf_agency_v2: a warm sun-lit kitchen, a cold blue gym and a flat studio in one
17-second film. Eight competent frames read as eight unrelated clips, and no colour pass after
the fact repairs a scene that was LIT differently — the shadows are already in the pixels.

So the fix belongs before generation: one LOOK string, resolved ONCE per brand, appended
verbatim to every prompt — the still seeds, the motion prompts, the poster background. Identical
text is the whole point; two shots that differ by a word differ by a grade.

D-8 ON THE AESTHETIC AXIS. The look is DERIVED from the brand's own learned DNA — a pharmacy is
cool and clinical, a snack brand is warm and golden — because stamping one house style on every
client is the same class of error as stamping one country on every market. The owner's example
string is the DEFAULT for a brand whose DNA has not been built yet, not a universal rule.
"""
from __future__ import annotations

from typing import Any, Optional

# The owner's own example, kept verbatim as the no-DNA default.
DEFAULT_LOOK = ("cinematic lighting, warm colour palette, golden hour glow, richly saturated, "
                "consistent studio commercial look, 35mm lens, Kodak film grade")

# Each family is a COMPLETE look: light + palette + lens + grade. They are written out in full
# rather than assembled from parts so the string is stable and readable in a log.
_LOOKS = {
    "clinical": ("cinematic lighting, cool clean colour palette, bright soft daylight, "
                 "crisp and airy, consistent studio commercial look, 50mm lens, "
                 "clean digital grade"),
    "warm": DEFAULT_LOOK,
    "luxury": ("cinematic lighting, deep restrained colour palette, low-key directional light, "
               "rich shadows, consistent studio commercial look, 85mm lens, "
               "fine film grain grade"),
    "vibrant": ("cinematic lighting, punchy saturated colour palette, crisp high-key light, "
                "high contrast, consistent studio commercial look, 35mm lens, "
                "modern digital grade"),
}

_CLINICAL = ("clinical", "clean", "sterile", "pharmac", "medical", "hygien", "lab", "white",
             "trust", "professional",
             # A supplement/vitamin brand IS the cool case — the brief names Nano Care as the
             # example, and none of the words above appear anywhere in its profile.
             "supplement", "vitamin", "nutraceutical", "dermatolog", "derma", "capsule",
             "liposom", "dose", "dosage", "مكمل", "فيتامين", "صيدل", "طبي", "عيادة")
_LUXURY = ("luxury", "premium", "elegant", "refined", "sophisticat", "opulent", "heritage")
_VIBRANT = ("vibrant", "playful", "bold", "energetic", "youth", "punchy", "sport")
_WARM = ("warm", "golden", "cosy", "cozy", "appetis", "appetiz", "sunlit", "homely", "natural")


def _dna_blob(brand_dna: Any, profile: Optional[dict]) -> str:
    parts = []
    for attr in ("mood", "imagery_style", "composition_patterns", "layout_philosophy"):
        v = getattr(brand_dna, attr, "") if brand_dna is not None else ""
        if v:
            parts.append(v if isinstance(v, str) else " ".join(map(str, v)))
    if profile:
        # WHAT THE BRAND IS, not only how it talks. MEASURED 2026-07-27: reading `tone_of_voice`
        # alone made this resolver INERT on every real profile — Nano Care ("inspirational"),
        # Abu Auf ("friendly") and Al Fouad (None) all fell through to the default, so a deep-blue
        # pharmaceutical brand and a nut roaster got the identical golden look. A voice descriptor
        # is not a visual one; the family keywords ("clinical", "pharmac", "lab", "luxury") describe
        # the brand's WORLD, and the fields that carry that were never read.
        for key in ("tone_of_voice", "website_type", "market_category", "description",
                    "industry"):
            v = (profile or {}).get(key)
            if isinstance(v, dict):
                v = v.get("value")
            if v:
                parts.append(str(v))
    return " ".join(parts).lower()


def _hits(blob: str, keywords) -> bool:
    """Does any keyword start a WORD in the blob?

    MEASURED 2026-07-27: bare `in` made "lab" match inside "available", so Abu Auf — nuts, dried
    fruit and coffee — resolved CLINICAL. Third time this class has bitten in one session («بشرة»
    inside «بالبشرة», "men" inside "women"). The keywords are deliberate PREFIXES ("pharmac" must
    still reach "pharmaceutical"), so the rule is word-START, not whole-word.
    """
    import re as _re
    words = _re.findall(r"[^\W\d_]+", blob or "", _re.UNICODE)
    return any(w.startswith(k) for w in words for k in keywords)


def look_lock(*, brand_dna: Any = None, profile: Optional[dict] = None) -> str:
    """The LOOK for this brand — one string, identical on every call for the same inputs.

    Resolve it ONCE per run and pass it down; recomputing per scene invites a changed field to
    split one film into two grades."""
    import os
    override = (os.environ.get("BRAND_LOOK") or "").strip()
    if override:
        return override

    blob = _dna_blob(brand_dna, profile)
    if not blob:
        return DEFAULT_LOOK
    # Order matters: a brand described as "clean, premium clinic" is clinical first — the light
    # is what the camera obeys, and clinical light is the stronger constraint.
    if _hits(blob, _CLINICAL):
        return _LOOKS["clinical"]
    if _hits(blob, _LUXURY):
        return _LOOKS["luxury"]
    if _hits(blob, _VIBRANT):
        return _LOOKS["vibrant"]
    if _hits(blob, _WARM):
        return _LOOKS["warm"]
    return DEFAULT_LOOK
