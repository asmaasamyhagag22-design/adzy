# -*- coding: utf-8 -*-
"""Which voice reads this brand's ad — chosen from the real library, by register.

MEASURED LIVE the moment the owner funded the account (2026-07-26): the same key had returned
402 `paid_plan_required` all day, and the moment it cleared the library showed FOUR professional
Egyptian-Arabic voices. Before this the reel's Arabic fell back to the free `edge` voice, which
the owner called «بشع… برود… مفيهوش أي حماس».

The catalogue below is what her account actually holds — voice ids verified against
`GET /v1/voices`, not guessed. Selection follows the brand's own measured register (D-8: the
dialect comes from `poster.locale`, never from a market assumption), and `REEL_TTS_VOICE`
overrides everything for a one-off.
"""
from __future__ import annotations

import os

# Verified against the owner's account, 2026-07-26. All four are `professional` tier and are
# Egyptian-Arabic; every one is male — a female Egyptian voice would have to be added to the
# library first (3 custom slots, 0 used), and until then this module must not pretend otherwise.
EGYPTIAN_VOICES = [
    {"voice_id": "QvNF0qyyt1Tuy1YAmnzH", "name": "Mostafa", "accent": "egyptian",
     "gender": "male", "register": "bold", "note": "Bold and Spirited"},
    {"voice_id": "5REPlS2Ja1VZ7zNA0ykn", "name": "Ziad", "accent": "egyptian",
     "gender": "male", "register": "energetic", "note": "Dynamic and Vibrant"},
    {"voice_id": "Ojb0nFbyzZn95u0i5a5p", "name": "Marco Nady", "accent": "egyptian",
     "gender": "male", "register": "trust", "note": "naturally earns your trust"},
    {"voice_id": "VqHyN6PYNu3uNKGdbxKs", "name": "ELareef", "accent": "egyptian",
     "gender": "male", "register": "neutral", "note": ""},
]

# The default multilingual read for everything else. NOT an Egyptian voice — a brand that does
# not speak Egyptian Arabic must never be given an Egyptian accent (the D-8 country axis).
NEUTRAL_VOICE = {"voice_id": "cjVigY5qzO86Huf0OWal", "name": "Eric", "accent": "american",
                 "gender": "male", "register": "trust", "note": "Smooth, Trustworthy"}

_TRUST_WORDS = ("trust", "professional", "clinic", "pharmac", "medical", "reliable", "expert",
                "ثقة", "طبي", "صيدل", "عيادة")
_BOLD_WORDS = ("bold", "energetic", "youth", "vibrant", "playful", "fun", "sport",
               "حماس", "طاقة", "شباب", "قوي")


def pick_voice(*, dialect: str = "", tone: str = "") -> dict:
    """The voice for this brand. `dialect` is the MEASURED register (poster.locale), `tone` the
    profile's own tone-of-voice. Returns a catalogue entry; never raises."""
    override = (os.environ.get("REEL_TTS_VOICE") or "").strip()
    if override:
        return {"voice_id": override, "name": "override", "accent": "", "gender": "",
                "register": "", "note": "REEL_TTS_VOICE"}

    blob = f"{dialect} {tone}".lower()
    egyptian = "egypt" in blob or "مصر" in blob or "masri" in blob
    if not egyptian:
        return dict(NEUTRAL_VOICE)

    if any(w in blob for w in _TRUST_WORDS):
        want = "trust"
    elif any(w in blob for w in _BOLD_WORDS):
        want = "energetic"
    else:
        want = "bold"
    for v in EGYPTIAN_VOICES:
        if v["register"] == want:
            return dict(v)
    return dict(EGYPTIAN_VOICES[0])
