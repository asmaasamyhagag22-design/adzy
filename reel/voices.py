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

# FEMALE Egyptian voices. The account's own library holds none — all four professional Egyptian
# voices in it are male — so these come from the shared library, where a voice id works for
# generation without being added to the account (verified live: all three rendered the script).
# Usage counts are the library's own popularity figures, recorded because they are the only
# quality signal available without listening to every one.
FEMALE_EGYPTIAN_VOICES = [
    # OWNER'S PICK (2026-07-26), chosen from a live three-way listen on the real script and
    # confirmed by her as a FEMALE voice — the library's own metadata query was unreliable for
    # this id, her ear was not. It leads the list, so it is the default female read.
    {"voice_id": "6aXW46RTUz6Y2lkBGQ1a", "name": "owner's pick", "accent": "egyptian",
     "gender": "female", "register": "bold", "note": "chosen by the owner from a live A/B/C"},
    {"voice_id": "I3u6waC588j43py1kDN2", "name": "Fatima", "accent": "egyptian",
     "gender": "female", "register": "energetic", "note": "Expressive Egyptian (3,040 uses)"},
    {"voice_id": "vWDp3PLsTWjIhBxxUKh9", "name": "Fatima", "accent": "egyptian",
     "gender": "female", "register": "trust", "note": "Smooth narrator (6,436 uses)"},
    {"voice_id": "xPcC3nehhziQaOrIeAwv", "name": "Ghozlan", "accent": "egyptian",
     "gender": "female", "register": "bold", "note": "Soft, clear, conversational (3,201 uses)"},
]

# The default multilingual read for everything else. NOT an Egyptian voice — a brand that does
# not speak Egyptian Arabic must never be given an Egyptian accent (the D-8 country axis).
NEUTRAL_VOICE = {"voice_id": "cjVigY5qzO86Huf0OWal", "name": "Eric", "accent": "american",
                 "gender": "male", "register": "trust", "note": "Smooth, Trustworthy"}

# WHO IS BEING SPOKEN TO (owner, 2026-07-26): «على حسب الكاتيجوري اختار الصوت — سكين كير وملابس
# وبرفان حاجة نسائي نشغّل النسائي، رجالي يختار رجالي، غير كده هو يختار». A category that is not
# gendered (a pharmacy, a snack, a telecom) leaves the choice to the brand's register.
_FEMALE_CATEGORIES = (
    "skincare", "skin care", "cosmetic", "makeup", "make-up", "beauty", "perfume", "fragrance",
    "haircare", "hair care", "women", "womens", "ladies", "lingerie", "jewel", "abaya", "dress",
    "بشرة", "مكياج", "تجميل", "عطور", "عطر", "حريمي", "نسائي", "سيدات",
    "مجوهرات", "فساتين", "شعر", "سكين",          # «سكين كير» — how it is actually written here
)
_MALE_CATEGORIES = (
    "men", "mens", "male grooming", "beard", "shaving", "barber", "menswear",
    "رجالي", "رجال", "دقن", "حلاقة", "لحية",
)

_TRUST_WORDS = ("trust", "professional", "clinic", "pharmac", "medical", "reliable", "expert",
                "ثقة", "طبي", "صيدل", "عيادة")
_BOLD_WORDS = ("bold", "energetic", "youth", "vibrant", "playful", "fun", "sport",
               "حماس", "طاقة", "شباب", "قوي")


def voice_gender_for(category: str) -> str:
    """"female" | "male" | "" — who this category is spoken to.

    A skincare, perfume or womenswear ad is addressed to women and reads in a woman's voice; a
    grooming or menswear ad reads in a man's. Most categories (a pharmacy, a snack, a telecom)
    are not gendered at all and return "" so the brand's REGISTER decides instead — the owner's
    own rule: «غير كده هو يختار»."""
    import re
    blob = str(category or "").lower().strip()
    if not blob:
        return ""

    # Arabic glues its articles and prepositions onto the noun: «العناية بالبشرة» must match
    # «بشرة». The same morphology lesson the claim linter learned an hour earlier.
    _ar_prefix = r"(?:و|ف|ب|ك|ل|ال|بال|لل|كال|وال)?"

    def _has(words) -> bool:
        # WORD BOUNDARIES, not substrings: "women" CONTAINS "men", so a plain `in` test read
        # "women fashion" as a men's category. Measured the moment the rule was first run.
        for w in words:
            # A trailing English plural is the same category ("cosmetic" / "cosmetics"), so the
            # boundary allows it — otherwise the word-boundary fix trades one miss for another.
            pat = rf"(?<![\w؀-ۿ]){_ar_prefix}{re.escape(w)}s?(?![\w؀-ۿ])"
            if re.search(pat, blob):
                return True
        return False

    # FEMALE FIRST for the same reason: the more specific term must win a tie.
    if _has(_FEMALE_CATEGORIES):
        return "female"
    if _has(_MALE_CATEGORIES):
        return "male"
    return ""


def pick_voice(*, dialect: str = "", tone: str = "", category: str = "",
               gender: str = "") -> dict:
    """The voice for this brand. `dialect` is the MEASURED register (poster.locale), `tone` the
    profile's own tone-of-voice, `category` the market it sells into, `gender` an explicit
    override. Returns a catalogue entry; never raises."""
    override = (os.environ.get("REEL_TTS_VOICE") or "").strip()
    if override:
        return {"voice_id": override, "name": "override", "accent": "", "gender": "",
                "register": "", "note": "REEL_TTS_VOICE"}

    blob = f"{dialect} {tone}".lower()
    egyptian = "egypt" in blob or "مصر" in blob or "masri" in blob
    if not egyptian:
        return dict(NEUTRAL_VOICE)

    want_gender = (gender or "").strip().lower() or voice_gender_for(category)

    if any(w in blob for w in _TRUST_WORDS):
        want = "trust"
    elif any(w in blob for w in _BOLD_WORDS):
        want = "energetic"
    else:
        want = "bold"

    pool = FEMALE_EGYPTIAN_VOICES if want_gender == "female" else EGYPTIAN_VOICES
    for v in pool:
        if v["register"] == want:
            return dict(v)
    return dict(pool[0])
