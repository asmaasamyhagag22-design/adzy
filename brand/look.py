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
             "trust", "professional")
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
        t = (profile or {}).get("tone_of_voice")
        if isinstance(t, dict):
            t = t.get("value")
        if t:
            parts.append(str(t))
    return " ".join(parts).lower()


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
    if any(w in blob for w in _CLINICAL):
        return _LOOKS["clinical"]
    if any(w in blob for w in _LUXURY):
        return _LOOKS["luxury"]
    if any(w in blob for w in _VIBRANT):
        return _LOOKS["vibrant"]
    if any(w in blob for w in _WARM):
        return _LOOKS["warm"]
    return DEFAULT_LOOK
