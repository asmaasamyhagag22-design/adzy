# -*- coding: utf-8 -*-
"""ش2 — الحقائق الحتمية (2026-07-19): deterministic competitor-ad facts, CODE not AI.
Winner = active ≥30d (config, distinct from PROVEN 60d) · Fatigue = ≥3 variations of the SAME
collated ad starting inside one sliding 7-day window · CTA = a deterministic ladder over the
link fields then Arabic/English regex ("" = honest unknown) · additive AdPresence fields.
Hermetic — pure functions + fixtures, no network."""
from __future__ import annotations

from datetime import date, datetime, timezone

from ads_intel.schemas import AdPresence, CompetitorAd, ad_presence

_NOW = datetime(2026, 7, 19, tzinfo=timezone.utc)
_TODAY = date(2026, 7, 19)


def _ad(ad_id="1", text="اشتري دلوقتي", start=None, active=True, fmt="image",
        page="Rival A", captions=None, titles=None):
    return CompetitorAd(source="fixture", ad_id=ad_id, page_name=page, brand_ref="rival_a",
                       platforms=["facebook"], started_at=start, is_active=active,
                       creative_format=fmt, ad_text=text, snapshot_url=f"https://fb/{ad_id}",
                       scraped_at=_NOW,
                       link_captions=captions or [], link_titles=titles or [])


# ---- winner (30d) vs proven (60d) ---------------------------------------------------

def test_winner_at_30_days_not_29():
    from ads_intel.facts import ad_facts
    w = ad_facts(_ad(start=date(2026, 6, 19)), today=_TODAY, group_is_fatigue=False)
    assert w.duration_days == 30 and w.status == "winner"
    n = ad_facts(_ad(start=date(2026, 6, 20)), today=_TODAY, group_is_fatigue=False)
    assert n.duration_days == 29 and n.status == "normal"
    # an INACTIVE 100-day ad is history, not a winner
    dead = ad_facts(_ad(start=date(2026, 4, 1), active=False), today=_TODAY, group_is_fatigue=False)
    assert dead.status == "normal"


def test_proven_stays_60_and_distinct_from_winner():
    from ads_intel.config import PROVEN_DAYS, winner_days
    assert PROVEN_DAYS == 60 and winner_days() == 30
    ads = [_ad(ad_id="1", start=date(2026, 6, 10))]          # 39d: winner but NOT proven
    ap = ad_presence(ads, today=_TODAY)
    assert ap.winners_30d == 1 and ap.proven_winners == 0


# ---- collation + fatigue ------------------------------------------------------------

def test_collation_folds_case_whitespace_and_long_suffix():
    from ads_intel.facts import collation_key
    a = _ad(text="خصم  50%   على كل الجلسات! " + "تفاصيل " * 40)
    b = _ad(text="خصم 50% على كل الجلسات!  " + "تفاصيل " * 40 + "نسخة تانية مختلفة الذيل")
    assert collation_key(a) == collation_key(b)              # same 120-char prefix collates
    c = _ad(text="عرض مختلف تماماً")
    assert collation_key(a) != collation_key(c)


def test_empty_ad_text_never_forms_a_group():
    from ads_intel.facts import collation_key, fatigue_groups
    assert collation_key(_ad(text="")) is None
    groups = fatigue_groups([_ad(ad_id=str(i), text="", start=date(2026, 7, 15)) for i in range(5)])
    assert groups == {}                                       # 5 empty texts != one fatigue group


def test_fatigue_three_starts_in_seven_days():
    from ads_intel.facts import fatigue_groups, is_fatigue_group
    mk = lambda i, d: _ad(ad_id=str(i), text="نفس الإعلان بالظبط", start=d)
    tight = [mk(1, date(2026, 7, 10)), mk(2, date(2026, 7, 13)), mk(3, date(2026, 7, 16))]
    g = fatigue_groups(tight)
    assert len(g) == 1 and is_fatigue_group(list(g.values())[0])          # 3 in 7d -> fatigue
    spread = [mk(1, date(2026, 6, 1)), mk(2, date(2026, 6, 20)), mk(3, date(2026, 7, 10))]
    assert not is_fatigue_group(list(fatigue_groups(spread).values())[0])  # 3 in 40d -> not
    two = [mk(1, date(2026, 7, 10)), mk(2, date(2026, 7, 12))]
    assert not is_fatigue_group(list(fatigue_groups(two).values())[0])     # 2 in 7d -> not
    # None starts never count toward a window
    nones = [mk(1, None), mk(2, None), mk(3, None)]
    assert not is_fatigue_group(list(fatigue_groups(nones).values())[0])


# ---- deterministic CTA --------------------------------------------------------------

def test_cta_arabic_and_english_patterns():
    from ads_intel.facts import extract_cta
    assert extract_cta("كلمنا على واتساب دلوقتي", [], []) == "whatsapp"
    assert extract_cta("احجزي جلستك النهارده", [], []) == "book"
    assert extract_cta("اتصل بنا الآن", [], []) == "call"
    assert extract_cta("اطلب أونلاين ويوصلك", [], []) == "order"
    assert extract_cta("سجل معانا في البرنامج", [], []) == "signup"
    assert extract_cta("Shop the new collection", [], []) == "order"
    assert extract_cta("Book your consultation", [], []) == "book"


def test_cta_prefers_link_fields_over_body_regex():
    from ads_intel.facts import extract_cta
    # the body says "اتصل" but the actual link goes to WhatsApp — the link wins
    assert extract_cta("اتصل بنا", ["wa.me/2010000"], []) == "whatsapp"
    assert extract_cta("نص عادي", [], ["Book Now"]) == "book"


def test_cta_unknown_is_empty_string():
    from ads_intel.facts import extract_cta
    assert extract_cta("إعلان جميل عن البراند", [], []) == ""


# ---- presence facts (additive) ------------------------------------------------------

def test_presence_new_fields_from_ads():
    ads = [
        _ad(ad_id="1", text="خصم كبير على الليزر", start=date(2026, 6, 1), fmt="video",
            captions=["wa.me/x"]),                                         # 48d winner, whatsapp
        _ad(ad_id="2", text="نفس الإعلان المكرر", start=date(2026, 7, 14), fmt="image"),
        _ad(ad_id="3", text="نفس الإعلان المكرر", start=date(2026, 7, 16), fmt="image"),
        _ad(ad_id="4", text="نفس الإعلان المكرر", start=date(2026, 7, 18), fmt="image"),
    ]
    ap = ad_presence(ads, today=_TODAY)
    assert ap.winners_30d == 1
    assert ap.fatigue_groups == 1                             # the 3-in-7d repeated ad
    assert ap.top_format == "image"                           # 3 image vs 1 video
    assert ap.top_cta == "whatsapp"
    assert ap.active_count == 4                               # legacy fields untouched


def test_old_snapshot_without_link_fields_still_parses():
    from ads_intel.providers.meta_ad_library import ads_from_snapshot
    snap = {"brand_ref": "r", "pulled_at": "2026-07-18T00:00:00Z", "ads": [
        {"id": "9", "page_name": "P", "publisher_platforms": ["facebook"],
         "ad_delivery_start_time": "2026-06-01", "media_type": "image",
         "ad_creative_bodies": ["نص قديم"]}]}                  # no link fields (old pull shape)
    ads = ads_from_snapshot(snap)
    assert len(ads) == 1 and ads[0].link_captions == [] and ads[0].link_titles == []
    # and a NEW-shape row carries them through
    snap["ads"][0]["ad_creative_link_captions"] = ["wa.me/x"]
    snap["ads"][0]["ad_creative_link_titles"] = ["احجز الآن"]
    ads2 = ads_from_snapshot(snap)
    assert ads2[0].link_captions == ["wa.me/x"] and ads2[0].link_titles == ["احجز الآن"]


def test_old_presence_dumps_still_validate():
    # additive fields must not break snapshots serialized before ش2
    AdPresence(brand_ref="r", page_name="P", active_count=2, longest_days=10,
               proven_winners=0, formats={}, platforms={})
