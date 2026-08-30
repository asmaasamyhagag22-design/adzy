"""P-70: the LLM validator must check that a cited quote SUPPORTS the extracted value's hard
claim, not merely that the quote is authentic.

Before, a hallucinated hard claim ("Egypt's #1 pharmacy") attached to ANY real but unrelated quote
passed validation. Now a value carrying a hard, falsifiable claim (number/ranking/credential) is
dropped unless that claim resolves against the cited evidence — while pure paraphrase and soft
marketing superlatives (best/leading) are unaffected (they are commonly synthesised).
"""
from __future__ import annotations

from business_profile.llm.validator import _value_hard_claims_supported


class _Ev:
    def __init__(self, quote):
        self.quote = quote


def test_fabricated_ranking_with_unrelated_quote_is_not_supported():
    # value claims a #1 ranking; the only cited quote is a real-but-unrelated contact line.
    assert _value_hard_claims_supported("Egypt's #1 pharmacy", [_Ev("Contact us at 0123456789")]) is False


def test_fabricated_founding_year_not_in_evidence_is_not_supported():
    assert _value_hard_claims_supported("Serving customers since 1965", [_Ev("We deliver nationwide.")]) is False


def test_supported_year_passes():
    assert _value_hard_claims_supported("Serving since 1983", [_Ev("Established in 1983 in Cairo.")]) is True


def test_pure_paraphrase_always_passes():
    assert _value_hard_claims_supported("Natural skincare for everyday radiance",
                                        [_Ev("We make natural skincare products.")]) is True


def test_descriptive_non_claim_adjective_is_not_dropped():
    # "modern" is a descriptive adjective, NOT a falsifiable claim (unlike a ranking/number), so a
    # synthesised description carrying it must NOT be dropped for lacking a verbatim quote.
    assert _value_hard_claims_supported("A modern dermatology clinic in Cairo",
                                        [_Ev("Our dermatology clinic serves patients in Cairo.")]) is True


def test_ranking_word_leading_is_treated_as_a_claim_and_requires_support():
    # The project's Evidence Ledger classes "leading" as a ranking (first-group) claim — the same
    # standard it applies to all copy. So an unsupported "leading" is correctly required here too.
    unsupported = _value_hard_claims_supported("A leading dermatology clinic in Cairo",
                                               [_Ev("Our dermatology clinic serves patients in Cairo.")])
    supported = _value_hard_claims_supported("A leading dermatology clinic",
                                             [_Ev("We are the leading dermatology clinic in Cairo.")])
    assert unsupported is False and supported is True


def test_no_evidence_degrades_to_true():
    # No accepted quotes -> the strict require_evidence gate handles that; this check doesn't block.
    assert _value_hard_claims_supported("Egypt's #1 pharmacy", []) is True


def test_non_string_value_passes():
    assert _value_hard_claims_supported(None, [_Ev("x")]) is True
