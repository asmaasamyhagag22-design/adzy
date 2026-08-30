# -*- coding: utf-8 -*-
"""Best-attempt selection: honesty outranks beauty.

MEASURED (nanocare, 2026-07-24): the image model redrew the product box's label —
"Liposomal Iron" became a fabricated "Lifosemul Iron". Vision-QA caught it
(invented_product=True, overall_pass=False) — and `generate_poster` still returned that
attempt as "best" because best was ranked BY SCORE ALONE. A poster that lies about the
client's own product must never win over a lower-scored honest attempt: that is a
grounding failure (the moat), not a taste failure.

Two-part policy, following the file's established pattern of testing extracted pure
functions (the loop wiring is 3 lines over these):
  1. `_trust_violation`: the grounding-class defects (fabricated product, third-party
     mark, broken script) — distinct from aesthetic failures (score, focal point).
  2. `_attempt_rank`: (no-violation, score) — an honest attempt always outranks a
     violating one; score only breaks ties within the same honesty class.
When EVERY attempt violates, the result ships with an EXPLICIT disclosure (top-level
flag + client-facing Arabic note) — «الضعف المُفصح عنه درع». Whether it should instead
be refused outright is the owner's open decision (logged as D-7); the disclosure default
is the non-breaking one. Hermetic."""
from __future__ import annotations

from poster.pipeline import _TRUST_NOTE_AR, PosterGenResult, _attempt_rank, _trust_violation
from poster.vision_qa import PosterQAVerdict


def _verdict(**kw) -> PosterQAVerdict:
    base = dict(checked=True, overall_pass=False, score=5, reason="x")
    base.update(kw)
    return PosterQAVerdict(**base)


def _result(qa: PosterQAVerdict) -> PosterGenResult:
    from poster.schemas import PosterBrief, PosterDesignSpec
    from poster.concept import CreativeConcept
    return PosterGenResult(
        poster_path="p.png", filename="p.png", image_base64="",
        brief=PosterBrief(business_name="Nano Care", category="beauty", headline="كل حباية بتوصل"),
        spec=PosterDesignSpec(), concept=CreativeConcept(), brand_dna=None,
        background_path="b.png", model_used="m", prompt="", qa=qa)


def test_the_grounding_class_is_exactly_the_lying_defects():
    assert _trust_violation(_verdict(invented_product=True))
    assert _trust_violation(_verdict(third_party_mark=True))
    assert _trust_violation(_verdict(script_wellformed=False))
    # aesthetic failures are NOT trust violations — a low score alone is taste, not lying
    assert not _trust_violation(_verdict(score=2, single_focal=False))


def test_an_honest_attempt_outranks_a_prettier_fabrication():
    honest = _result(_verdict(score=5))
    fabricated = _result(_verdict(score=9, invented_product=True))
    assert _attempt_rank(honest) > _attempt_rank(fabricated), (
        "the nanocare failure: a score-9 fabricated label beat a score-5 honest attempt")


def test_score_still_breaks_ties_within_the_same_honesty_class():
    assert _attempt_rank(_result(_verdict(score=7))) > _attempt_rank(_result(_verdict(score=4)))
    a = _result(_verdict(score=7, invented_product=True))
    b = _result(_verdict(score=4, invented_product=True))
    assert _attempt_rank(a) > _attempt_rank(b)      # least-bad among the bad


def test_the_result_can_disclose_and_defaults_clean():
    r = _result(_verdict())
    assert r.trust_violation is False               # additive field, older callers unaffected
    assert r.trust_note_ar == ""


def test_the_arabic_note_is_owner_voiced():
    assert _TRUST_NOTE_AR.strip(), "the disclosure note must exist"
    assert any("؀" <= ch <= "ۿ" for ch in _TRUST_NOTE_AR), "client-facing => Arabic"
    assert "صفر هلوسة" not in _TRUST_NOTE_AR        # the banned overclaim, anywhere, always
    assert "مراجعة" in _TRUST_NOTE_AR, "the note must tell the client what to do: review it"
