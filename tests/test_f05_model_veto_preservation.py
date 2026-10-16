"""F-05: the expected-brand excuse may clear ONE flag, never manufacture the critic's approval.

`_excuse_expected_brand` exists for a real, measured defect (Al Fouad 2026-07-26): the owner
pasted a La Roche-Posay product link, the poster showed exactly that product, and the
`third_party_mark` HARD GATE failed a correct ad. Clearing that one flag is the intended
exception and these tests protect it.

What is NOT intended is reopening the critic's own top-level FAIL. `overall_pass` from the model
is an independent veto channel: `_recompute_pass` ANDs it with the code-side gates, so the model
can fail a poster for something no structured flag represents (`garbled_text`, `single_focal`,
`cta_prominent`, `placement_logical`, or plain prose). Recomputing the excused verdict as though
the model had approved it converts that veto into a PASS.

Hermetic: a typed fake caller stands in for the vision model. No image is rendered, no provider
is called, nothing is spent.
"""
from __future__ import annotations

import pytest

from poster.pipeline import _excuse_expected_brand
from poster.vision_qa import poster_vision_qa, third_party_is_expected


# The measured case this exception was built for.
_PRODUCT = "La Roche-Posay Anthelios Anti-Dark Spot Fluid SPF50+"
_EXPECTED_BRAND_REASON = "the ad is dominated by the third-party brand La Roche-Posay"

_CLEAN = {
    "has_latin_text": False,
    "cta_clipped": False,
    "on_brand_color": True,
    "candid_violation": False,
    "logo_ok": True,
    "script_wellformed": True,
    "invented_product": False,
    "third_party_mark": False,
    "garbled_text": False,
    "single_focal": True,
    "cta_prominent": True,
    "placement_logical": True,
    "score": 9,
    "overall_pass": True,
    "reason": "clean",
}


def _verdict(**updates):
    """The real `poster_vision_qa` driven by a typed fake model response."""
    payload = {**_CLEAN, **updates}

    def caller(_system, _user, schema, **_kwargs):
        return schema(**payload), {"total_tokens": 0}

    return poster_vision_qa(
        b"\x89PNG\r\n\x1a\n" + b"0" * 64,
        caller=caller,
        arabic=payload.pop("_arabic", True) if "_arabic" in payload else True,
        logo_composited=False,
    )


def _excused(verdict, *, arabic: bool = True, logo_composited: bool = False):
    return _excuse_expected_brand(verdict, _PRODUCT, arabic=arabic,
                                  logo_composited=logo_composited)


def test_the_fixture_actually_triggers_the_exception():
    """Guards the guard: every case below is meaningless if the excuse never engages."""
    assert third_party_is_expected(_EXPECTED_BRAND_REASON, product_name=_PRODUCT) is True
    assert third_party_is_expected("a stray Nike swoosh on the model's shirt",
                                   product_name=_PRODUCT) is False


# ---------------------------------------------------------------------------
# A — the baseline
# ---------------------------------------------------------------------------

def test_a_model_pass_with_every_hard_gate_green_passes():
    assert _verdict().overall_pass is True


# ---------------------------------------------------------------------------
# B — the exception this wrapper exists for, and must keep
# ---------------------------------------------------------------------------

def test_b_the_requested_products_own_brand_is_still_excused():
    """The Al Fouad case: the critic approved the poster, the code-side mark gate failed it."""
    verdict = _verdict(third_party_mark=True, reason=_EXPECTED_BRAND_REASON)
    assert verdict.overall_pass is False, "the hard gate should have failed it first"

    excused = _excused(verdict)
    assert excused.third_party_mark is False
    assert excused.overall_pass is True, "the intended expected-brand exception was destroyed"


def test_b_an_unrelated_mark_is_never_excused():
    verdict = _verdict(third_party_mark=True,
                       reason="a stray Nike swoosh is rendered on the model's shirt")
    excused = _excused(verdict)
    assert excused.third_party_mark is True
    assert excused.overall_pass is False


# ---------------------------------------------------------------------------
# C / E — an explicit model veto the structured flags do not represent
# ---------------------------------------------------------------------------

def test_c_a_model_veto_with_no_structured_flag_is_not_reopened_by_the_excuse():
    """E: every gate in the conjunction is green, so only the critic's own FAIL stands.

    Nothing here proves the veto was ONLY the expected brand — the reason names a second,
    unstructured complaint — so the excuse must not manufacture approval.
    """
    verdict = _verdict(
        third_party_mark=True,
        overall_pass=False,
        reason=(_EXPECTED_BRAND_REASON
                + "; and the composition is chaotic with competing focal points"),
    )
    assert verdict.overall_pass is False

    excused = _excused(verdict)
    assert excused.third_party_mark is False, "the flag itself is still a false positive"
    assert excused.overall_pass is False, (
        "an explicit model veto was converted into a PASS by the expected-brand excuse")


def test_c_a_bare_model_veto_carries_no_proof_of_exclusivity():
    """The critic failed it and said only that the expected brand is present.

    Even here the veto is one bit over free text: it is not evidence that the expected brand was
    the ONLY ground. Fail-safe is the required answer.
    """
    verdict = _verdict(third_party_mark=True, overall_pass=False,
                       reason=_EXPECTED_BRAND_REASON)
    excused = _excused(verdict)
    assert excused.overall_pass is False


# ---------------------------------------------------------------------------
# D — the excused condition plus an independent failure
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("invented_product", True),
        ("cta_clipped", True),
        ("on_brand_color", False),
        ("candid_violation", True),
        ("script_wellformed", False),
        ("logo_ok", False),
        ("has_latin_text", True),       # arabic authored copy
        ("score", 5),
    ],
)
def test_d_an_independent_hard_failure_survives_the_excuse(field, value):
    verdict = _verdict(third_party_mark=True, reason=_EXPECTED_BRAND_REASON, **{field: value})
    assert _excused(verdict).overall_pass is False, field


def test_d_a_model_veto_over_garbled_text_survives_the_excuse():
    """`garbled_text` is a real structured finding that the conjunction does NOT contain.

    It is the owner's «SAV TAERMALE» defect. The critic can only express it through its own
    FAIL, which is exactly the channel the excuse was overwriting.
    """
    verdict = _verdict(third_party_mark=True, overall_pass=False, garbled_text=True,
                       reason=_EXPECTED_BRAND_REASON + "; the label text is malformed")
    excused = _excused(verdict)
    assert excused.garbled_text is True
    assert excused.overall_pass is False


def test_d_a_model_veto_over_placement_survives_the_excuse():
    verdict = _verdict(third_party_mark=True, overall_pass=False, placement_logical=False,
                       reason=_EXPECTED_BRAND_REASON + "; the pack floats in mid-air")
    assert _excused(verdict).overall_pass is False


# ---------------------------------------------------------------------------
# The enabling invariant: a code-side gate failure is distinguishable from a model veto
# ---------------------------------------------------------------------------

def test_the_verdict_records_what_the_critic_itself_decided():
    """Without this the excuse cannot tell 'the code failed it' from 'the critic failed it'.

    `overall_pass` is the RECOMPUTED answer, so the critic's own aggregate has to survive
    separately or every later reader has to guess it — and the guess in place was `True`.
    """
    approved = _verdict(third_party_mark=True, reason=_EXPECTED_BRAND_REASON)
    assert approved.overall_pass is False        # the code-side mark gate
    assert getattr(approved, "critic_pass", None) is True, "the critic's approval was lost"

    vetoed = _verdict(overall_pass=False, reason="the composition is chaotic")
    assert getattr(vetoed, "critic_pass", None) is False, "the critic's veto was lost"


def test_an_unknown_critic_aggregate_is_never_read_as_approval():
    """A verdict with no recorded critic aggregate must not be excused into a PASS."""
    from poster.vision_qa import PosterQAVerdict

    verdict = PosterQAVerdict(third_party_mark=True, overall_pass=False,
                              reason=_EXPECTED_BRAND_REASON, checked=True, score=9)
    assert getattr(verdict, "critic_pass", None) is None
    assert _excused(verdict).overall_pass is not True


# ---------------------------------------------------------------------------
# The excuse never makes anything MORE permissive than the hard-gate contract
# ---------------------------------------------------------------------------

def test_a_model_pass_with_a_failing_hard_gate_stays_failed_through_the_excuse():
    verdict = _verdict(third_party_mark=True, reason=_EXPECTED_BRAND_REASON, cta_clipped=True)
    assert verdict.overall_pass is False
    assert _excused(verdict).overall_pass is False


def test_the_excuse_is_a_no_op_when_no_product_was_requested():
    verdict = _verdict(third_party_mark=True, reason=_EXPECTED_BRAND_REASON)
    unchanged = _excuse_expected_brand(verdict, "", arabic=True, logo_composited=False)
    assert unchanged.third_party_mark is True
    assert unchanged.overall_pass is False


def test_the_excuse_never_turns_a_pass_into_a_fail():
    clean = _verdict()
    assert _excused(clean).overall_pass is True


def test_the_excuse_operates_on_the_same_candidates_metadata():
    """§5: the flag cleared, the reason kept, and the aggregate decided must be one verdict."""
    verdict = _verdict(third_party_mark=True, reason=_EXPECTED_BRAND_REASON, score=9)
    excused = _excused(verdict)
    assert excused.reason == verdict.reason
    assert excused.score == verdict.score
    assert excused.text_lines == verdict.text_lines
    assert excused is not verdict, "the excuse must not mutate the candidate's own verdict"
