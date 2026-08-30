"""P-42 / P-33: the poster audit sidecar must carry the vision-QA verdict (so a counterfeit mark /
invented product leaves a trace), and must NOT claim the generated scene is "text-free"."""
from __future__ import annotations

from poster.audit import attach_vision_qa, build_poster_audit, _POSTER_EXCLUDED
from poster.vision_qa import PosterQAVerdict


def test_vision_qa_verdict_is_recorded_in_audit():
    audit = {"asset_type": "poster"}
    # a poster that a vision model INSPECTED and rejected for a third-party mark (the topshoes case)
    qa = PosterQAVerdict(overall_pass=False, checked=True, third_party_mark=True,
                         invented_product=True, score=3, reason="Nike swoosh on the hero shoe")
    out = attach_vision_qa(audit, qa)
    vq = out["vision_qa"]
    assert vq["checked"] is True
    assert vq["overall_pass"] is False           # tri-state preserved (not silently True)
    assert vq["third_party_mark"] is True
    assert vq["invented_product"] is True
    assert vq["score"] == 3
    assert "swoosh" in vq["reason"].lower()


def test_not_checked_verdict_is_recorded_as_none_not_pass():
    audit = {"asset_type": "poster"}
    qa = PosterQAVerdict()  # defaults: overall_pass=None, checked=False (no vision model ran)
    vq = attach_vision_qa(audit, qa)["vision_qa"]
    assert vq["checked"] is False
    assert vq["overall_pass"] is None            # "cannot verify" is NOT spelled as "verified"


def test_attach_is_noop_without_audit_or_qa():
    assert attach_vision_qa(None, PosterQAVerdict()) is None
    assert attach_vision_qa({"x": 1}, None) == {"x": 1}


def test_audit_coverage_does_not_claim_text_free_scene():
    # P-33: the exclusion reason must not assert the imagery is text-free.
    reason = _POSTER_EXCLUDED["background_image"].lower()
    assert "text-free" not in reason
    assert "vision_qa" in reason                 # points to where any scene text IS recorded
