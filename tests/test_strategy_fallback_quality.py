# -*- coding: utf-8 -*-
"""Strategy deterministic-fallback quality (2026-07-05 audit, strategy = the weakest subsystem).

Fixes measured on the corpus:
- items spread EVENLY over the whole window (was front-loaded: a 30-day/17-item plan filled
  only days 0-16, leaving the last ~43% empty),
- the LAST-RESORT filler is LANGUAGE-matched (an Arabic brand no longer gets hardcoded
  English topics that would render as English poster headlines),
- the real `tagline` is used as one more grounded topic.

Hermetic: deterministic path (caller=None), no network / no LLM.
"""
from strategy.builder import build_strategy, _fallback_plan, _profile_topics, _brand_is_arabic


def _offsets(profile, days, target):
    plan = _fallback_plan(profile, days=days, platforms=["instagram"], target=target)
    return [it.day_offset for it in plan]


def test_items_use_the_whole_window_not_just_the_front():
    offs = _offsets({"name": {"value": "X"}, "offerings": [{"name": "Coffee"}]}, days=30, target=17)
    assert min(offs) == 0
    assert max(offs) >= 27               # reaches the tail (was 16 before)
    # the second half of the window is populated, not empty
    assert any(o >= 15 for o in offs)


def test_single_item_is_valid():
    assert _offsets({"name": {"value": "X"}}, days=30, target=1) == [0]


def test_offsets_are_monotonic_and_in_range():
    offs = _offsets({"name": {"value": "X"}}, days=14, target=6)
    assert offs == sorted(offs)
    assert all(0 <= o <= 13 for o in offs)


def test_arabic_brand_gets_arabic_filler():
    ar = {"name": {"value": "متجر مصري"}, "description": {"value": "نبيع منتجات مصرية أصيلة"},
          "languages": ["ar"]}
    assert _brand_is_arabic(ar)
    topics = _profile_topics(ar)          # no offerings/value-props -> filler
    assert all(any("؀" <= c <= "ۿ" for c in t) for t in topics)


def test_english_brand_gets_english_filler():
    en = {"name": {"value": "Acme"}, "category": "retail"}
    assert not _brand_is_arabic(en)
    assert _profile_topics(en)[0] == "Brand highlight"


def test_tagline_is_used_as_a_grounded_topic():
    topics = _profile_topics({"name": {"value": "X"},
                              "tagline": {"value": "Freshness delivered daily"}})
    assert "Freshness delivered daily" in topics
    assert topics != ["Brand highlight", "Customer story", "Behind the scenes", "Offer spotlight"]


def test_arabic_fallback_calendar_has_no_english_filler_topics():
    ar = {"name": {"value": "مطعم القاهرة"}, "description": {"value": "أشهى الأكلات المصرية"},
          "languages": ["ar"]}
    cal = build_strategy(ar, caller=None, days=21)
    for it in cal.items:
        assert "Brand highlight" not in it.topic          # no English filler on an Arabic brand
