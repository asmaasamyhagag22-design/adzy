"""F-05: code, not the model, owns the complete Vision-QA hard conjunction.

Hermetic: synthetic bytes and typed fake model responses only; no render, network, or spend.
"""
from __future__ import annotations

import pytest

from poster.vision_qa import PosterQAVerdict, poster_vision_qa


_CLEAN = {
    "has_latin_text": False,
    "cta_clipped": False,
    "on_brand_color": True,
    "candid_violation": False,
    "logo_ok": True,
    "script_wellformed": True,
    "invented_product": False,
    "third_party_mark": False,
    "score": 9,
    "overall_pass": True,
    "reason": "clean",
}


def _run_qa(*, arabic: bool = True, logo_composited: bool = False, **updates):
    payload = {**_CLEAN, **updates}

    def caller(_system, _user, schema, **_kwargs):
        return schema(**payload), {"total_tokens": 0}

    return poster_vision_qa(
        b"\x89PNG\r\n\x1a\n" + b"0" * 64,
        caller=caller,
        arabic=arabic,
        logo_composited=logo_composited,
    )


@pytest.mark.parametrize(
    ("field", "invalid"),
    [
        ("has_latin_text", True),
        ("cta_clipped", True),
        ("on_brand_color", False),
        ("candid_violation", True),
        ("logo_ok", False),
        ("script_wellformed", False),
        ("invented_product", True),
        ("third_party_mark", True),
        ("score", 6),
    ],
)
def test_one_invalid_hard_field_defeats_model_overall_pass(field, invalid):
    verdict = _run_qa(**{field: invalid})
    assert verdict.checked is True
    assert verdict.overall_pass is False, field


def test_fully_clean_structured_verdict_passes():
    assert _run_qa().overall_pass is True


def test_two_simultaneous_missing_hard_failures_cannot_cancel_each_other():
    verdict = _run_qa(has_latin_text=True, candid_violation=True, score=10)
    assert verdict.overall_pass is False


def test_latin_is_language_aware_not_a_universal_failure():
    # The field is scoped to authored copy. An English poster legitimately uses Latin.
    assert _run_qa(arabic=False, has_latin_text=True).overall_pass is True


def test_arabic_allows_latin_from_scoped_brand_and_product_assets():
    # Latin in a composited logo/product label is outside headline/chips/CTA, so the critic's
    # scoped flag remains false even though its transcription contains those legitimate labels.
    verdict = _run_qa(
        arabic=True,
        text_lines=["اطلب الآن", "NanoCare", "SPF50"],
        has_latin_text=False,
    )
    assert verdict.overall_pass is True
    assert "NanoCare" in verdict.text_lines


def test_runtime_prompt_uses_the_composited_logo_exception():
    seen = {}

    def caller(system, _user, schema, **_kwargs):
        seen["system"] = system
        return schema(**_CLEAN), {"total_tokens": 0}

    poster_vision_qa(
        b"\x89PNG\r\n\x1a\n" + b"0" * 64,
        caller=caller,
        logo_composited=True,
    )
    prompt = seen["system"]
    assert "brand's OWN file" in prompt
    assert "judge ONLY its visibility and placement" in prompt


def _expected_product_verdict(**updates) -> PosterQAVerdict:
    base = {
        **_CLEAN,
        "checked": True,
        # The Al Fouad shape: the CRITIC approved the poster and the code-side `third_party_mark`
        # gate is what failed it. `critic_pass` records that approval, which is the only evidence
        # entitling the wrapper to re-decide the aggregate at all.
        "critic_pass": True,
        "overall_pass": False,
        "third_party_mark": True,
        "reason": "The La Roche-Posay mark belongs to the advertised product",
    }
    base.update(updates)
    return PosterQAVerdict(**base)


def _excuse(verdict: PosterQAVerdict, *, arabic: bool | None = None,
            logo_composited: bool | None = None) -> PosterQAVerdict:
    from poster.pipeline import _excuse_expected_brand
    kwargs = {}
    if arabic is not None:
        kwargs["arabic"] = arabic
    if logo_composited is not None:
        kwargs["logo_composited"] = logo_composited
    return _excuse_expected_brand(verdict, "La Roche-Posay Anthelios Fluid", **kwargs)


def test_expected_product_wrapper_can_reopen_only_a_clean_result():
    verdict = _excuse(_expected_product_verdict())
    assert verdict.third_party_mark is False
    assert verdict.overall_pass is True


@pytest.mark.parametrize(
    ("field", "invalid"),
    [
        ("has_latin_text", True),
        ("cta_clipped", True),
        ("on_brand_color", False),
        ("candid_violation", True),
        ("logo_ok", False),
        ("script_wellformed", False),
        ("invented_product", True),
        ("score", 6),
    ],
)
def test_expected_product_wrapper_cannot_reopen_another_hard_failure(field, invalid):
    verdict = _excuse(_expected_product_verdict(**{field: invalid}))
    assert verdict.third_party_mark is False
    assert verdict.overall_pass is False, field


def test_wrapper_preserves_composited_logo_and_english_latin_exceptions():
    logo = _excuse(_expected_product_verdict(logo_ok=False), logo_composited=True)
    english = _excuse(_expected_product_verdict(has_latin_text=True), arabic=False)
    assert logo.overall_pass is True
    assert english.overall_pass is True


def test_unrelated_third_party_mark_is_never_excused():
    verdict = _expected_product_verdict(reason="Unrelated swoosh on the model's shirt")
    from poster.pipeline import _excuse_expected_brand
    out = _excuse_expected_brand(verdict, "La Roche-Posay Anthelios Fluid")
    assert out.third_party_mark is True and out.overall_pass is False
