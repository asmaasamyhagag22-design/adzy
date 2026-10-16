"""One-shot candidate selection returns the safest best render, not merely the latest one.

Hermetic: synthetic image bytes, typed QA verdicts, and local monkeypatches only.  No network,
image model, vision model, or paid call.
"""
from __future__ import annotations

import base64
import json
from pathlib import Path

import pytest

import poster.oneshot as oneshot
import poster.pipeline as pipeline
from poster.concept import CreativeConcept
from poster.from_profile import build_poster_brief
from poster.vision_qa import PosterQAVerdict, _recompute_verdict_pass


_PROFILE = {
    "name": {"value": "Synthetic Studio", "evidence": [
        {"block_id": "b1", "page_url": "https://synthetic.invalid/", "quote": "Synthetic Studio"}
    ]},
    "languages": ["en"],
    "tagline": {"value": "Build clear ideas", "evidence": [
        {"block_id": "b2", "page_url": "https://synthetic.invalid/", "quote": "Build clear ideas"}
    ]},
    "offerings": [],
}


def _concept() -> CreativeConcept:
    return CreativeConcept(
        audience="teams", single_message="one clear idea", core_benefit="clarity",
        emotional_tone="confident", visual_idea="clean abstract studio",
        proof_points=[], headline="Build clear ideas", subheadline="", cta="Learn More",
    )


class _OCRCaller:
    """Return the exact authored copy so every generated candidate reaches structured QA."""

    def __call__(self, _system, _user, response_model, group_name="", images=None):
        return response_model(lines=["Build clear ideas", "Learn More"]), None


def _verdict(*, score: int, reason: str, **updates) -> PosterQAVerdict:
    """Build a production-possible verdict and let the canonical F-05 conjunction decide pass."""
    draft = PosterQAVerdict(
        checked=True,
        overall_pass=True,
        # The recomputation below is handed `model_pass=True`, so this fixture already models a
        # critic that APPROVED. Recording that on the verdict keeps it production-possible.
        critic_pass=True,
        score=score,
        reason=reason,
        text_lines=["Build clear ideas", "Learn More"],
        **updates,
    )
    return draft.model_copy(update={
        "overall_pass": _recompute_verdict_pass(
            draft, arabic=False, logo_composited=False, model_pass=True),
    })


def _run_sequence(monkeypatch, tmp_path, verdicts: list[PosterQAVerdict]):
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "hermetic-test-project")
    generated = {"count": 0}
    qa_queue = list(verdicts)

    def fake_generate(_prompt, out_path, **_kwargs):
        generated["count"] += 1
        Path(out_path).write_bytes(f"candidate-{generated['count']}".encode("ascii"))
        return Path(out_path)

    def fake_qa(*_args, **_kwargs):
        verdict = qa_queue.pop(0)
        identity = _kwargs["expected_attempt_identity"]
        policy = _kwargs["required_policy"]
        return verdict.model_copy(update={
            "qa_source": "poster.vision_qa.poster_vision_qa",
            "policy_id": policy.policy_id,
            "policy_version": policy.version,
            "policy_context": dict(policy.context),
            "mandatory_checks_expected": list(policy.mandatory_checks),
            "mandatory_checks_completed": list(policy.mandatory_checks),
            "non_applicable_checks": dict(policy.non_applicable_checks),
            "attempt_identity": identity,
        })

    monkeypatch.setattr(oneshot, "generate_oneshot_poster", fake_generate)
    monkeypatch.setattr(pipeline, "poster_vision_qa", fake_qa)
    monkeypatch.setattr(pipeline, "_gather_product_props",
                        lambda *_a, **_k: ([], [], "prop"))

    brief = build_poster_brief(_PROFILE, headline_override="Build clear ideas")
    brief = brief.model_copy(update={"cta_text": "Learn More", "logo_url": None})
    result = pipeline._try_oneshot(
        _PROFILE, brief, _concept(), None, _OCRCaller(),
        arabic=False, audit={"asset_type": "poster", "final_copy": {"fields": []}},
        out_dir=str(tmp_path), variation=None, max_retries=len(verdicts) - 1,
        log=lambda _message: None,
    )
    return result, generated["count"], len(qa_queue)


def _candidate_number(result) -> int:
    assert result is not None
    raw = base64.b64decode(result.image_base64).decode("ascii")
    assert Path(result.poster_path).read_bytes().decode("ascii") == raw
    return int(raw.rsplit("-", 1)[1])


def _cosmetic_fail(score: int, n: int) -> PosterQAVerdict:
    return _verdict(score=score, reason=f"candidate-{n}: candid image", candid_violation=True)


def _clip_fail(score: int, n: int) -> PosterQAVerdict:
    return _verdict(score=score, reason=f"candidate-{n}: CTA clipped", cta_clipped=True)


@pytest.mark.parametrize(
    ("verdicts", "selected", "generated"),
    [
        pytest.param([_cosmetic_fail(9, 1), _clip_fail(2, 2)], 1, 2,
                     id="higher-quality-earlier-beats-lower-nonfixable-later"),
        pytest.param([_cosmetic_fail(2, 1), _cosmetic_fail(9, 2)], 2, 2,
                     id="higher-quality-later-wins-within-same-safe-class"),
        pytest.param([_cosmetic_fail(3, 1), _verdict(score=9, reason="candidate-2: pass")], 2, 2,
                     id="later-pass-beats-every-earlier-failure"),
        pytest.param([_verdict(score=8, reason="candidate-1: pass"), _cosmetic_fail(10, 2)], 1, 1,
                     id="first-pass-returns-without-an-unnecessary-generation"),
        pytest.param([
            _verdict(score=10, reason="candidate-1: invented product", invented_product=True),
            _cosmetic_fail(7, 2),
        ], 2, 2, id="safer-failure-beats-higher-score-integrity-failure"),
        pytest.param([
            _cosmetic_fail(7, 1),
            _verdict(score=10, reason="candidate-2: invented product", invented_product=True),
        ], 1, 2, id="later-higher-score-cannot-displace-safer-earlier-failure"),
        pytest.param([_cosmetic_fail(4, 1), _cosmetic_fail(8, 2), _cosmetic_fail(6, 3)], 2, 3,
                     id="middle-candidate-remains-best-when-last-is-worse"),
        pytest.param([
            _verdict(score=7, reason="candidate-1: invented product", invented_product=True),
            _cosmetic_fail(7, 2),
        ], 2, 2, id="equal-score-tie-is-broken-by-integrity-class"),
        pytest.param([_cosmetic_fail(7, 1), _cosmetic_fail(7, 2)], 1, 2,
                     id="exact-rank-tie-deterministically-keeps-earlier-candidate"),
        pytest.param([_clip_fail(5, 1)], 1, 1,
                     id="only-invalid-candidate-is-returned-with-its-verdict"),
    ],
)
def test_oneshot_sequence_selects_by_validity_integrity_then_quality(
        monkeypatch, tmp_path, verdicts, selected, generated):
    result, generation_count, remaining = _run_sequence(monkeypatch, tmp_path, verdicts)
    assert _candidate_number(result) == selected
    assert result.qa.reason.startswith(f"candidate-{selected}:")
    assert result.qa.overall_pass is (True if "pass" in result.qa.reason else False)
    assert generation_count == generated
    assert remaining == len(verdicts) - generated


def test_nonfixable_stop_returns_best_image_qa_and_public_failure_disclosure_together(
        monkeypatch, tmp_path):
    """Exercise the selected PosterGenResult through the customer-visible image response."""
    selected, generation_count, _ = _run_sequence(
        monkeypatch, tmp_path,
        [_cosmetic_fail(9, 1), _clip_fail(2, 2), _cosmetic_fail(10, 3)],
    )
    assert generation_count == 2  # non-fixable stops retries; it does not decide which image wins
    assert _candidate_number(selected) == 1

    import api.webapp_images as web_images

    selected_png = base64.b64decode(selected.image_base64)
    monkeypatch.setattr(web_images, "paid_images_enabled", lambda: True)
    monkeypatch.setattr(
        web_images, "_render_real",
        lambda *_a, **_k: web_images.RealRender(png=selected_png, result=selected),
    )
    monkeypatch.setattr(web_images, "_caption_for", lambda *_a, **_k: {})

    response = web_images.generate_image(
        {"domain_key": "synthetic", "_profile": _PROFILE}, headline="Build clear ideas")
    assert base64.b64decode(response["image_b64"]) == b"candidate-1"
    assert response["qa_failed"] is True
    assert response["qa_note"] == selected.qa.reason == "candidate-1: candid image"


def test_ranking_consumes_a_wrapper_modified_canonical_verdict(monkeypatch, tmp_path):
    """A wrapper's structured correction is ranked after its canonical pass is recomputed."""
    raw = _verdict(
        score=9,
        reason="The Synthetic Product mark belongs to the advertised product",
        third_party_mark=True,
    )
    adjusted = pipeline._excuse_expected_brand(
        raw, "Synthetic Product", arabic=False, logo_composited=False)
    assert adjusted.third_party_mark is False and adjusted.overall_pass is True

    selected, generation_count, _ = _run_sequence(
        monkeypatch, tmp_path, [_cosmetic_fail(10, 1), adjusted])
    assert generation_count == 2
    assert _candidate_number(selected) == 2
    assert selected.qa.third_party_mark is adjusted.third_party_mark is False
    assert selected.qa.overall_pass is adjusted.overall_pass is True
    assert selected.qa.attempt_identity is not None


def test_selected_failed_fallback_remains_diagnostic_not_canonical(
        monkeypatch, tmp_path):
    """Best available failed pixels remain auditable but never enter the accepted sink."""
    canonical = tmp_path / "synthetic_poster.png"
    monkeypatch.setitem(pipeline._CANONICAL_SINK, "path", str(canonical))

    selected, generation_count, _ = _run_sequence(
        monkeypatch, tmp_path,
        [_cosmetic_fail(9, 1), _clip_fail(2, 2), _cosmetic_fail(10, 3)],
    )
    assert generation_count == 2
    assert _candidate_number(selected) == 1
    assert not canonical.exists()
    assert selected.acceptance.accepted is False
    assert base64.b64decode(selected.image_base64) == b"candidate-1"


def test_explicit_complete_pass_is_the_only_candidate_published_canonical(
        monkeypatch, tmp_path):
    canonical = tmp_path / "synthetic_poster.png"
    monkeypatch.setitem(pipeline._CANONICAL_SINK, "path", str(canonical))

    selected, generation_count, _ = _run_sequence(
        monkeypatch, tmp_path, [_verdict(score=8, reason="candidate-1: pass")])

    assert generation_count == 1
    assert selected.acceptance.accepted is True
    assert canonical.read_bytes() == b"candidate-1"


def test_unverified_qa_is_not_accepted_published_or_retried_without_policy(
        monkeypatch, tmp_path):
    canonical = tmp_path / "synthetic_poster.png"
    monkeypatch.setitem(pipeline._CANONICAL_SINK, "path", str(canonical))
    unchecked = PosterQAVerdict(
        checked=False, overall_pass=None, critic_pass=None,
        reason="evaluator unavailable")

    selected, generation_count, remaining = _run_sequence(
        monkeypatch, tmp_path, [unchecked, unchecked, unchecked])

    assert generation_count == 1
    assert remaining == 2
    assert selected.qa.overall_pass is None
    assert selected.acceptance.qa_status == "QA_UNVERIFIED"
    assert selected.acceptance.accepted is False
    assert not canonical.exists()


def test_public_generate_poster_returns_and_audits_the_same_selected_candidate(
        monkeypatch, tmp_path):
    """Run the real public poster entry point; only model/network seams are faked."""
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "hermetic-test-project")
    generated = {"count": 0}
    verdicts = [_cosmetic_fail(9, 1), _clip_fail(2, 2)]

    def fake_generate(_prompt, out_path, **_kwargs):
        generated["count"] += 1
        Path(out_path).write_bytes(f"candidate-{generated['count']}".encode("ascii"))

    monkeypatch.setattr(oneshot, "generate_oneshot_poster", fake_generate)
    monkeypatch.setattr(pipeline, "poster_vision_qa", lambda *_a, **_k: verdicts.pop(0))
    monkeypatch.setattr(pipeline, "_gather_product_props",
                        lambda *_a, **_k: ([], [], "prop"))
    monkeypatch.setattr(pipeline, "build_creative_concept", lambda *_a, **_k: _concept())
    monkeypatch.setattr(pipeline, "build_variation", lambda: {})

    import competitor.search_providers as search_providers
    monkeypatch.setattr(search_providers, "get_default_search_provider", lambda: None)

    canonical = tmp_path / "public_poster.png"
    result = pipeline.generate_poster(
        _PROFILE,
        caller=_OCRCaller(),
        qa_caller=object(),
        use_dna=False,
        engine="oneshot",
        language="en",
        max_qa_retries=1,
        out_dir=str(tmp_path),
        canonical_out=str(canonical),
        log=lambda _message: None,
    )

    assert generated["count"] == 2
    assert _candidate_number(result) == 1
    assert not canonical.exists()
    assert result.acceptance.accepted is False
    assert result.qa.reason == "candidate-1: candid image"
    audit = json.loads(Path(result.poster_path).with_suffix(".audit.json").read_text("utf-8"))
    assert "candidate-1: candid image" in json.dumps(audit, ensure_ascii=False)
    assert audit["poster_acceptance"]["accepted"] is False
