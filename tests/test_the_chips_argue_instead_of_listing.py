# -*- coding: utf-8 -*-
"""The proof chips are an argument, not a spec sheet.

OWNER (2026-08-06): «الكلام اللي عالبوستر… محتاج يبقا له معنى أكتر، مش أي كلام جنب بعضه».

MEASURED on the poster NTI actually shipped, and the two chips fail in two different ways:

    headline  «زمان كنت بتسافرله. دلوقتي هو اللي جنبك.»
    chip 1    «موجودين في معظم محافظات مصر»       <- the headline already said exactly this
    chip 2    «شهادات معتمدة من شركات عالمية»     <- a CATEGORY, printable on any competitor

Chip 1 spends a slot on information the reader already has. Chip 2 says nothing only this brand
can say. Meanwhile NTI's own profile carried «اعتماد المجلس الأعلى للجامعات», «شهادة Huawei Big
Data Associate», «أول معمل متنقل في مصر لشهادة CCIE بالشراكة مع Cisco» and «تأسست 1983» — named,
checkable, unavailable to a competitor. The evidence was there; the brief never asked for it.

LIVE A/B after the contract landed (same profile, same model, two runs):

    headline  «خلّي شهادتك تشغّلك.»
    chips     «شهادات معتمدة من شركات عالمية زي هواوي.»        AUTHORITY, named
              «تدريب عملي مكثف في ٤ شهور بس.»                  NUMBER
              «متاح في مراكز إبداع مصر الرقمية في محافظتك.»    ACCESS, named

    named specifics   0 -> 2-3      distinct proof kinds   1 -> 3      headline echoes   1 -> 0

Three different KINDS of proof, all named, none repeating the headline — together they answer the
question the headline provokes ("why would a certificate get me hired?"): Huawei accredits it, it
takes four months, and it is in your governorate. That is a case rather than a list.
"""
from __future__ import annotations

import inspect
import re

import poster.concept as pc

# NO MECHANICAL ECHO CHECK — AND THE REASON IS THE MEASUREMENT ITSELF.
#
# I first wrote a word-overlap detector for "this chip restates the headline" and it FAILED on the
# very case it was built for. The shipped pair —
#
#     headline  «زمان كنت بتسافرله. دلوقتي هو اللي جنبك.»
#     chip      «موجودين في معظم محافظات مصر»
#
# — shares ZERO words. The repetition is entirely semantic: "now it is next to you" and "we are in
# most governorates" are the same claim in different vocabulary. This is the lesson `process.md`
# already records: *"A token/stem heuristic cannot judge SUBJECT identity"* — it cannot separate a
# paraphrase from an unrelated statement, because both are token-disjoint.
#
# So the no-repeat rule is enforced by the BRIEF, where a model that understands meaning can apply
# it, and this file does not pretend to check it. What IS mechanically checkable — named specifics
# and distinct kinds of proof — is checked below on the real before/after.


# =====================================================================
# the contract reaches the brief
# =====================================================================

def test_the_three_rules_are_in_the_concept_prompt():
    """The chips are written by a model, so the only place this can be enforced is the ask."""
    src = inspect.getsource(pc)
    assert "PROOF THAT ARGUES" in src
    assert "NEVER restate the headline" in src
    assert "DIFFERENT KIND of proof" in src
    assert "NAMED over the categorical" in src


def test_every_kind_of_proof_is_named_for_the_model():
    """'Different kinds' is meaningless unless the kinds are enumerated."""
    src = inspect.getsource(pc)
    for kind in ("AUTHORITY", "NUMBER", "NAMED SPECIFIC", "ACCESS"):
        assert kind in src, f"the brief never tells the model what a {kind} proof is"


def test_the_brief_prefers_dropping_a_point_to_writing_its_category():
    """The failure mode this closes: 'accredited by international companies' is what every
    competitor says. Without an explicit instruction the model fills the slot rather than
    leaving it empty."""
    src = inspect.getsource(pc)
    assert "drop that point rather than" in src


def test_the_chips_are_told_what_job_they_do():
    src = inspect.getsource(pc)
    assert "the one question the headline provokes" in src


# =====================================================================
# the measured examples, as data
# =====================================================================

SHIPPED_HEADLINE = "زمان كنت بتسافرله. دلوقتي هو اللي جنبك."
SHIPPED_CHIPS = ["موجودين في معظم محافظات مصر", "شهادات معتمدة من شركات عالمية"]

AFTER_HEADLINE = "خلّي شهادتك تشغّلك."
AFTER_CHIPS = ["شهادات معتمدة من شركات عالمية زي هواوي.",
               "تدريب عملي مكثف في ٤ شهور بس.",
               "متاح في مراكز إبداع مصر الرقمية في محافظتك."]


def test_the_shipped_repetition_is_invisible_to_word_overlap():
    """Pinned because it is the reason there is no automatic check here. The shipped chip restates
    its headline and the two share NOT ONE word — a lexical detector would have passed it, which
    is how a wrong gate ends up certifying the defect it was written for."""
    words = lambda s: set(re.findall(r"[؀-ۿA-Za-z]{3,}", s))          # noqa: E731
    assert words(SHIPPED_HEADLINE) & words(SHIPPED_CHIPS[0]) == set()


def test_the_measured_result_carries_named_specifics():
    """'international companies' -> 'Huawei'. A name is what a competitor cannot copy."""
    named = [c for c in AFTER_CHIPS if "هواوي" in c or "مصر الرقمية" in c]
    assert len(named) >= 2, named
    assert not any("هواوي" in c or "مصر الرقمية" in c for c in SHIPPED_CHIPS)


def test_the_measured_result_uses_more_than_one_kind_of_proof():
    """A number, an authority and an access fact — three different questions answered, which is
    what turns two facts standing next to each other into a case."""
    has_number = any(re.search(r"[0-9٠-٩]", c) for c in AFTER_CHIPS)
    has_authority = any("معتمد" in c for c in AFTER_CHIPS)
    has_access = any("متاح" in c or "محافظ" in c for c in AFTER_CHIPS)
    assert has_number and has_authority and has_access
    # the shipped pair managed one kind between them
    assert not any(re.search(r"[0-9٠-٩]", c) for c in SHIPPED_CHIPS)


def test_the_chip_count_grew_because_each_one_now_earns_its_slot():
    """Two chips became three — not padding: the third is a whole kind of proof (access) the
    shipped pair never offered."""
    assert len(SHIPPED_CHIPS) == 2 and len(AFTER_CHIPS) == 3
