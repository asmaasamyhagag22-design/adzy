# -*- coding: utf-8 -*-
"""Media Plan v2 · step 1 (2026-07-18): the evidence-BOUND marketing-intel extractor.
Offers/USPs/pain-points are pulled from a social SNAPSHOT, but every item must carry a VERBATIM
quote that the CODE re-verifies against the snapshot (the moat, PD-1). A model that invents an
offer without a real quote is REJECTED — zero hallucination by construction. Hermetic: MockCaller,
no network, no live IG scrape (PD-10 — the snapshot is owner-provided)."""
from __future__ import annotations

from datetime import datetime, timezone

from business_profile.llm.caller import MockCaller
from marketing_intel import extract_marketing_intel
from marketing_intel.extractor import _RawItem, _RawResponse
from social_intel.schemas import SocialSignal

_NOW = datetime(2026, 7, 18, tzinfo=timezone.utc)


def _sig(kind, url, text, own=True):
    return SocialSignal(source="snapshot", source_url=url, signal_kind=kind,
                        author_hash="hash1234abcd", text=text, is_own_brand=own, scraped_at=_NOW)


_SNAPSHOT = [
    _sig("caption", "https://instagram.com/p/AAA",
         "خصم 20% على كل الجلسات لهذا الأسبوع فقط! احجزي الآن."),
    _sig("caption", "https://instagram.com/p/BBB",
         "We are the only clinic with board-certified dermatologists in Riyadh."),
    _sig("comment", "https://instagram.com/p/CCC",
         "المشكلة إن مواعيد الحجز دايمًا متأخرة وبستنى كتير", own=False),
]


def _staged(items):
    return MockCaller({"ig_marketing_intel": _RawResponse(items=items)})


def test_verbatim_items_survive_and_carry_the_right_permalink():
    caller = _staged([
        _RawItem(kind="offer", text="20% off all sessions this week",
                 quote="خصم 20% على كل الجلسات لهذا الأسبوع", permalink="https://instagram.com/p/AAA"),
        _RawItem(kind="usp", text="only clinic with board-certified dermatologists",
                 quote="the only clinic with board-certified dermatologists",
                 permalink="https://wrong.example/"),   # wrong permalink — code re-derives the real one
        _RawItem(kind="pain_point", text="booking appointments are always late",
                 quote="مواعيد الحجز دايمًا متأخرة", permalink="https://instagram.com/p/CCC"),
    ])
    mi = extract_marketing_intel(_SNAPSHOT, caller, brand_ref="clinic_x")
    assert len(mi.offers) == 1 and len(mi.usps) == 1 and len(mi.pain_points) == 1
    assert mi.rejected == 0
    # PD-1: evidence permalink is the signal the CODE matched, not the model's (wrong) claim
    assert mi.usps[0].evidence[0].page_url == "https://instagram.com/p/BBB"
    assert mi.offers[0].evidence[0].page_url == "https://instagram.com/p/AAA"
    assert mi.offers[0].source_type.value == "extracted"


def test_a_fabricated_quote_is_rejected_by_the_moat():
    caller = _staged([
        _RawItem(kind="offer", text="buy one get one free",
                 quote="اشتري واحدة واحصلي على الثانية مجانًا",   # NOT in the snapshot -> fabricated
                 permalink="https://instagram.com/p/AAA"),
        _RawItem(kind="usp", text="cheap", quote="best", permalink="https://instagram.com/p/BBB"),  # too short
    ])
    mi = extract_marketing_intel(_SNAPSHOT, caller)
    assert mi.is_empty()                                   # nothing grounded survived
    assert mi.rejected == 2                                # both dropped by the code-side verbatim check


def test_no_caller_or_empty_snapshot_degrades_to_empty():
    assert extract_marketing_intel(_SNAPSHOT, None).is_empty()
    assert extract_marketing_intel([], _staged([])).is_empty()


def test_a_caller_failure_never_fabricates():
    boom = MockCaller({}, raise_on_group={"ig_marketing_intel"})
    mi = extract_marketing_intel(_SNAPSHOT, boom)
    assert mi.is_empty() and mi.rejected == 0
