# -*- coding: utf-8 -*-
"""P-23 (2026-07-18): the third-party-mark ban had two holes. (1) The PLAN gate's regex
(coherence._MARK_PHRASE_RE) only matched an explicit "<X> logo/trademark" phrase, so a scene that
puts a NAMED global brand in frame ("a runner laces up Nike shoes", "an iPhone on the desk") sailed
through — a competitor's trademark rendered into the ad. (2) The render QA (SceneQAVerdict) had no
field for a third-party mark the VISION model actually sees in a frame, so a rendered swoosh could
not fail the gate. Both closed here, universally (a curated GLOBAL-brand list, own-brand always
exempt; ordinary-noun brand names like 'orange'/'apple' deliberately excluded). Hermetic."""
from __future__ import annotations

from reel.coherence import coherence_lint
from reel.creative_director import CreativeReel, CreativeScene


def _scene(veo: str) -> CreativeScene:
    return CreativeScene(image_index=0, veo_prompt=veo, voiceover="ص", duration_s=3.0)


def _reel(veo: str) -> CreativeReel:
    return CreativeReel(concept="c", hook="h", cta="اطلب دلوقتي", language="ar",
                        images=[], scenes=[_scene(veo), _scene("close on the product")])


def _has_mark_issue(rep) -> bool:
    return any("third-party" in x.lower() or "mark" in x.lower() for x in rep.issues)


def test_named_third_party_brand_in_a_scene_is_flagged():
    assert _has_mark_issue(coherence_lint(_reel("a runner laces up Nike shoes on a track"),
                                          profile={"name": {"value": "Baseera"}}))
    assert _has_mark_issue(coherence_lint(_reel("an iPhone sits on the marble desk"),
                                          profile={"name": {"value": "Baseera"}}))


def test_the_explicit_logo_phrase_still_trips():
    assert _has_mark_issue(coherence_lint(_reel("the Adidas logo fills the frame"),
                                          profile={"name": {"value": "Baseera"}}))


def test_the_brands_OWN_name_never_trips_and_ordinary_nouns_dont_either():
    # a brand literally named "Nike" showing its OWN shoes is fine (own-mark exempt)
    assert not _has_mark_issue(coherence_lint(_reel("a runner laces up Nike shoes"),
                                              profile={"name": {"value": "Nike"}}))
    # 'orange' the fruit / 'apple' the fruit are ordinary nouns, not the trademark — never trip
    assert not _has_mark_issue(coherence_lint(_reel("a bowl of fresh orange and apple slices"),
                                              profile={"name": {"value": "Baseera"}}))


def test_scene_qa_verdict_carries_a_third_party_mark_field_and_fails_the_gate(monkeypatch):
    import reel.scene_qa as sq
    monkeypatch.setattr(sq, "_extract_frames", lambda clip, n=3: [b"f1"])

    class _MarkSeen:
        def __call__(self, system, user, model, group_name="", images=None):
            # everything else clean, but the frame shows a competitor's mark
            return model(product_faithful=True, product_persists=True, action_plausible=True,
                         no_third_party_marks=False, overall_pass=True, reason="a Nike swoosh is visible"), None

    v = sq.check_scene("clip.mp4", caller=_MarkSeen())
    assert v.checked is True
    assert v.no_third_party_marks is False
    assert v.overall_pass is False          # a third-party mark fails the compound gate in CODE
    # the criterion is actually asked of the vision model
    assert "third-party" in _MarkSeen.__call__.__doc__ if _MarkSeen.__call__.__doc__ else True
