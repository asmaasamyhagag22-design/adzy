# -*- coding: utf-8 -*-
"""نضج البراند (owner ruling 2026-07-20 "نعالجها/أوتوماتيك بالدليل"): deterministic verdict from
EXISTING evidence only (social presence, saved IG engagement, reviews, trust signals) — every
verdict carries readable signals ("أب القرار"), and thin evidence yields UNKNOWN (today's
behavior), never a guessed maturity. Hermetic."""
from __future__ import annotations

from media_plan.maturity import brand_maturity


def _ig(posts):
    return {"items": [{"likesCount": l, "commentsCount": c} for l, c in posts]}


def test_bare_brand_is_nascent_with_readable_signals():
    v = brand_maturity({"social_presence": []},
                       ig_snapshot=_ig([(2, 0), (1, 0), (3, 1)]), n_reviews=0)
    assert v.level == "nascent"
    assert len(v.signals) >= 2                              # the ruling: ≥2 signals, disclosed
    assert any("سوشيال" in s for s in v.signals)
    assert any("تفاعل" in s for s in v.signals)
    assert v.disclosure                                     # human-readable "أب القرار"


def test_rich_brand_is_established():
    profile = {"social_presence": [{"platform": p} for p in ("facebook", "instagram", "tiktok")],
               "trust_signals": [{"value": "ISO"}, {"value": "award"}]}
    v = brand_maturity(profile, ig_snapshot=_ig([(500, 40)] * 12), n_reviews=25)
    assert v.level == "established"


def test_thin_evidence_is_unknown_never_guessed():
    # one platform, no IG snapshot, no reviews info — not enough to CALL it either way
    v = brand_maturity({"social_presence": [{"platform": "facebook"}]})
    assert v.level == "unknown"
    assert "غير كافية" in v.disclosure or "unknown" in v.disclosure.lower()


def test_conflicting_evidence_is_unknown():
    # weak IG engagement BUT strong platforms + reviews -> conflicting, stay honest
    profile = {"social_presence": [{"platform": p} for p in ("facebook", "instagram", "tiktok")]}
    v = brand_maturity(profile, ig_snapshot=_ig([(1, 0)] * 10), n_reviews=30)
    assert v.level in ("unknown", "established")            # NEVER nascent against 2 counters
