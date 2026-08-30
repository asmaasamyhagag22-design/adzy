# -*- coding: utf-8 -*-
"""The same fact must not be listed twice, and an absent source must say it is absent.

TWO DEFECTS THE OWNER SAW ON HER OWN CLIENT'S SWOT (alfouadpharmacies.com, 2026-07-27).

1. THE SAME FACT, TWICE. Its Strengths quadrant shipped both
       "7,000+ Customers Daily"          (from trust_signals)
       "Serves over 7,000 customers daily"  (from other_unique_insights)
   `synthesize_swot` de-duplicates on `s.text.strip().lower()` — EXACT match — so a restatement of
   the same fact from a second source is invisible to it. Ten "strengths" of which two are one
   strength makes the whole count untrustworthy.

2. NO CUSTOMER VOICE, AND NO EXPLANATION. «فين الريفيو سواء في الموقع او السوشيال ميديا»
   MEASURED, and the answer is NOT a broken pipeline — the collector runs correctly and there is
   nothing for it to collect:
       website testimonials                        0
       Instagram snapshot on disk                  15 posts
       comments across ALL of those posts          4
       Instagram's own commentsCount per post      0 on most of them
       of those 4, brand replies ("فروعنا في القاهرة والجيزة")   2
       remaining customer comments with a polarity 0  (a joke and a question)
   So the SWOT carries zero review-derived items because the brand's accounts carry almost no
   customer text. The defect is that this is indistinguishable, on screen, from "we never looked".
"""
from __future__ import annotations


# ---------------------------------------------------------------------
# 1 — one fact, one line
# ---------------------------------------------------------------------

def test_a_restatement_of_the_same_fact_is_not_a_second_strength():
    from competitor.swot import _same_claim
    assert _same_claim("7,000+ Customers Daily", "Serves over 7,000 customers daily") is True


def test_distinct_claims_that_share_a_word_stay_distinct():
    """The dedup must not merge two real strengths just because both say 'delivery'."""
    from competitor.swot import _same_claim
    assert _same_claim("Fast Delivery", "Broad Delivery Coverage") is False
    assert _same_claim("100% Original Products", "Secure and Multiple Payment Methods") is False


def test_it_holds_in_arabic_too():
    """D-8: the rule is token containment, not an English word list."""
    from competitor.swot import _same_claim
    assert _same_claim("توصيل سريع", "توصيل سريع لكل المحافظات") is True
    assert _same_claim("توصيل سريع", "منتجات أصلية 100%") is False


def test_a_single_shared_token_is_never_enough():
    from competitor.swot import _same_claim
    assert _same_claim("Delivery", "Delivery coverage across Egypt") is False, (
        "one content word in common must not collapse two claims")


def test_the_synthesis_drops_the_duplicate():
    from competitor.swot import SWOT, SWOTItem, dedupe_claims
    items = [SWOTItem(text="7,000+ Customers Daily", citation=["your profile"], evidence=""),
             SWOTItem(text="Serves over 7,000 customers daily", citation=["your profile"],
                      evidence=""),
             SWOTItem(text="Fast Delivery", citation=["your profile"], evidence="")]
    out = dedupe_claims(items)
    assert len(out) == 2, [i.text for i in out]
    assert out[0].text == "7,000+ Customers Daily", "the first statement of a fact is the one kept"


# ---------------------------------------------------------------------
# 2 — an absent source names itself
# ---------------------------------------------------------------------

def test_no_collected_customer_voice_is_stated_not_silent():
    """A SWOT with no review-derived item must say whether that is because none were found or
    because none were collected. Silence lets a client assume the reviews were read."""
    from competitor.swot import customer_voice_note
    note = customer_voice_note(themes=[], collected=0)
    assert note and ("0" in note or "لم" in note or "مفيش" in note or "no " in note.lower())


def test_collected_but_unusable_says_so_differently():
    """4 comments that carry no praise or complaint is a different fact from 0 comments, and the
    client is entitled to the difference."""
    from competitor.swot import customer_voice_note
    few = customer_voice_note(themes=[], collected=4)
    none = customer_voice_note(themes=[], collected=0)
    assert few and none and few != none


def test_a_swot_with_real_voice_adds_no_note():
    from competitor.swot import customer_voice_note
    assert customer_voice_note(themes=[object()], collected=12) == ""
